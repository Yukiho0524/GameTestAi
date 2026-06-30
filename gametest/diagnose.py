"""診斷工具：讀測試結果，列出失敗/可疑步驟與修正建議，當作腳本修正清單。

對應使用者痛點：影片看不出短點/長壓，腳本可能跑不出預期 → 這裡彙整證據與建議，
方便人或 AI 快速檢視並修正腳本（例如把某步改成 long_press 或調 timeout/座標）。
"""
from __future__ import annotations

import json
from pathlib import Path

from .config import Config


def latest_result_dir(cfg: Config) -> Path | None:
    dirs = [d for d in cfg.results_dir.glob("*") if (d / "report.json").exists()]
    if not dirs:
        return None
    return max(dirs, key=lambda d: d.stat().st_mtime)


def _suggest(step: dict) -> str:
    """依步驟結果給修正建議。"""
    action = step.get("action", "")
    reason = step.get("bug_reason", "") or ""
    msg = step.get("message", "") or ""

    if step.get("escalated"):
        return ("此步短點無反應、自動長壓才成功 → 建議把腳本改成 "
                "`long_press` / `long_press_image`，或設 `press: long`，避免每次重試。")
    if "無變化" in reason or "無反應" in reason:
        if action in ("tap", "tap_image"):
            return "點下去畫面沒變 → 可能其實是長壓：把該步改 `press: auto` 或 `press: long` 再試。"
        return "操作後畫面沒變 → 檢查座標/目標是否正確。"
    if "找不到圖片" in msg:
        return ("模板圖比對失敗 → 重截更清楚的 template（避免漸層/動畫）、加 `region` 限定範圍，"
                "或調低 matching.threshold。")
    if "差異過大" in reason or "與預期不符" in reason:
        return ("畫面與原影片差異大 → 可能是解析度適配問題（圖歪/掉圖），"
                "或 reference 影格抓得不準，需人工確認是真 BUG 還是參考圖要換。")
    if "黑" in reason or "純色" in reason or "掉圖" in reason or "占位" in reason:
        return "疑似掉圖/載入失敗 → 確認該解析度資源有正確載入，可能是真適配 BUG。"
    if "解析度" in reason or "黑邊" in reason:
        return "解析度/黑邊異常 → 確認模擬器解析度有正確套用、遊戲是否支援此比例。"
    if "逾時" in msg:
        return "等待逾時 → 加大該步 timeout，或確認前一步是否真的成功。"
    return "需人工檢視 before/after/原影片截圖判斷。"


def diagnose(cfg: Config, result_dir: Path | None = None) -> dict:
    result_dir = result_dir or latest_result_dir(cfg)
    if not result_dir:
        print("找不到任何測試結果（results/ 下沒有 report.json）。")
        return {}
    data = json.loads((result_dir / "report.json").read_text(encoding="utf-8"))

    print(f"診斷結果：{result_dir}")
    print(f"腳本：{data.get('script_name')}　時間：{data.get('started_at')}\n")

    findings = []
    for run in data.get("runs", []):
        res = run["resolution"]
        rn = run["run_index"]
        if run.get("crashes"):
            print(f"[崩潰] {res} 第{rn}次：{run['crashes'][:2]}")
            findings.append({"resolution": res, "run": rn, "type": "crash"})
        for s in run.get("steps", []):
            if s.get("bug") or not s.get("ok") or s.get("escalated"):
                tag = "BUG" if s.get("bug") else ("自動長壓" if s.get("escalated") else "失敗")
                detail = s.get("bug_reason") or s.get("message") or ""
                print(f"[{tag}] {res} 第{rn}次 步驟#{s['index']} {s['name']}（{s['action']}）")
                if detail:
                    print(f"       現象：{detail}")
                print(f"       建議：{_suggest(s)}")
                findings.append({"resolution": res, "run": rn, "step": s["index"],
                                 "name": s["name"], "tag": tag, "detail": detail})

    # 彙整：哪些步驟「跨多次/多解析度」反覆出問題（最該優先修）
    from collections import Counter
    key = Counter((f.get("name"), f.get("tag")) for f in findings if "step" in f or "name" in f)
    repeated = [(k, c) for k, c in key.items() if c >= 2]
    if repeated:
        print("\n=== 反覆出問題的步驟（優先修）===")
        for (name, tag), c in sorted(repeated, key=lambda x: -x[1]):
            print(f"  {name}（{tag}）出現 {c} 次")

    if not findings:
        print("沒有發現失敗或可疑步驟 ✅")
    return {"result_dir": str(result_dir), "findings": findings}
