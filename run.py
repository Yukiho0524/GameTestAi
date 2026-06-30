"""雷電模擬器手遊自動化測試 CLI。

用法：
  py run.py gui                              # 開啟圖形控制台（選解析度/包體名/執行）
  py run.py devices                          # 列出雷電實例與 adb 裝置
  py run.py presets                          # 列出解析度預設（直版/橫版）
  py run.py apps [--filter 關鍵字]            # 列出模擬器已安裝套件
  py run.py verify-app [包體名]               # 驗證可用 ADB 開啟 App
  py run.py watch [--watch] [--force]        # 監看影片來源資料夾，新影片自動抽幀
  py run.py autogen [--watch]                # 新影片自動呼叫 Claude 生成腳本並推 git
  py run.py extract <影片> [--every 1.0]      # 手動對單一影片抽幀
  py run.py capture <輸出.png>                # 截一張目前畫面（製作模板圖用）
  py run.py test <腳本.yaml> [--repeat N]     # 執行測試並輸出報告
  py run.py test <腳本.yaml> --once           # 快速模式：單解析度單次（修腳本用）
  py run.py diagnose [結果資料夾]              # 列出失敗步驟與修正建議
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


def cmd_gui(args):
    from gametest.gui import launch
    launch(load_config(args.config))


def cmd_presets(args):
    from gametest import resolutions as R
    print("== 橫版 Landscape ==")
    for p in R.LANDSCAPE:
        print(f"  {p.key:14s} {p.label}  (dpi={p.dpi})")
    print("\n== 直版 Portrait ==")
    for p in R.PORTRAIT:
        print(f"  {p.key:14s} {p.label}  (dpi={p.dpi})")
    print("\n在 settings.yaml 用 test.resolution_presets: [key, ...] 引用。")


def _connect_adb(cfg):
    from gametest.adb import connect_instance
    from gametest.ldplayer import LDConsole
    console = LDConsole(cfg)
    if not console.is_running(cfg.instance_index):
        print(f"實例 {cfg.instance_index} 未執行，正在啟動 ...")
        console.launch(cfg.instance_index)
    adb = connect_instance(cfg, cfg.instance_index)
    adb.wait_boot(cfg.boot_timeout)
    return adb


def cmd_apps(args):
    cfg = load_config(args.config)
    from gametest.appcheck import list_packages
    adb = _connect_adb(cfg)
    pkgs = list_packages(adb, args.filter or "")
    print(f"已安裝套件（{len(pkgs)}）：")
    for p in pkgs:
        print(f"  {p}")


def cmd_verify_app(args):
    cfg = load_config(args.config)
    pkg = args.package or cfg.package_name
    if not pkg:
        print("請提供包體名：py run.py verify-app <package>，或在 settings.yaml 設定 package_name")
        return
    from gametest.appcheck import launch_and_verify
    adb = _connect_adb(cfg)
    print(f"驗證 App：{pkg}")
    r = launch_and_verify(adb, pkg)
    print(f"  已安裝：{'是' if r.installed else '否'}")
    print(f"  已啟動：{'是' if r.launched else '否'}")
    print(f"  進入前景：{'是' if r.foreground else '否'}")
    print(f"  結果：{r.message}")
    print("✅ 可用 ADB 開啟此 App" if r.ok else "❌ 無法確認可正常開啟，請檢查包體名")


def cmd_watch(args):
    cfg = load_config(args.config)
    from gametest.watcher import run as watch_run
    print(f"影片來源：{cfg.video_source_dir}")
    watch_run(cfg, watch=args.watch, force=args.force)


def cmd_autogen(args):
    cfg = load_config(args.config)
    from gametest.autogen import run as autogen_run
    print(f"影片來源：{cfg.video_source_dir}")
    autogen_run(cfg, watch=args.watch)


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


def cmd_diagnose(args):
    cfg = load_config(args.config)
    from gametest.diagnose import diagnose
    rd = Path(args.result_dir) if args.result_dir else None
    diagnose(cfg, rd)


def cmd_test(args):
    cfg = load_config(args.config)
    if args.repeat:
        cfg.repeat = args.repeat
    if args.once:
        cfg.repeat = 1
        cfg.resolutions = cfg.resolutions[:1]
        print("[--once] 快速模式：單解析度單次")
    from gametest.script_model import TestScript
    from gametest.runner import run_suite
    from gametest.report import write_reports
    from gametest.report_excel import write_excel

    script = TestScript.load(args.script)
    print(f"載入腳本：{script.name} — {script.description}")
    print(f"解析度：{[r.label for r in cfg.resolutions]} ｜ 每組重複 {cfg.repeat} 次")

    suite, root = run_suite(cfg, script)
    json_path, html_path = write_reports(suite, root)
    xlsx_path = write_excel(suite, root)

    print(f"\n總成功率：{suite.success_rate():.1f}%")
    for res in suite.resolutions:
        runs = [x for x in suite.runs if x.resolution == res]
        bugs = sum(x.bug_count for x in runs)
        print(f"  {res}: {suite.success_rate(res):.1f}%（BUG 步驟 {bugs}）")
    print(f"\nExcel 報告：{xlsx_path}")
    print(f"HTML 報告：{html_path}")
    print(f"JSON 報告：{json_path}")
    if not args.no_open:
        webbrowser.open(xlsx_path.as_uri())

    # 成功率過低 → 自動 review + 修正腳本（只修一次）
    if not args.no_review:
        from gametest.autoreview import maybe_review
        maybe_review(cfg, script.name, suite.success_rate(), root)


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

    sp = sub.add_parser("gui", help="開啟圖形控制台（選解析度/輸入包體名/執行測試）")
    sp.set_defaults(func=cmd_gui)

    sp = sub.add_parser("presets", help="列出解析度預設（直版/橫版）")
    sp.set_defaults(func=cmd_presets)

    sp = sub.add_parser("apps", help="列出模擬器已安裝套件")
    sp.add_argument("--filter", help="關鍵字過濾")
    sp.set_defaults(func=cmd_apps)

    sp = sub.add_parser("verify-app", help="驗證可用 ADB 開啟指定 App")
    sp.add_argument("package", nargs="?", help="包體名（省略則用 settings.yaml）")
    sp.set_defaults(func=cmd_verify_app)

    sp = sub.add_parser("watch", help="監看影片來源資料夾，對新影片自動抽幀")
    sp.add_argument("--watch", action="store_true", help="常駐監看（預設只掃一次）")
    sp.add_argument("--force", action="store_true", help="即使已抽過幀也重抽")
    sp.set_defaults(func=cmd_watch)

    sp = sub.add_parser("autogen", help="偵測新影片→抽幀→呼叫 Claude 自動生成腳本並推 git")
    sp.add_argument("--watch", action="store_true", help="常駐監看（預設只掃一次）")
    sp.set_defaults(func=cmd_autogen)

    sp = sub.add_parser("capture", help="截目前畫面")
    sp.add_argument("output")
    sp.set_defaults(func=cmd_capture)

    sp = sub.add_parser("test", help="執行測試")
    sp.add_argument("script")
    sp.add_argument("--repeat", type=int, help="覆寫重複次數")
    sp.add_argument("--once", action="store_true", help="快速模式：單解析度單次（修腳本時用）")
    sp.add_argument("--no-open", action="store_true", help="不要自動開啟報告")
    sp.add_argument("--no-review", action="store_true", help="關閉成功率過低時的自動 review")
    sp.set_defaults(func=cmd_test)

    sp = sub.add_parser("diagnose", help="診斷最近一次測試，列出失敗步驟與修正建議")
    sp.add_argument("result_dir", nargs="?", help="結果資料夾（省略則用最新）")
    sp.set_defaults(func=cmd_diagnose)

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
