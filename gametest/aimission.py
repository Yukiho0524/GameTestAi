"""AI 指令模式（白話測試）：把自然語言命令交給 Claude Code headless，
讓它用 aidrive 工具「截圖→判讀→操作」自主完成任務並留下截圖紀錄。

任務可存成 missions/<名稱>.yaml 重複使用（= 使用者的「測試 subagent」），
內含：命令、記錄事項(checks)、次數(repeat)、解析度(resolutions)。
套件執行會對 解析度×次數 逐輪跑，並把每輪的 RECORD 記錄與結果
彙整成 results/ai_report_<ts>/report.md 文件。

CLI：
  py -m gametest.aimission "進入遊戲，開啟商城買一次雞精"       # 臨時單跑
  py -m gametest.aimission --save 購買雞精 "命令..." --check "記錄購買前後金幣數量" \
      --res 1920x1080,1280x720 --repeat 2                       # 存成任務
  py -m gametest.aimission --mission 購買雞精                    # 跑已存任務（套件）
  py -m gametest.aimission --list                                # 列出任務
"""
from __future__ import annotations

import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

from .config import Config, load_config

# 任務只需要跑 aidrive 駕駛指令 + 看截圖——Bash 收窄到只允許 aidrive
# （安全：不給 headless 子代理無限制 shell）
MISSION_TOOLS = "Read,Bash(py -m gametest.aidrive:*)"


# ---------------------------------------------------------------- 任務檔
def missions_dir(cfg: Config) -> Path:
    d = cfg.root / "missions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_mission(cfg: Config, name: str, command: str,
                 checks: list[str] | None = None,
                 repeat: int = 1,
                 resolutions: list[str] | None = None) -> Path:
    """把一次白話測試需求存成可重用任務檔。回傳路徑。"""
    safe = re.sub(r'[\\/:*?"<>|\s]+', "_", name.strip()) or "mission"
    p = missions_dir(cfg) / f"{safe}.yaml"
    data = {"name": name.strip(), "command": command.strip(),
            "checks": [c for c in (checks or []) if c.strip()],
            "repeat": int(repeat),
            "resolutions": resolutions or ["1920x1080"]}
    p.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                 encoding="utf-8")
    return p


def load_mission(cfg: Config, name_or_path: str) -> dict:
    p = Path(name_or_path)
    if not p.exists():
        p = missions_dir(cfg) / f"{name_or_path}.yaml"
    if not p.exists():
        raise FileNotFoundError(f"找不到任務檔：{name_or_path}")
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    data.setdefault("name", p.stem)
    data.setdefault("checks", [])
    data.setdefault("repeat", 1)
    data.setdefault("resolutions", ["1920x1080"])
    return data


def list_missions(cfg: Config) -> list[str]:
    return [p.stem for p in sorted(missions_dir(cfg).glob("*.yaml"))]


# ---------------------------------------------------------------- prompt
def build_prompt(command: str, checks: list[str] | None = None,
                 resolution: str | None = None) -> str:
    res = resolution or "1920x1080"
    checks = [c for c in (checks or []) if c.strip()]
    checks_text = ""
    if checks:
        lines = "\n".join(f"   - {c}" for c in checks)
        checks_text = f"""
6. 【記錄事項】過程中順道確認並記下下列資訊（從截圖判讀數值，必要時多截幾張）：
{lines}
   最後輸出時，每一項用一行「RECORD: <名稱>=<值>」回報（放在 MISSION DONE 之前）；
   若某項確認結果不符預期，用「RECORD: <名稱>=異常(<說明>)」回報。"""

    return f"""你是遊戲自動化測試 AI，在雷電模擬器上用「截圖→判讀→操作」循環完成指定命令，
並把過程截圖留在任務資料夾（boot 時會印出路徑）。

工具（在 repo 根目錄以 Bash 執行；只允許以下 aidrive 指令，勿加任何前綴/串接其他命令）：
  py -m gametest.aidrive boot {res}    開雷電+啟動受測遊戲（務必用這個解析度），印任務資料夾
  py -m gametest.aidrive shot <名稱>   截圖存檔並印路徑 → 立刻用 Read 看圖判讀
  py -m gametest.aidrive tap <nx> <ny>          點正規化座標（左上0,0 右下1,1）
  py -m gametest.aidrive long <nx> <ny> [ms]    長壓
  py -m gametest.aidrive swipe <x1> <y1> <x2> <y2> [ms]
  py -m gametest.aidrive back / text <字串>

務必遵守：
1. boot 後等標題畫面（多 shot 幾次）→ 點「進入遊戲」。點擊可能被載入吞掉：
   點完 shot 確認已離開標題，沒離開就再點（短/長交替），進入後等載入（可能 30~60s）。
2. 每一步都「先 shot、Read 判讀、再操作」，禁止盲點。座標從截圖自行估正規化值。
3. 遇到非預期彈窗（問卷/公告/更新/劇情對話）→ 找 X / SKIP / 確認 關掉再繼續。
4. 達成命令後 `shot final_state` 留最終畫面，最後印出一行
   「MISSION DONE: <一句話結果>」+ 任務資料夾路徑。
5. 判斷無法達成（重試多次仍卡住）→ 印「MISSION FAILED: <原因>」，同樣留最終截圖。{checks_text}

命令：{command}
"""


