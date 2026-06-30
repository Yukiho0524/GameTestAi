"""設定檔載入與路徑解析。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Resolution:
    width: int
    height: int
    dpi: int = 240

    @property
    def label(self) -> str:
        return f"{self.width}x{self.height}"

    def as_modify_arg(self) -> str:
        # ldconsole modify --resolution 需要 "寬,高,dpi"
        return f"{self.width},{self.height},{self.dpi}"


@dataclass
class Config:
    raw: dict[str, Any]
    root: Path

    # ldplayer
    console_path: str
    adb_path: str
    instance_index: int
    boot_timeout: int
    adb_base_port: int

    # test
    package_name: str
    repeat: int
    resolutions: list[Resolution]
    screenshot_every_step: bool
    restart_app_each_run: bool

    # matching
    threshold: float
    multi_scale: bool
    scale_min: float
    scale_max: float
    scale_steps: int

    # paths（已解析為絕對路徑）
    scripts_dir: Path
    assets_dir: Path
    recordings_dir: Path
    results_dir: Path
    frames_dir: Path
    video_source_dir: Path

    # watch
    watch_extensions: list[str]
    watch_every_sec: float
    watch_poll_interval: int

    def ensure_dirs(self) -> None:
        for d in (self.scripts_dir, self.assets_dir, self.recordings_dir,
                  self.results_dir, self.frames_dir):
            d.mkdir(parents=True, exist_ok=True)


def _abs(root: Path, p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else (root / path)


def load_config(path: str | os.PathLike | None = None) -> Config:
    """載入設定檔。預設讀 <repo>/config/settings.yaml。"""
    root = Path(__file__).resolve().parent.parent
    cfg_path = Path(path) if path else root / "config" / "settings.yaml"
    if not cfg_path.exists():
        raise FileNotFoundError(f"找不到設定檔: {cfg_path}")

    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    ld = data.get("ldplayer", {})
    test = data.get("test", {})
    match = data.get("matching", {})
    paths = data.get("paths", {})
    watch = data.get("watch", {})

    # 解析度可用兩種方式指定：
    #   1) resolution_presets: [預設 key, ...]（引用 resolutions.py 的預設庫）
    #   2) resolutions: [{width,height,dpi}, ...]（直接寫死）
    # 兩者皆給時，presets 優先。
    preset_keys = test.get("resolution_presets", [])
    if preset_keys:
        from .resolutions import resolve_keys  # 延遲匯入避免循環
        resolutions = resolve_keys(preset_keys)
    else:
        resolutions = [
            Resolution(width=r["width"], height=r["height"], dpi=r.get("dpi", 240))
            for r in test.get("resolutions", [])
        ]
    if not resolutions:
        raise ValueError("settings.yaml 至少要設定一個解析度（resolution_presets 或 resolutions）")

    return Config(
        raw=data,
        root=root,
        console_path=ld.get("console_path", "ldconsole.exe"),
        adb_path=ld.get("adb_path", "adb.exe"),
        instance_index=int(ld.get("instance_index", 0)),
        boot_timeout=int(ld.get("boot_timeout", 120)),
        adb_base_port=int(ld.get("adb_base_port", 5555)),
        package_name=test.get("package_name", ""),
        repeat=int(test.get("repeat", 1)),
        resolutions=resolutions,
        screenshot_every_step=bool(test.get("screenshot_every_step", True)),
        restart_app_each_run=bool(test.get("restart_app_each_run", True)),
        threshold=float(match.get("threshold", 0.8)),
        multi_scale=bool(match.get("multi_scale", True)),
        scale_min=float(match.get("scale_min", 0.5)),
        scale_max=float(match.get("scale_max", 1.5)),
        scale_steps=int(match.get("scale_steps", 21)),
        scripts_dir=_abs(root, paths.get("scripts", "scripts")),
        assets_dir=_abs(root, paths.get("assets", "assets")),
        recordings_dir=_abs(root, paths.get("recordings", "recordings")),
        results_dir=_abs(root, paths.get("results", "results")),
        frames_dir=_abs(root, paths.get("frames", "recordings/frames")),
        video_source_dir=_abs(root, paths.get("video_source", "recordings")),
        watch_extensions=[e.lower() for e in watch.get(
            "extensions", [".mp4", ".mkv", ".avi", ".mov", ".flv"])],
        watch_every_sec=float(watch.get("every_sec", 1.0)),
        watch_poll_interval=int(watch.get("poll_interval", 5)),
    )
