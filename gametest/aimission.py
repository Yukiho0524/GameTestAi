"""AI 指令模式：把自然語言命令交給 Claude Code headless，
讓它用 aidrive 工具「截圖→判讀→操作」自主完成任務並留下截圖紀錄。

GUI 的「🤖 AI 指令」輸入框走這裡；也可 CLI：
  py -m gametest.aimission "進入遊戲，開啟商城買一次雞精"
"""
from __future__ import annotations

import subprocess
import sys

from .config import Config, load_config

# 任務只需要跑 aidrive 指令 + 看截圖
MISSION_TOOLS = "Bash,Read"


def build_prompt(command: str) -> str:
    return f"""你是遊戲自動化測試 AI，在雷電模擬器上用「截圖→判讀→操作」循環完成指定命令，
並把過程截圖留在任務資料夾（boot 時會印出路徑）。

工具（在 repo 根目錄以 Bash 執行，前面加 PYTHONIOENCODING=utf-8）：
  py -m gametest.aidrive boot [WxH]    開雷電+啟動受測遊戲（預設 1920x1080），印任務資料夾
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
5. 判斷無法達成（重試多次仍卡住）→ 印「MISSION FAILED: <原因>」，同樣留最終截圖。

命令：{command}
"""


def run_mission(cfg: Config, command: str, timeout: int = 1800) -> tuple[bool, str]:
    """執行 AI 任務。回傳 (是否成功, claude 輸出全文)。"""
    from .autogen import find_claude
    exe = find_claude()
    if not exe:
        return False, "找不到 claude CLI（未安裝或不在 PATH）"
    cmd = [exe, "-p", build_prompt(command), "--allowedTools", MISSION_TOOLS]
    try:
        proc = subprocess.run(cmd, cwd=str(cfg.root), timeout=timeout,
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return False, f"AI 任務逾時（>{timeout}s）"
    out = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
    return "MISSION DONE" in out, out


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return
    ok, out = run_mission(load_config(), " ".join(sys.argv[1:]))
    print(out)
    print("=== 結果：", "達成" if ok else "未達成")


if __name__ == "__main__":
    main()
