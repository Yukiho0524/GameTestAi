"""自動 review：測試跑完若總成功率低於門檻，叫 headless Claude 看診斷+失敗截圖、
修正腳本並推 git。只修一次（不自動重跑），避免無限迴圈。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from .autogen import ALLOWED_TOOLS, find_claude
from .config import Config


def _prompt(script_name: str, rate: float, result_dir: Path) -> str:
    return (
        f"測試腳本 `{script_name}` 剛跑完，總成功率 {rate:.1f}% 偏低，需要你 review 並修正一次。\n"
        f"結果資料夾：`{result_dir.as_posix()}`（內有 report.json 與各解析度/各次的 before/after/截圖 png）。\n"
        "請依本專案 CLAUDE.md 規範：\n"
        f"1) 執行 `py run.py diagnose {result_dir.as_posix()}` 看失敗步驟與修正建議；\n"
        "2) 用 Read 開啟失敗步驟的截圖（result 資料夾下的 png）對照判斷真正原因；\n"
        f"3) 修正腳本 `scripts/{script_name}.yaml`：依「點擊策略」優先改用 tap_image"
        "（裁實際元件模板）或 tap_scene，調整 timeout/press/anchor，移除盲猜座標；\n"
        "4) git commit（訊息以 [Hibari] 開頭，說明修了什麼）+ push origin main。\n"
        "只修這一次，不要重新執行測試。完成後回報修了哪些步驟。"
    )


def maybe_review(cfg: Config, script_name: str, success_rate: float,
                 result_dir: Path, timeout: int = 1800) -> bool:
    """成功率低於門檻時自動 review+修正。回傳是否有觸發 review。"""
    threshold = cfg.auto_review_below
    if threshold <= 0:
        return False
    if success_rate >= threshold:
        print(f"成功率 {success_rate:.1f}% ≥ {threshold}%，不需自動 review。")
        return False

    claude = find_claude()
    if not claude:
        print(f"[自動 review] 成功率 {success_rate:.1f}% < {threshold}%，"
              "但找不到 claude CLI，略過自動修正。")
        return False

    print(f"\n[自動 review] 成功率 {success_rate:.1f}% < {threshold}%，"
          "呼叫 Claude review 並修正腳本（只修一次）...")
    cmd = [claude, "-p", _prompt(script_name, success_rate, result_dir),
           "--allowedTools", ALLOWED_TOOLS]
    try:
        proc = subprocess.run(cmd, cwd=str(cfg.root), timeout=timeout,
                              capture_output=True, text=True, errors="replace")
    except subprocess.TimeoutExpired:
        print("[自動 review] Claude 逾時。")
        return False

    combined = (proc.stdout or "") + (proc.stderr or "")
    low = combined.lower()
    if "not logged in" in low or "/login" in low:
        print("[自動 review] headless Claude 未登入，略過。請先用 CLI 跑一次 /login。")
        return False
    if proc.stdout:
        print("  --- Claude review 回覆（節錄）---")
        print("  " + "\n  ".join(proc.stdout.strip().splitlines()[-15:]))
    print("[自動 review] 完成。建議再 --once 驗證修正結果。")
    return True
