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

    # ---- 啟動 App ----
    def resolve_launch_activity(self, package: str) -> str | None:
        """解析 App 的啟動 Activity，回傳 'pkg/.Activity' 元件字串。"""
        try:
            out = self.shell("cmd", "package", "resolve-activity", "--brief", package)
        except AdbError:
            return None
        for line in reversed(out.splitlines()):
            line = line.strip()
            if "/" in line and line.startswith(package):
                return line
        return None

    def start_app(self, package: str) -> str:
        """啟動 App：優先 am start -n <pkg/activity>，失敗退回 monkey。回傳採用方式。"""
        comp = self.resolve_launch_activity(package)
        if comp:
            self.shell("am", "start", "-n", comp)
            return f"am start -n {comp}"
        # 退回：用 monkey 觸發 LAUNCHER intent（不需知道 Activity）
        self.shell("monkey", "-p", package,
                   "-c", "android.intent.category.LAUNCHER", "1")
        return "monkey LAUNCHER"

    # ---- 螢幕錄影（會錄到 show_touches 觸控標記）----
    def screenrecord(self, seconds: int, local_path: str,
                     bitrate: int = 8_000_000) -> None:
        """用 adb screenrecord 錄影 seconds 秒，pull 回 local_path。

        相較雷電內建錄影，screenrecord 會把系統觸控疊層(顯示點按操作)一起錄進去。
        """
        dev = "/sdcard/gametest_rec.mp4"
        self.shell("rm", "-f", dev)
        subprocess.run(
            [*self._base(), "shell", "screenrecord",
             "--time-limit", str(seconds), "--bit-rate", str(bitrate), dev],
            capture_output=True, timeout=seconds + 60,
        )
        # screenrecord 結束後檔案才完整，pull 回本機
        subprocess.run([*self._base(), "pull", dev, local_path],
                       capture_output=True, timeout=120)
        try:
            self.shell("rm", "-f", dev)
        except AdbError:
            pass

    _REC_DEV = "/sdcard/gametest_rec.mp4"

    def screenrecord_start(self, max_seconds: int = 180,
                           bitrate: int = 8_000_000):
        """開始錄影（非阻塞），回傳 Popen 控制代碼。screenrecord 上限 180 秒。"""
        self.shell("settings", "put", "system", "show_touches", "1")
        try:
            self.shell("rm", "-f", self._REC_DEV)
        except AdbError:
            pass
        return subprocess.Popen(
            [*self._base(), "shell", "screenrecord", "--time-limit",
             str(min(max_seconds, 180)), "--bit-rate", str(bitrate), self._REC_DEV],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def screenrecord_stop(self, popen, local_path: str, timeout: int = 40) -> None:
        """送 INT 讓 screenrecord 收尾寫檔，等結束後 pull 回本機。"""
        try:
            self.shell("pkill", "-INT", "screenrecord")
        except AdbError:
            pid = ""
            try:
                pid = self.shell("pidof", "screenrecord").strip()
            except AdbError:
                pass
            if pid:
                self.shell("kill", "-INT", pid.split()[0])
        try:
            popen.wait(timeout=timeout)
        except Exception:
            try:
                popen.kill()
            except Exception:
                pass
        time.sleep(1.0)
        subprocess.run([*self._base(), "pull", self._REC_DEV, local_path],
                       capture_output=True, timeout=120)
        try:
            self.shell("rm", "-f", self._REC_DEV)
        except AdbError:
            pass

    # ---- 分段錄影（自動接續 >3 分鐘用）----
    def screenrecord_seg_start(self, device_path: str, seconds: int,
                               bitrate: int = 8_000_000):
        """啟動一段錄影（非阻塞），到 seconds 秒自動結束。回傳 Popen。"""
        try:
            self.shell("rm", "-f", device_path)
        except AdbError:
            pass
        return subprocess.Popen(
            [*self._base(), "shell", "screenrecord", "--time-limit",
             str(min(seconds, 180)), "--bit-rate", str(bitrate), device_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def screenrecord_intr(self) -> None:
        """送 INT 讓目前 screenrecord 立即收尾（提早結束該段）。"""
        try:
            self.shell("pkill", "-INT", "screenrecord")
        except AdbError:
            pass

    def pull_file(self, device_path: str, local_path: str) -> None:
        subprocess.run([*self._base(), "pull", device_path, local_path],
                       capture_output=True, timeout=180)
        try:
            self.shell("rm", "-f", device_path)
        except AdbError:
            pass

    def force_stop(self, package: str) -> None:
        try:
            self.shell("am", "force-stop", package)
        except AdbError:
            pass

    # ---- logcat（崩潰/ANR 偵測）----
    def logcat_clear(self) -> None:
        try:
            subprocess.run([*self._base(), "logcat", "-c"],
                           capture_output=True, timeout=15)
        except Exception:
            pass

    def logcat_scan_crashes(self, package: str = "") -> list[str]:
        """傾印 logcat 並掃 FATAL/ANR/crash。回傳命中的行（去重）。"""
        try:
            proc = subprocess.run([*self._base(), "logcat", "-d"],
                                  capture_output=True, text=True,
                                  errors="replace", timeout=30)
        except Exception as e:  # noqa: BLE001
            return [f"(logcat 讀取失敗: {e})"]
        hits, seen = [], set()
        keys = ("FATAL EXCEPTION", "ANR in", "force-finishing", "CRASH",
                "java.lang.", "Process crashed", "signal 11", "tombstoned")
        for line in proc.stdout.splitlines():
            if any(k in line for k in keys):
                if package and package not in line and "ANR in" not in line \
                        and "FATAL" not in line:
                    continue
                s = line.strip()
                if s not in seen:
                    seen.add(s)
                    hits.append(s)
        return hits


def _find_device(adb_path: str, serial: str) -> str | None:
    """嘗試連線並從 adb devices 找出可用裝置 serial；找不到回 None。"""
    subprocess.run([adb_path, "connect", serial], capture_output=True, timeout=20)
    devices = _list_devices(adb_path)
    if serial in devices:
        return serial
    if len(devices) == 1:
        return devices[0]
    for d in devices:
        if d.startswith("emulator-") or d.startswith("127.0.0.1"):
            return d
    return None


def connect_instance(cfg: Config, index: int, timeout: int | None = None,
                     on_wait=None) -> Adb:
    """連線指定雷電實例，回傳鎖定 serial 的 Adb。

    雷電剛啟動時 adb 抓不到裝置，故會「輪詢重試」直到裝置出現或逾時。
    timeout 預設用 cfg.boot_timeout；on_wait(秒) 可回報等待進度。
    """
    adb_path = cfg.adb_path
    port = cfg.adb_base_port + index * 2
    serial = f"127.0.0.1:{port}"
    timeout = cfg.boot_timeout if timeout is None else timeout

    start = time.time()
    while True:
        found = _find_device(adb_path, serial)
        if found:
            return Adb(cfg, found)
        waited = time.time() - start
        if waited >= timeout:
            break
        if on_wait:
            on_wait(int(waited))
        time.sleep(2)

    raise AdbError(
        f"等待 {timeout}s 仍找不到可用裝置（預期 {serial}）。"
        "請確認雷電實例已啟動、adb 埠正確（settings.yaml adb_base_port）。"
    )


def _list_devices(adb_path: str) -> list[str]:
    proc = subprocess.run([adb_path, "devices"], capture_output=True, text=True, timeout=20)
    devices = []
    for line in proc.stdout.splitlines()[1:]:
        line = line.strip()
        if line and "\tdevice" in line:
            devices.append(line.split("\t")[0])
    return devices
