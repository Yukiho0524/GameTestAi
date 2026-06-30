#!/usr/bin/env python
"""雷電模擬器手遊自動化測試 CLI。

用法：
  py run.py devices                          # 列出雷電實例與 adb 裝置
  py run.py watch [--watch] [--force]        # 監看影片來源資料夾，新影片自動抽幀
  py run.py extract <影片> [--every 1.0]      # 手動對單一影片抽幀
  py run.py capture <輸出.png>                # 截一張目前畫面（製作模板圖用）
  py run.py test <腳本.yaml> [--repeat N]     # 執行測試並輸出報告
"""
from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

# Windows 主控台預設非 UTF-8，會讓中文/emoji 輸出崩潰；強制切到 UTF-8。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

from gametest.config import load_config


def cmd_devices(args):
    cfg = load_config(args.config)
    from gametest.ldplayer import LDConsole
    from gametest.adb import _list_devices

    print("== 雷電實例 (ldconsole list2) ==")
    for inst in LDConsole(cfg).list_instances():
        state = "執行中" if inst.android_started else "已停止"
        print(f"  [{inst.index}] {inst.title}  ({state}, pid={inst.pid})")

    print("\n== adb 裝置 ==")
    devs = _list_devices(cfg.adb_path)
    print("  " + ("\n  ".join(devs) if devs else "（無，請先啟動實例）"))


def cmd_extract(args):
    cfg = load_config(args.config)
    cfg.ensure_dirs()
    from gametest.video import extract_frames
    frames = extract_frames(cfg, args.video, every_sec=args.every, max_frames=args.max)
    print("\n下一步：把這些圖交給 Claude 分析，產生 scripts/*.yaml 測試腳本。")
    if frames:
        print(f"圖片資料夾：{frames[0].parent}")


def cmd_watch(args):
    cfg = load_config(args.config)
    from gametest.watcher import run as watch_run
    print(f"影片來源：{cfg.video_source_dir}")
    watch_run(cfg, watch=args.watch, force=args.force)


def cmd_capture(args):
    cfg = load_config(args.config)
    from gametest.device import Device
    import cv2

    dev = Device(cfg)
    from gametest.adb import connect_instance
    dev.adb = connect_instance(cfg, cfg.instance_index)
    dev._size = dev.adb.screen_size()
    img = dev.screencap()
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), img)
    print(f"已存截圖：{out}（尺寸 {dev.size[0]}x{dev.size[1]}）")


def cmd_test(args):
    cfg = load_config(args.config)
    if args.repeat:
        cfg.repeat = args.repeat
    from gametest.script_model import TestScript
    from gametest.runner import run_suite
    from gametest.report import write_reports

    script = TestScript.load(args.script)
    print(f"載入腳本：{script.name} — {script.description}")
    print(f"解析度：{[r.label for r in cfg.resolutions]} ｜ 每組重複 {cfg.repeat} 次")

    suite, root = run_suite(cfg, script)
    json_path, html_path = write_reports(suite, root)

    print(f"\n總成功率：{suite.success_rate():.1f}%")
    for res in suite.resolutions:
        print(f"  {res}: {suite.success_rate(res):.1f}%")
    print(f"\nJSON 報告：{json_path}")
    print(f"HTML 報告：{html_path}")
    if not args.no_open:
        webbrowser.open(html_path.as_uri())


def main(argv=None):
    p = argparse.ArgumentParser(description="雷電模擬器手遊自動化測試系統")
    p.add_argument("--config", help="設定檔路徑（預設 config/settings.yaml）")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("devices", help="列出實例與裝置")
    sp.set_defaults(func=cmd_devices)

    sp = sub.add_parser("extract", help="影片抽幀")
    sp.add_argument("video")
    sp.add_argument("--every", type=float, default=1.0, help="每幾秒抽一張")
    sp.add_argument("--max", type=int, default=0, help="最多抽幾張 (0=不限)")
    sp.set_defaults(func=cmd_extract)

    sp = sub.add_parser("watch", help="監看影片來源資料夾，對新影片自動抽幀")
    sp.add_argument("--watch", action="store_true", help="常駐監看（預設只掃一次）")
    sp.add_argument("--force", action="store_true", help="即使已抽過幀也重抽")
    sp.set_defaults(func=cmd_watch)

    sp = sub.add_parser("capture", help="截目前畫面")
    sp.add_argument("output")
    sp.set_defaults(func=cmd_capture)

    sp = sub.add_parser("test", help="執行測試")
    sp.add_argument("script")
    sp.add_argument("--repeat", type=int, help="覆寫重複次數")
    sp.add_argument("--no-open", action="store_true", help="不要自動開啟報告")
    sp.set_defaults(func=cmd_test)

    args = p.parse_args(argv)
    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\n已中斷。")
        return 130
    except Exception as e:  # noqa: BLE001
        print(f"\n[錯誤] {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
