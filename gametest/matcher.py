"""OpenCV 圖像比對：多尺度模板比對 + SIFT 關鍵點級聯，解決跨解析度問題。

比對策略（仿 Airtest CVSTRATEGY，不用 Poco 的純圖像做法）：
  1. mstpl：多尺度 TM_CCOEFF_NORMED（像素相關性；快，但對縮放/亮度較敏感）。
  2. sift ：關鍵點特徵 + 單應性（尺度/旋轉/局部遮擋不變；模板不必與畫面等比例）。
模板比對過門檻就用；不過再退 SIFT。SIFT 對「近純色/無特徵」模板會自然抓不到
關鍵點而回報失敗——等於順便揪出裁壞的模板。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .config import Config

# SIFT 關鍵點級聯參數
_SIFT_RATIO = 0.75      # Lowe ratio test
_SIFT_MIN_GOOD = 8      # 至少要幾個 good match 才嘗試單應性
_HAS_SIFT = hasattr(cv2, "SIFT_create")
_sift = cv2.SIFT_create() if _HAS_SIFT else None


@dataclass
class MatchResult:
    found: bool
    score: float
    # 命中中心點（螢幕像素座標）
    center: tuple[int, int] | None
    scale: float
    method: str = "tpl"  # 命中用的方法：tpl / sift


def _load_template(path: Path) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(f"無法讀取模板圖: {path}")
    return img


def _match_multiscale(
    search: np.ndarray, template: np.ndarray, cfg: Config,
    offset_x: int, offset_y: int, thr: float,
) -> MatchResult:
    if cfg.multi_scale:
        scales = np.linspace(cfg.scale_min, cfg.scale_max, cfg.scale_steps)
    else:
        scales = [1.0]

    best = MatchResult(found=False, score=-1.0, center=None, scale=1.0, method="tpl")
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
                found=max_val >= thr, score=float(max_val),
                center=(cx, cy), scale=float(scale), method="tpl",
            )
    return best


def _match_sift(
    search: np.ndarray, template: np.ndarray,
    offset_x: int, offset_y: int, thr: float,
) -> MatchResult | None:
    """SIFT 關鍵點比對。回傳 MatchResult（found 依信心是否過門檻）或 None（無法比對）。

    信心分數：用單應性把螢幕命中區反投影回模板尺寸，再與模板做 TM_CCOEFF_NORMED，
    得到可與模板門檻直接相比的 0~1 分數。
    """
    if _sift is None:
        return None
    g_tpl = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    g_scr = cv2.cvtColor(search, cv2.COLOR_BGR2GRAY)
    kp1, des1 = _sift.detectAndCompute(g_tpl, None)
    kp2, des2 = _sift.detectAndCompute(g_scr, None)
    if des1 is None or des2 is None or len(kp1) < 2 or len(kp2) < 2:
        return None

    bf = cv2.BFMatcher(cv2.NORM_L2)
    try:
        knn = bf.knnMatch(des1, des2, k=2)
    except cv2.error:
        return None
    good = [m for m, n in (p for p in knn if len(p) == 2)
            if m.distance < _SIFT_RATIO * n.distance]
    if len(good) < _SIFT_MIN_GOOD:
        return None

    src = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
    if H is None:
        return None

    th, tw = g_tpl.shape[:2]
    # 模板中心投影到螢幕座標
    center_t = np.float32([[[tw / 2.0, th / 2.0]]])
    cen = cv2.perspectiveTransform(center_t, H)[0][0]
    cx = int(offset_x + cen[0]); cy = int(offset_y + cen[1])

    # 反投影螢幕命中區回模板尺寸，比對出可與門檻相比的信心
    warped = cv2.warpPerspective(g_scr, H, (tw, th), flags=cv2.WARP_INVERSE_MAP)
    conf = float(cv2.matchTemplate(warped, g_tpl, cv2.TM_CCOEFF_NORMED).max())
    # 估算尺度（單應性左上 2x2 的面積開根）
    scale = float(np.sqrt(abs(H[0, 0] * H[1, 1] - H[0, 1] * H[1, 0])) or 1.0)
    return MatchResult(found=conf >= thr, score=conf,
                       center=(cx, cy), scale=scale, method="sift")


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
    先多尺度模板比對；不過門檻再退 SIFT 關鍵點比對。
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

    best = _match_multiscale(search, template, cfg, offset_x, offset_y, thr)
    if best.found:
        return best

    # 模板比對沒過 → 退 SIFT 關鍵點比對
    sift = _match_sift(search, template, offset_x, offset_y, thr)
    if sift is not None and sift.score > best.score:
        return sift
    return best
