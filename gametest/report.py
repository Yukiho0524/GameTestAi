"""報告產生：JSON + 自包含 HTML（含成功率與截圖縮圖）。"""
from __future__ import annotations

import html
import json
from dataclasses import asdict
from pathlib import Path

from .runner import Suite


def _rel(path: str, base: Path) -> str:
    try:
        return Path(path).relative_to(base).as_posix()
    except ValueError:
        return Path(path).as_posix()


def write_reports(suite: Suite, root: Path) -> tuple[Path, Path]:
    json_path = root / "report.json"
    json_path.write_text(
        json.dumps(asdict(suite), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    html_path = root / "report.html"
    html_path.write_text(_render_html(suite, root), encoding="utf-8")
    return json_path, html_path


def _badge(passed: bool) -> str:
    color = "#1a7f37" if passed else "#cf222e"
    text = "PASS" if passed else "FAIL"
    return f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:4px;font-size:12px">{text}</span>'


def _render_html(suite: Suite, root: Path) -> str:
    overall = suite.success_rate()
    # 各解析度摘要
    rows = []
    for res in suite.resolutions:
        rate = suite.success_rate(res)
        runs = [r for r in suite.runs if r.resolution == res]
        passed = sum(1 for r in runs if r.passed)
        bar = (f'<div style="background:#eee;border-radius:4px;overflow:hidden;height:18px">'
               f'<div style="width:{rate:.0f}%;background:#1a7f37;height:18px"></div></div>')
        rows.append(
            f"<tr><td>{html.escape(res)}</td><td>{passed}/{len(runs)}</td>"
            f"<td>{rate:.1f}%</td><td style='width:200px'>{bar}</td></tr>"
        )
    summary_table = "\n".join(rows)

    # 每輪明細
    detail_blocks = []
    for r in suite.runs:
        step_rows = []
        for s in r.steps:
            shot = ""
            if s.screenshot:
                img_rel = _rel(str(Path(r.screenshot_dir) / s.screenshot), root)
                shot = (f'<a href="{img_rel}" target="_blank">'
                        f'<img src="{img_rel}" style="height:90px;border:1px solid #ddd"></a>')
            score = f"{s.score:.3f}" if s.score is not None else "—"
            step_rows.append(
                f"<tr><td>{s.index}</td><td>{html.escape(s.name)}</td>"
                f"<td>{html.escape(s.action)}</td><td>{_badge(s.ok)}</td>"
                f"<td>{score}</td><td>{html.escape(s.message)}</td><td>{shot}</td></tr>"
            )
        err = (f'<pre style="color:#cf222e;white-space:pre-wrap">{html.escape(r.error)}</pre>'
               if r.error else "")
        detail_blocks.append(f"""
        <details style="margin:8px 0;border:1px solid #ddd;border-radius:6px;padding:8px">
          <summary><b>{html.escape(r.resolution)} — 第 {r.run_index} 次</b> {_badge(r.passed)}
            <span style="color:#666">({r.duration_sec}s)</span></summary>
          {err}
          <table>
            <tr><th>#</th><th>步驟</th><th>動作</th><th>結果</th><th>分數</th><th>訊息</th><th>截圖</th></tr>
            {''.join(step_rows)}
          </table>
        </details>""")

    return f"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<title>測試報告 - {html.escape(suite.script_name)}</title>
<style>
  body{{font-family:'Segoe UI','Microsoft JhengHei',sans-serif;margin:24px;color:#24292f}}
  table{{border-collapse:collapse;width:100%;margin:8px 0}}
  th,td{{border:1px solid #d0d7de;padding:6px 10px;text-align:left;font-size:14px;vertical-align:top}}
  th{{background:#f6f8fa}}
  h1{{margin-bottom:4px}}
  .big{{font-size:42px;font-weight:700;color:{'#1a7f37' if overall>=80 else '#9a6700' if overall>=50 else '#cf222e'}}}
</style></head><body>
<h1>📊 自動化測試報告</h1>
<p>腳本：<b>{html.escape(suite.script_name)}</b> ｜ 時間：{html.escape(suite.started_at)}
   ｜ 每解析度重複 {suite.repeat} 次</p>
<div class="big">總成功率 {overall:.1f}%</div>
<h2>各解析度成功率</h2>
<table>
  <tr><th>解析度</th><th>通過/總數</th><th>成功率</th><th>圖示</th></tr>
  {summary_table}
</table>
<h2>逐輪明細</h2>
{''.join(detail_blocks)}
</body></html>"""
