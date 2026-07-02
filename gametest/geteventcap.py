"""getevent 觸控擷取：錄影同時讀 /dev/input/event2 的原始觸控事件，
解析成精確的點擊（座標/時間/時長/滑動），供生成腳本時精準裁出被點圖案。

雷電的真實點擊會經過 evdev（已驗證），座標即螢幕像素（X:0~max, Y:0~max）。
比影像偵測觸控標記可靠得多（雷電不會把 show_touches 疊層畫進畫面）。
"""
from __future__ import annotations

import math
import re
import subprocess
from dataclasses import dataclass

from .adb import Adb

_TOUCH_DEV = "/dev/input/event2"
_LINE = re.compile(r"\[\s*([\d.]+)\]\s+(\w+)\s+(\w+)\s+(\S+)")


@dataclass
class Touch:
    t_down: float          # 影片相對秒（已減去 t0）
    t_up: float
    x: int                 # 按下位置（像素）
    y: int
    end_x: int             # 放開位置（滑動用）
    end_y: int
    max_x: int
    max_y: int

    @property
    def duration_ms(self) -> int:
        return int((self.t_up - self.t_down) * 1000)

    @property
    def nx(self) -> float:
        return self.x / self.max_x

    @property
    def ny(self) -> float:
        return self.y / self.max_y

    @property
    def displacement(self) -> float:
        return math.hypot(self.end_x - self.x, self.end_y - self.y) / self.max_x

    def kind(self, long_ms: int = 400, swipe_frac: float = 0.04) -> str:
        if self.displacement > swipe_frac:
            return "swipe"
        return "long_press" if self.duration_ms >= long_ms else "tap"


def detect_touch_device(adb: Adb) -> str:
    """自動偵測觸控輸入裝置：掃 /dev/input/event* 找有 ABS_MT_POSITION_X 的那個。

    雷電的觸控裝置節點號會因設定/開機而變（實測有時 event2 反而是鍵盤），
    硬編 event2 會靜默抓不到。偵測失敗才退回預設 event2。
    """
    try:
        listing = adb.shell("ls", "/dev/input")
    except Exception:
        return _TOUCH_DEV
    devs = [d.strip() for d in listing.split() if d.strip().startswith("event")]
    for d in sorted(devs):
        path = f"/dev/input/{d}"
        try:
            cap = adb.shell("getevent", "-lp", path)
        except Exception:
            continue
        if "ABS_MT_POSITION_X" in cap:
            return path
    return _TOUCH_DEV


def device_uptime(adb: Adb) -> float:
    """裝置開機至今秒數（getevent 時間戳的基準）。"""
    out = adb.shell("cat", "/proc/uptime")
    return float(out.strip().split()[0])


def touch_range(adb: Adb, dev: str | None = None) -> tuple[int, int]:
    """讀觸控裝置 X/Y 最大值；失敗則退回螢幕尺寸-1。"""
    try:
        out = adb.shell("getevent", "-lp", dev or _TOUCH_DEV)
        mx = my = None
        for line in out.splitlines():
            if "ABS_MT_POSITION_X" in line:
                m = re.search(r"max\s+(\d+)", line)
                if m:
                    mx = int(m.group(1))
            elif "ABS_MT_POSITION_Y" in line:
                m = re.search(r"max\s+(\d+)", line)
                if m:
                    my = int(m.group(1))
        if mx and my:
            return mx, my
    except Exception:
        pass
    return 1279, 719


