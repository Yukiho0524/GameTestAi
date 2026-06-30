"""高階裝置封裝：整合 ldconsole 與 adb，提供正規化座標操作。"""
from __future__ import annotations

import numpy as np

from .adb import Adb, connect_instance
from .config import Config, Resolution
from .ldplayer import LDConsole


class Device:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.index = cfg.instance_index
        self.console = LDConsole(cfg)
        self.adb: Adb | None = None
        self._size: tuple[int, int] | None = None

    # ---- 解析度 / 連線 ----
    def prepare(self, res: Resolution) -> None:
        """套用解析度、開啟雷電、等裝置出現、等開機完成、連線 adb。"""
        print(f"  [1/3] 套用解析度 {res.label} 並開啟雷電 ...")
        self.console.apply_resolution_and_launch(self.index, res)

        print("  [2/3] 等待裝置上線（雷電開機中，adb 可能要一陣子才抓到）...")
        self.adb = connect_instance(
            self.cfg, self.index,
            on_wait=lambda s: print(f"        ...等待中 {s}s", end="\r", flush=True))

        print("\n  [3/3] 等待 Android 開機完成 ...")
        if not self.adb.wait_boot(self.cfg.boot_timeout):
            raise TimeoutError(f"實例 {self.index} 在 {self.cfg.boot_timeout}s 內未開機完成")
        self._size = self.adb.screen_size()
        print(f"        裝置就緒：{self.adb.serial}，解析度 {self._size[0]}x{self._size[1]}")

    @property
    def size(self) -> tuple[int, int]:
        if self._size is None:
            raise RuntimeError("裝置尚未 prepare()")
        return self._size

    def _denorm(self, x: float, y: float) -> tuple[int, int]:
        w, h = self.size
        return int(x * w), int(y * h)

    # ---- App ----
    def start_app(self) -> None:
        # 用 adb am start -n <pkg/activity> 啟動（失敗退回 monkey）
        how = self.adb.start_app(self.cfg.package_name)
        print(f"        啟動 App（{how}）")

    def stop_app(self) -> None:
        # 用 adb am force-stop 確保乾淨狀態
        if self.adb:
            self.adb.force_stop(self.cfg.package_name)
        else:
            self.console.kill_app(self.index, self.cfg.package_name)

    # ---- 操作（正規化座標）----
    def tap(self, x: float, y: float) -> None:
        px, py = self._denorm(x, y)
        self.adb.tap(px, py)

    def tap_pixel(self, px: int, py: int) -> None:
        self.adb.tap(px, py)

    def long_press(self, x: float, y: float, duration_ms: int = 800) -> None:
        px, py = self._denorm(x, y)
        self.long_press_pixel(px, py, duration_ms)

    def long_press_pixel(self, px: int, py: int, duration_ms: int = 800) -> None:
        # 同一點、拉長持續時間 = 長壓
        self.adb.swipe(px, py, px, py, duration_ms)

    def swipe(self, x1: float, y1: float, x2: float, y2: float, duration_ms: int = 300) -> None:
        a = self._denorm(x1, y1)
        b = self._denorm(x2, y2)
        self.adb.swipe(a[0], a[1], b[0], b[1], duration_ms)

    def input_text(self, text: str) -> None:
        self.adb.input_text(text)

    def key(self, keycode) -> None:
        mapping = {"back": 4, "home": 3, "enter": 66, "menu": 82}
        self.adb.keyevent(mapping.get(str(keycode).lower(), keycode))

    def screencap(self) -> np.ndarray:
        return self.adb.screencap()

    # ---- 清理 ----
    def shutdown(self) -> None:
        try:
            self.console.quit(self.index)
        except Exception:
            pass
