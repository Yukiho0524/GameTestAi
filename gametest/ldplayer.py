"""ldconsole.exe 包裝：實例管理與解析度設定。"""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass

from .config import Config, Resolution


@dataclass
class Instance:
    index: int
    title: str
    android_started: bool
    pid: int


class LDConsole:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.console = cfg.console_path

    def _run(self, *args: str, timeout: int = 60) -> str:
        cmd = [self.console, *args]
        proc = subprocess.run(
            cmd, capture_output=True, timeout=timeout,
            # ldconsole 在中文 Windows 多為 gbk 輸出
            text=False,
        )
        out = proc.stdout.decode("gbk", errors="replace")
        return out

    # ---- 查詢 ----
    def list_instances(self) -> list[Instance]:
        """list2 欄位: index,title,top_handle,android_started,pid,vbox_pid,..."""
        out = self._run("list2")
        instances: list[Instance] = []
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(",")
            if len(parts) < 5:
                continue
            try:
                instances.append(Instance(
                    index=int(parts[0]),
                    title=parts[1],
                    android_started=parts[3] == "1",
                    pid=int(parts[4]) if parts[4].lstrip("-").isdigit() else -1,
                ))
            except ValueError:
                continue
        return instances

    def is_running(self, index: int) -> bool:
        out = self._run("isrunning", "--index", str(index)).strip()
        return out.lower() == "running"

    # ---- 生命週期 ----
    def launch(self, index: int) -> None:
        self._run("launch", "--index", str(index))

    def quit(self, index: int) -> None:
        self._run("quit", "--index", str(index))

    def quit_all(self) -> None:
        self._run("quitall")

    def reboot(self, index: int) -> None:
        self._run("reboot", "--index", str(index))

    # ---- 解析度 ----
    def set_resolution(self, index: int, res: Resolution) -> None:
        """設定解析度。需在實例關閉狀態套用，下次啟動才生效。"""
        self._run("modify", "--index", str(index),
                  "--resolution", res.as_modify_arg())

    def apply_resolution_and_launch(self, index: int, res: Resolution) -> None:
        """關閉 → 設定解析度 → 啟動，確保解析度生效。"""
        if self.is_running(index):
            self.quit(index)
            self._wait_stopped(index)
        self.set_resolution(index, res)
        self.launch(index)

    def _wait_stopped(self, index: int, timeout: int = 60) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self.is_running(index):
                return
            time.sleep(1)

    # ---- App ----
    def run_app(self, index: int, package: str) -> None:
        self._run("runapp", "--index", str(index), "--packagename", package)

    def kill_app(self, index: int, package: str) -> None:
        self._run("killapp", "--index", str(index), "--packagename", package)