def parse(text: str, t0: float, max_x: int, max_y: int) -> list[Touch]:
    """解析 getevent -lt 輸出成觸控清單。時間以 t0 為基準轉成影片相對秒。"""
    touches: list[Touch] = []
    down_t = None
    sx = sy = lx = ly = None
    for line in text.splitlines():
        m = _LINE.search(line)
        if not m:
            continue
        ts, etype, code, val = float(m.group(1)), m.group(2), m.group(3), m.group(4)
        if code == "BTN_TOUCH":
            if val == "DOWN":
                down_t = ts
                sx = sy = lx = ly = None
            elif val == "UP" and down_t is not None:
                if sx is not None:
                    touches.append(Touch(
                        t_down=down_t - t0, t_up=ts - t0,
                        x=sx, y=sy if sy is not None else 0,
                        end_x=lx if lx is not None else sx,
                        end_y=ly if ly is not None else (sy or 0),
                        max_x=max_x, max_y=max_y))
                down_t = None
        elif code == "ABS_MT_POSITION_X":
            v = int(val, 16)
            lx = v
            if sx is None:
                sx = v
        elif code == "ABS_MT_POSITION_Y":
            v = int(val, 16)
            ly = v
            if sy is None:
                sy = v
    return touches


def save_taps_json(video_path, touches: list[Touch]) -> "Path":
    """把解析出的觸控存成 <影片>.taps.json（供生成腳本取用精確點擊位置）。"""
    import json
    from pathlib import Path
    p = Path(str(video_path) + ".taps.json")
    data = [{"t": round(t.t_down, 3), "duration_ms": t.duration_ms,
             "x": t.x, "y": t.y, "nx": round(t.nx, 4), "ny": round(t.ny, 4),
             "end_nx": round(t.end_x / t.max_x, 4), "end_ny": round(t.end_y / t.max_y, 4),
             "kind": t.kind()} for t in touches]
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def start_capture(adb: Adb, max_seconds: int = 185, dev: str | None = None):
    """非阻塞啟動 getevent。回傳 Popen。

    用 `adb shell -tt` 強制配 PTY（-t 因 stdin 非終端機不會配），讓 getevent 變「行緩衝」
    ——每筆觸控事件立即吐出，避免點擊稀疏時輸出不滿塊緩衝、被 pkill 中止而整段丟失
    （會導致 taps.json 為空）。
    註：曾試「落地成檔(getevent > /sdcard)」，但檔案輸出是區塊緩衝、pkill 時未 flush→整段丟失，
    反而更糟；PTY 行緩衝才是正解。
    """
    return subprocess.Popen(
        [adb.adb, "-s", adb.serial, "shell", "-tt", "timeout", str(max_seconds),
         "getevent", "-lt", dev or _TOUCH_DEV],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")


class Capture:
    """連續 getevent 擷取：背景執行緒即時把每行讀出（避免長 session 塞爆管線）。

    長時間錄影用這個；短擷取可用 start_capture/stop_capture。
    """

    def __init__(self, adb: Adb, max_seconds: int = 3600, dev: str | None = None):
        self.adb = adb
        self.max_seconds = max_seconds
        self.dev = dev or detect_touch_device(adb)
        self._popen = None
        self._lines: list[str] = []
        self._thread = None

    def start(self):
        import threading
        self._popen = start_capture(self.adb, self.max_seconds, dev=self.dev)

        def _reader(p, out):
            try:
                for line in p.stdout:
                    out.append(line)
            except Exception:
                pass
        self._thread = threading.Thread(
            target=_reader, args=(self._popen, self._lines), daemon=True)
        self._thread.start()

    def stop(self) -> str:
        try:
            self.adb.shell("pkill", "getevent")
        except Exception:
            pass
        if self._popen:
            try:
                self._popen.wait(timeout=10)
            except Exception:
                try:
                    self._popen.kill()
                except Exception:
                    pass
        if self._thread:
            self._thread.join(timeout=5)
        return "".join(self._lines)


def stop_capture(adb: Adb, popen, timeout: int = 15) -> str:
    """結束 getevent（pkill 讓裝置端程序退出 → flush）並取回全部輸出。"""
    try:
        adb.shell("pkill", "getevent")
    except Exception:
        pass
    try:
        out, _ = popen.communicate(timeout=timeout)
        return out or ""
    except Exception:
        try:
            popen.kill()
        except Exception:
            pass
        return ""
