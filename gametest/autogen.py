"""自動生成：偵測新影片 → 抽幀 → 呼叫 Claude Code headless(`claude -p`) 自動分析、
生成腳本並推 git。讓「有新影片就自動產腳本上傳」真正無人值守。

注意：
- 看影格寫腳本是 AI 視覺分析，必須由一個 Claude Code 工作階段執行；本模組負責
  「偵測 + 抽幀 + 叫起 headless Claude」。
- headless 用 --allowedTools 收斂權限（只開 Read/Glob/Grep/Edit/Write/Bash），
  不用「略過所有權限」。見 build_cmd / ALLOWED_TOOLS。
- 無人值守推送到 main 仍可能被 auto-mode 守門擋下；需在 .claude/settings.local.json
  的 allow 加 "Bash(git push:*)" 才能免提示完成 push。
- 每支影片會花一次 Claude Code 額度。生成的是草稿，仍建議 --once + diagnose 校正。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from .config import Config
from .scriptgen import script_for_video
from .video import extract_frames

# 已知的 Claude Code CLI 位置（PATH 找不到時的後備）
_CLAUDE_CANDIDATES = [
    r"C:\Users\hibari.kuo\AppData\Roaming\Claude\claude-code\2.1.187\claude.exe",
]


def find_claude() -> str | None:
    exe = shutil.which("claude")
    if exe:
        return exe
    # 後備：掃 claude-code 安裝目錄取最新版
    base = Path(os.environ.get("APPDATA", "")) / "Claude" / "claude-code"
    if base.exists():
        vers = sorted([d for d in base.iterdir() if (d / "claude.exe").exists()],
                      key=lambda d: d.name)
        if vers:
            return str(vers[-1] / "claude.exe")
    for c in _CLAUDE_CANDIDATES:
        if Path(c).exists():
            return c
    return None


def _prompt(video_name: str, frames_dir: Path) -> str:
    return (
        f"有一支新的遊戲錄影 `{video_name}`，影格已抽到 `{frames_dir.as_posix()}`。\n"
        "請依本專案 CLAUDE.md 的「從影片影格自動生成測試腳本」規範：\n"
        "1) 用 Read 看那個資料夾的代表性影格，判讀操作流程；\n"
        "2) 產生跨解析度測試腳本（正規化座標、anchor、適配比對、不確定觸控用 press:auto、"
        "重用既有 assets 模板、需要的新模板用 cv2 裁切存 assets/）；\n"
        f"3) 用 gametest.scriptgen.save_and_push(cfg, yaml_text, video_name='{video_name}') "
        "落檔、自動命名 YYYYMMDD_NN、登記對應並 git push；若新增 assets 模板/參考圖請一併 git add 後 push。\n"
        "完成後回報腳本名稱與流程摘要。"
    )


# 無人值守允許的工具（收斂權限，不用 --dangerously-skip-permissions）
ALLOWED_TOOLS = "Read,Glob,Grep,Edit,Write,Bash"


def build_cmd(claude_exe: str, prompt: str) -> list[str]:
    # headless：-p 提示；--allowedTools 只開必要工具（讀圖、跑 py 裁圖、scriptgen 落檔+git）
    # 注意：unattended 推送到 main 仍可能被 auto-mode 守門擋下，需在
    #       .claude/settings.local.json 的 allow 加 "Bash(git push:*)" 才能免提示。
    return [claude_exe, "-p", prompt, "--allowedTools", ALLOWED_TOOLS]


def process_video(cfg: Config, video: Path, claude_exe: str,
                  every_sec: float = 2.0, timeout: int = 1800) -> bool:
    """對單支影片：抽幀 → 叫 headless Claude 生成腳本。成功回 True。"""
    print(f"\n▶ 自動處理新影片：{video.name}")
    frames = extract_frames(cfg, video, every_sec=every_sec)
    if not frames:
        print("  [略過] 抽不到影格")
        return False
    frames_dir = frames[0].parent

    prompt = _prompt(video.name, frames_dir)
    cmd = build_cmd(claude_exe, prompt)
    print(f"  呼叫 Claude Code headless 分析中（最多 {timeout}s）...")
    try:
        proc = subprocess.run(cmd, cwd=str(cfg.root), timeout=timeout,
                              capture_output=True, text=True, errors="replace")
    except subprocess.TimeoutExpired:
        print("  [錯誤] Claude headless 逾時")
        return False
    combined = (proc.stdout or "") + (proc.stderr or "")
    if proc.stdout:
        print("  --- Claude 回覆（節錄）---")
        print("  " + "\n  ".join(proc.stdout.strip().splitlines()[-15:]))

    # 偵測常見失敗：未登入
    low = combined.lower()
    if "not logged in" in low or "/login" in low or "please run" in low \
            or "invalid api key" in low or "authentication" in low:
        print("\n  [錯誤] headless Claude 未登入，無法自動生成。")
        print(f"  請先用這支 CLI 登入一次（互動執行後輸入 /login 完成瀏覽器授權）：")
        print(f"      \"{claude_exe}\"")
        print("  或設環境變數 ANTHROPIC_API_KEY（改用 API 計費）。登入後再跑一次即可。")
        return False

    # 以「對應表是否已登記」判定成功（save_and_push 會登記）
    name = script_for_video(cfg, video.name)
    if name:
        print(f"  ✅ 已生成並登記：{name}")
        return True
    print("  [警告] Claude 執行完但未偵測到已登記腳本，請檢查上面 log。")
    return False


def _pending_videos(cfg: Config) -> list[Path]:
    src = cfg.video_source_dir
    if not src.exists():
        return []
    out = []
    for p in sorted(src.iterdir()):
        # 分段 session 資料夾（含 session.json）當一個來源
        if p.is_dir() and (p / "session.json").exists():
            if not script_for_video(cfg, p.name):
                out.append(p)
        elif p.is_file() and p.suffix.lower() in cfg.watch_extensions:
            if not script_for_video(cfg, p.name):
                out.append(p)
    return out


def scan_once(cfg: Config, claude_exe: str | None = None) -> int:
    cfg.ensure_dirs()
    claude_exe = claude_exe or find_claude()
    if not claude_exe:
        print("[錯誤] 找不到 claude CLI，無法自動生成。請確認 Claude Code 已安裝。")
        return 0
    pending = _pending_videos(cfg)
    if not pending:
        print("沒有待處理的新影片。")
        return 0
    done = 0
    for v in pending:
        if process_video(cfg, v, claude_exe):
            done += 1
    return done


def watch_loop(cfg: Config) -> None:
    claude_exe = find_claude()
    if not claude_exe:
        print("[錯誤] 找不到 claude CLI。")
        return
    print(f"自動生成監看啟動：{cfg.video_source_dir}")
    print(f"使用 Claude：{claude_exe}")
    print(f"每 {cfg.watch_poll_interval}s 掃描一次，Ctrl+C 結束。")
    try:
        while True:
            scan_once(cfg, claude_exe)
            time.sleep(cfg.watch_poll_interval)
    except KeyboardInterrupt:
        print("\n已停止自動生成監看。")


def run(cfg: Config, watch: bool = False) -> None:
    if watch:
        watch_loop(cfg)
    else:
        scan_once(cfg)