# ---------------------------------------------------------------- 執行
def run_mission(cfg: Config, command: str, checks: list[str] | None = None,
                resolution: str | None = None,
                timeout: int = 1800) -> tuple[bool, str]:
    """執行單次 AI 任務。回傳 (是否成功, claude 輸出全文)。"""
    from .autogen import find_claude
    exe = find_claude()
    if not exe:
        return False, "找不到 claude CLI（未安裝或不在 PATH）"
    cmd = [exe, "-p", build_prompt(command, checks, resolution),
           "--allowedTools", MISSION_TOOLS]
    try:
        proc = subprocess.run(cmd, cwd=str(cfg.root), timeout=timeout,
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return False, f"AI 任務逾時（>{timeout}s）"
    out = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
    return "MISSION DONE" in out, out


def _parse_records(out: str) -> list[str]:
    return [m.group(1).strip()
            for m in re.finditer(r"RECORD:\s*(.+)", out)]


def _newest_mission_dir(cfg: Config, after_ts: float) -> str:
    dirs = [d for d in cfg.results_dir.glob("ai_mission_*")
            if d.is_dir() and d.stat().st_mtime >= after_ts - 5]
    return str(max(dirs, key=lambda d: d.stat().st_mtime)) if dirs else ""


def run_mission_suite(cfg: Config, mission: dict,
                      on_progress=None) -> tuple[Path, bool]:
    """跑任務套件：解析度 × 次數 逐輪執行，彙整 report.md。

    回傳 (report.md 路徑, 是否全部達成)。on_progress(文字) 供 GUI 顯示進度。
    """
    from .ldplayer import LDConsole
    name = mission.get("name", "mission")
    command = mission["command"]
    checks = mission.get("checks", [])
    repeat = int(mission.get("repeat", 1))
    resolutions = mission.get("resolutions", ["1920x1080"])

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    rdir = cfg.results_dir / f"ai_report_{stamp}"
    rdir.mkdir(parents=True, exist_ok=True)
    lines = [f"# AI 白話測試報告：{name}", "",
             f"- 命令：{command}",
             f"- 記錄事項：{'；'.join(checks) if checks else '（無）'}",
             f"- 解析度：{', '.join(resolutions)}；每解析度 {repeat} 次",
             f"- 時間：{stamp}", "", "---", ""]
    all_ok = True
    console = LDConsole(cfg)

    for res in resolutions:
        for n in range(1, repeat + 1):
            tag = f"{res} 第 {n}/{repeat} 次"
            if on_progress:
                on_progress(f"AI 任務 {tag} 執行中 ...")
            # 每輪從乾淨冷開機開始（headless AI 會自己 boot 指定解析度）
            try:
                console.quit(cfg.instance_index)
                time.sleep(3)
            except Exception:
                pass
            t0 = time.time()
            ok, out = run_mission(cfg, command, checks, res)
            recs = _parse_records(out)
            mdir = _newest_mission_dir(cfg, t0)
            tail = [l for l in out.strip().splitlines()
                    if "MISSION DONE" in l or "MISSION FAILED" in l]
            all_ok = all_ok and ok
            lines += [f"## {tag} — {'✅ 達成' if ok else '❌ 未達成'}", ""]
            if recs:
                lines += ["| 記錄 | 值 |", "|---|---|"]
                for r in recs:
                    k, _, v = r.partition("=")
                    lines.append(f"| {k.strip()} | {v.strip() or r} |")
                lines.append("")
            if tail:
                lines += [f"- 結果：{tail[-1].strip()}"]
            if mdir:
                lines += [f"- 截圖資料夾：`{mdir}`"]
            lines += [""]
            # 每輪的 claude 原始輸出留檔備查
            (rdir / f"run_{res}_{n:02d}.log").write_text(out, encoding="utf-8")

    report = rdir / "report.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    return report, all_ok


# ---------------------------------------------------------------- CLI
def main() -> None:
    cfg = load_config()
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    if args[0] == "--list":
        for m in list_missions(cfg):
            print(m)
        return
    if args[0] == "--save":
        name = args[1]
        command = args[2]
        checks, res, repeat = [], ["1920x1080"], 1
        i = 3
        while i < len(args):
            if args[i] == "--check":
                checks.append(args[i + 1]); i += 2
            elif args[i] == "--res":
                res = args[i + 1].split(","); i += 2
            elif args[i] == "--repeat":
                repeat = int(args[i + 1]); i += 2
            else:
                i += 1
        p = save_mission(cfg, name, command, checks, repeat, res)
        print(f"已存任務：{p}")
        return
    if args[0] == "--mission":
        mission = load_mission(cfg, args[1])
        report, ok = run_mission_suite(cfg, mission, on_progress=print)
        print(f"報告：{report}")
        print("=== 結果：", "全部達成" if ok else "有未達成")
        return
    # 臨時單跑（沿用舊行為）
    ok, out = run_mission(cfg, " ".join(args))
    print(out)
    print("=== 結果：", "達成" if ok else "未達成")


if __name__ == "__main__":
    main()
