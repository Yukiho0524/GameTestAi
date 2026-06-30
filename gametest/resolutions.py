"""常見手遊解析度預設庫，分為直版（portrait）與橫版（landscape）。

每個預設含 width / height / dpi / orientation。dpi 依面板密度概估，
雷電 modify --resolution 需要 寬,高,dpi 三個值。
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import Resolution


@dataclass(frozen=True)
class Preset:
    key: str            # 唯一代號（給 UI / 設定檔引用）
    label: str          # 顯示名稱
    width: int
    height: int
    dpi: int
    orientation: str    # "portrait" | "landscape"

    def to_resolution(self) -> Resolution:
        return Resolution(width=self.width, height=self.height, dpi=self.dpi)


# ===== 橫版（landscape）：寬 > 高，多數動作/RPG/MOBA 手遊 =====
LANDSCAPE: list[Preset] = [
    Preset("ld_960x540",   "qHD 960x540 (16:9)",      960,  540, 160, "landscape"),
    Preset("ld_1280x720",  "HD 1280x720 (16:9)",      1280, 720, 240, "landscape"),
    Preset("ld_1600x720",  "HD+ 1600x720 (20:9)",     1600, 720, 240, "landscape"),
    Preset("ld_1920x1080", "FHD 1920x1080 (16:9)",    1920, 1080, 320, "landscape"),
    Preset("ld_2340x1080", "FHD+ 2340x1080 (19.5:9)", 2340, 1080, 400, "landscape"),
    Preset("ld_2400x1080", "FHD+ 2400x1080 (20:9)",   2400, 1080, 400, "landscape"),
    Preset("ld_2048x1536", "iPad 2048x1536 (4:3)",    2048, 1536, 320, "landscape"),
]

# ===== 直版（portrait）：高 > 寬，多數卡牌/放置/二次元手遊 =====
PORTRAIT: list[Preset] = [
    Preset("pt_540x960",   "qHD 540x960 (9:16)",      540,  960, 160, "portrait"),
    Preset("pt_720x1280",  "HD 720x1280 (9:16)",      720,  1280, 240, "portrait"),
    Preset("pt_720x1600",  "HD+ 720x1600 (9:20)",     720,  1600, 240, "portrait"),
    Preset("pt_1080x1920", "FHD 1080x1920 (9:16)",    1080, 1920, 320, "portrait"),
    Preset("pt_1080x2340", "FHD+ 1080x2340 (9:19.5)", 1080, 2340, 400, "portrait"),
    Preset("pt_1080x2400", "FHD+ 1080x2400 (9:20)",   1080, 2400, 400, "portrait"),
    Preset("pt_1536x2048", "iPad 1536x2048 (3:4)",    1536, 2048, 320, "portrait"),
]

ALL: list[Preset] = LANDSCAPE + PORTRAIT
_BY_KEY = {p.key: p for p in ALL}


def get(key: str) -> Preset:
    if key not in _BY_KEY:
        raise KeyError(f"未知解析度預設 '{key}'，可用: {list(_BY_KEY)}")
    return _BY_KEY[key]


def resolve_keys(keys: list[str]) -> list[Resolution]:
    """把預設 key 清單轉成 Resolution 清單。"""
    return [get(k).to_resolution() for k in keys]
