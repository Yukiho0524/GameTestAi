"""adb.exe 包裝：連線、輸入、截圖。"""
from __future__ import annotations

import subprocess
import time

import cv2
import numpy as np

from .config import Config


class AdbError(RuntimeError):
    pass


class Adb:
    """以 serial 鎖定單一裝置操作。"""

    def __init__(self, cfg: Config, serial: str):
        self.adb = cfg.adb_path
        self.serial = serial

    # ---- 底層 ----
    def _base(self) -> list[str]:
        return [self.adb, "-s", self.serial]

    def shell(self, *args: str, timeout: int = 30) -> str:
        proc = subprocess.run(
            [*self._base(), "shell", *args],
            capture_output=True, text=True, timeout=timeout,
        )
        if proc.returncode != 0:
            raise AdbError(f"adb shell {' '.join(args)} 失敗: {proc.stderr.strip()}")
        return proc.stdout

    def shell_bytes(self, *args: str, timeout: int = 30) -> bytes:
        """用 exec-out 取得原始位元組（避免 Windows CRLF 破壞 PNG）。"""
        proc = subprocess.run(
            [*self._base(), "exec-out", *args],
            capture_output=True, timeout=timeout,
        )
        if proc.returncode != 0:
            raise AdbError(f"adb exec-out {' '.join(args)} 失敗: "
                           f"{proc.stderr.decode('utf-8', 'replace').strip()}")
        return proc.stdout

    # ---- 開機狀態 ----
    def wait_boot(self, timeout: int) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self.shell("getprop", "sys.boot_completed", timeout=10).strip() == "1":
                    # 再等 UI 穩定
                    time.sleep(3)
                    return True
            except (AdbError, subprocess.TimeoutExpired):
                pass
            time.sleep(2)
        return False

    # ---- 螢幕資訊 ----
    def screen_size(self) -> tuple[int, int]:
        """回傳 (width, height)。解析 wm size。"""
        out = self.shell("wm", "size")
        # 例: "Physical size: 1280x720" 或 "Override size: 1280x720"
        line = out.strip().splitlines()[-1]
        wh = line.split(":")[-1].strip()
        w, h = wh.split("x")
        return int(w), int(h)

    def screencap(self) -> np.ndarray:
        """擷取畫面回傳 BGR ndarray。"""
        raw = self.shell_bytes("screencap", "-p")
        arr = np.frombuffer(raw, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            raise AdbError("螢幕截圖解碼失敗")
        return img

    # ---- 輸入 ----
    def tap(self, x: int, y: int) -> None:
        self.shell("input", "tap", str(x), str(y))

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        self.shell("input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms))

    def input_text(self, text: str) -> None:
        # 空白需轉成 %s
        self.shell("input", "text", text.replace(" ", "%s"))

    def keyevent(self, keycode: str | int) -> None:
        self.shell("input", "keyevent", str(keycode))

    def back(self) -> None:
        self.keyevent(4)

    def home(self) -> None:
        self.keyevent(3)


def connect_instance(cfg: Config, index: int) -> Adb:
    """連線指定雷電實例，回傳鎖定 serial 的 Adb。

    策略：先試 127.0.0.1:(base_port + index*2)，失敗則退回掃描 adb devices。
    """
    adb_path = cfg.adb_path
    port = cfg.adb_base_port + index * 2
    serial = f"127.0.0.1:{port}"

    subprocess.run([adb_path, "connect", serial], capture_output=True, timeout=20)
    devices = _list_devices(adb_path)

    if serial in devices:
        return Adb(cfg, serial)

    # 退回：若只有一台，直接用；否則挑第一台 emulator-/127.0.0.1
    if len(devices) == 1:
        return Adb(cfg, devices[0])
    for d in devices:
        if d.startswith("emulator-") or d.startswith("127.0.0.1"):
            return Adb(cfg, d)

    raise AdbError(
        f"找不到可用裝置。預期 {serial}，adb devices 回傳: {devices or '無'}。"
        "請確認雷電實例已啟動。"
    )


def _list_devices(adb_path: str) -> list[str]:
    proc = subprocess.run([adb_path, "devices"], capture_output=True, text=True, timeout=20)
    devices = []
    for line in proc.stdout.splitlines()[1:]:
        line = line.strip()
        if line and "\tdevice" in line:
            devices.append(line.split("\t")[0])
    return devices
