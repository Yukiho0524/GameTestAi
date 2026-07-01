"""OpenCV 模板比對：支援多尺度，解決跨解析度問題。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .config import Config


@dataclass
class MatchResult:
    found: bool
    score: float
    # 命中中心點（螢幕像素座標）
    center: tuple[int, int] | None
    scale: float


def _load_template(path: Path) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(f"無法讀取模板圖: {path}")
    return img


def match_template(
    screen: np.ndarray,
    template_path: Path,
    cfg: Config,
    region: tuple[float, float, float, float] | None = None,
    threshold: float | None = None,
) -> MatchResult:
    """在 screen 中尋找 template。

    region: 限定搜尋範圍 (x1,y1,x2,y2)，正規化 0~1；None 則全畫面。
    threshold: 覆寫比對門檻（None 用 cfg.threshold）。
    多尺度比對會縮放模板以適應不同解析度。
    """
    thr = cfg.threshold if threshold is None else threshold
    template = _load_template(template_path)
    sh, sw = screen.shape[:2]

    # 限定搜尋區域
    offset_x, offset_y = 0, 0
    search = screen
    if region:
        x1 = int(region[0] * sw); y1 = int(region[1] * sh)
        x2 = int(region[2] * sw); y2 = int(region[3] * sh)
        x1, x2 = sorted((max(0, x1), min(sw, x2)))
        y1, y2 = sorted((max(0, y1), min(sh, y2)))
        search = screen[y1:y2, x1:x2]
        offset_x, offset_y = x1, y1

    if cfg.multi_scale:
        scales = np.linspace(cfg.scale_min, cfg.scale_max, cfg.scale_steps)
    else:
        scales = [1.0]

    best = MatchResult(found=False, score=-1.0, center=None, scale=1.0)
    th, tw = template.shape[:2]
    Hs, Ws = search.shape[:2]

    for scale in scales:
        nw, nh = int(tw * scale), int(th * scale)
        if nw < 8 or nh < 8 or nw > Ws or nh > Hs:
            continue
        resized = cv2.resize(template, (nw, nh), interpolation=cv2.INTER_AREA)
        res = cv2.matchTemplate(search, resized, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)
        if max_val > best.score:
            cx = offset_x + max_loc[0] + nw // 2
            cy = offset_y + max_loc[1] + nh // 2
            best = MatchResult(
                found=max_val >= thr,
                score=float(max_val),
                center=(cx, cy),
                scale=float(scale),
            )

    return best
