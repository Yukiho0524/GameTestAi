"""包體名（package）驗證：確認 App 已安裝、可用 ADB 啟動並進入前景。"""
from __future__ import annotations

import time
from dataclasses import dataclass

from .adb import Adb


@dataclass
class AppCheckResult:
    package: str
    installed: bool
    launched: bool
    foreground: bool
    current_focus: str = ""
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.installed and self.launched and self.foreground


def list_packages(adb: Adb, keyword: str = "") -> list[str]:
    """列出已安裝套件，可選關鍵字過濾。"""
    args = ["pm", "list", "packages"]
    if keyword:
        args.append(keyword)
    out = adb.shell(*args)
    pkgs = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("package:"):
            pkgs.append(line[len("package:"):])
    return sorted(pkgs)


def is_installed(adb: Adb, package: str) -> bool:
    # 精確比對（pm list packages 會做子字串比對，故再過濾一次）
    return package in list_packages(adb, package)


def _current_focus(adb: Adb) -> str:
    """取得目前前景視窗/Activity 字串（跨 Android 版本盡量相容）。"""
    for cmd in (("dumpsys", "activity", "activities"),
                ("dumpsys", "window", "windows")):
        try:
            out = adb.shell(*cmd)
        except Exception:
            continue
        for key in ("mResumedActivity", "ResumedActivity",
                    "mCurrentFocus", "mFocusedApp"):
            for line in out.splitlines():
                if key in line:
                    return line.strip()
    return ""


def launch_and_verify(adb: Adb, package: str, timeout: float = 20.0) -> AppCheckResult:
    """用 monkey 啟動 App 並確認進入前景。"""
    res = AppCheckResult(package=package, installed=False,
                         launched=False, foreground=False)

    if not is_installed(adb, package):
        res.message = f"套件未安裝：{package}"
        return res
    res.installed = True

    # 用 monkey 觸發 LAUNCHER intent（不需知道確切 Activity 名）
    try:
        adb.shell("monkey", "-p", package,
                  "-c", "android.intent.category.LAUNCHER", "1")
        res.launched = True
    except Exception as e:  # noqa: BLE001
        res.message = f"啟動失敗：{e}"
        return res

    # 輪詢前景視窗，確認包名出現
    deadline = time.time() + timeout
    while time.time() < deadline:
        focus = _current_focus(adb)
        res.current_focus = focus
        if package in focus:
            res.foreground = True
            res.message = "App 已啟動並進入前景"
            return res
        time.sleep(1)

    res.message = (f"App 已下啟動指令，但 {timeout:.0f}s 內未偵測到前景為 {package}"
                   f"（目前前景：{res.current_focus or '未知'}）")
    return res
