"""AI 指令模式的駕駛工具：讓 AI（headless claude / 對話中的 Claude）以
「截圖→判讀→操作」循環自主操作遊戲，完成自然語言命令並留下截圖紀錄。

用法（CLI，每次呼叫獨立 process，狀態存 results/.aidrive_state.json）：
  py -m gametest.aidrive boot [WxH]     開雷電+啟動遊戲+建立任務資料夾
  py -m gametest.aidrive shot <名稱>    截圖存任務資料夾並印路徑（AI 用 Read 看）
  py -m gametest.aidrive tap <nx> <ny>  點正規化座標
  py -m gametest.aidrive long <nx> <ny> [ms]
  py -m gametest.aidrive swipe <x1> <y1> <x2> <y2> [ms]
  py -m gametest.aidrive text <字串>
  py -m gametest.aidrive back           返回鍵
  py -m gametest.aidrive stop           結束任務（不關模擬器）
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2

from .adb import Adb, connect_instance
from .config import Resolution, load_config

_STATE = None


def _state_path(cfg) -> Path:
    return cfg.results_dir / ".aidrive_state.json"


def _save_state(cfg, data: dict) -> None:
    _state_path(cfg).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _load_state(cfg) -> dict:
    p = _state_path(cfg)
    if not p.exists():
        raise SystemExit("尚未 boot：先執行 py -m gametest.aidrive boot")
    return json.loads(p.read_text(encoding="utf-8"))


def _attach(cfg) -> tuple[Adb, dict]:
    st = _load_state(cfg)
    adb = Adb(cfg, st["serial"])
    return adb, st


def _shot(cfg, adb: Adb, st: dict, name: str) -> Path:
    img = adb.screencap()
    st["shot_no"] = st.get("shot_no", 0) + 1
    fn = f"{st['shot_no']:02d}_{name}.png"
    out = Path(st["dir"]) / fn
    cv2.imwrite(str(out), img)
    _save_state(cfg, st)
    h, w = img.shape[:2]
    print(f"SHOT {out}  ({w}x{h})")
    return out


def main() -> None:
    cfg = load_config()
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    cmd = args[0]

    if cmd == "boot":
        from .device import Device
        wh = (args[1].split("x") if len(args) > 1 else ["1920", "1080"])
        res = Resolution(int(wh[0]), int(wh[1]), 320 if int(wh[1]) >= 1080 else 240)
        dev = Device(cfg)
        dev.prepare(res)
        dev.start_app()
        mdir = cfg.results_dir / f"ai_mission_{datetime.now():%Y%m%d_%H%M%S}"
        mdir.mkdir(parents=True, exist_ok=True)
        _save_state(cfg, {"serial": dev.adb.serial, "dir": str(mdir), "shot_no": 0,
                          "size": list(dev.size)})
        print(f"READY serial={dev.adb.serial} mission_dir={mdir}")
        return

    adb, st = _attach(cfg)
    w, h = st["size"]

    if cmd == "shot":
        _shot(cfg, adb, st, args[1] if len(args) > 1 else "screen")
    elif cmd == "tap":
        nx, ny = float(args[1]), float(args[2])
        adb.tap(int(nx * w), int(ny * h))
        print(f"TAP ({nx:.3f},{ny:.3f}) -> px({int(nx*w)},{int(ny*h)})")
    elif cmd == "long":
        nx, ny = float(args[1]), float(args[2])
        ms = int(args[3]) if len(args) > 3 else 800
        adb.swipe(int(nx * w), int(ny * h), int(nx * w), int(ny * h), ms)
        print(f"LONG ({nx:.3f},{ny:.3f}) {ms}ms")
    elif cmd == "swipe":
        x1, y1, x2, y2 = (float(a) for a in args[1:5])
        ms = int(args[5]) if len(args) > 5 else 400
        adb.swipe(int(x1 * w), int(y1 * h), int(x2 * w), int(y2 * h), ms)
        print(f"SWIPE ({x1:.2f},{y1:.2f})->({x2:.2f},{y2:.2f}) {ms}ms")
    elif cmd == "text":
        adb.input_text(args[1])
        print(f"TEXT {args[1]}")
    elif cmd == "back":
        adb.shell("input", "keyevent", "4")
        print("BACK")
    elif cmd == "stop":
        print(f"STOP mission_dir={st['dir']}")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
