"""影像比對與適配檢測引擎。

跨解析度比對：先把兩張圖正規化到同一尺寸再算結構相似度(SSIM)，
所以 720p 的原影片影格也能跟 1080p 的 runtime 截圖比。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

# 正規化比對尺寸（長寬固定，吸收解析度差異）
NORM_SIZE = (512, 288)

# 預設門檻
SIMILARITY_WARN = 0.60     # 與原影片相似度低於此 → 差異過大警示
NO_RESPONSE_SSIM = 0.985   # 點擊前後相似度高於此且應變化 → 視為無反應
SOLID_STD = 8.0            # 全畫面像素標準差低於此 → 純色畫面
BLACK_MEAN = 16.0          # 平均亮度低於此 → 黑屏
SOLID_RATIO = 0.90         # 單一色塊占比高於此 → 掉圖/純色
MAGENTA_RATIO = 0.05       # 洋紅占位(missing texture)占比高於此 → 破圖


def _norm_gray(img: np.ndarray) -> np.ndarray:
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return cv2.resize(g, NORM_SIZE, interpolation=cv2.INTER_AREA).astype(np.float64)


def ssim(a: np.ndarray, b: np.ndarray) -> float:
    """全域平均 SSIM（0~1，1=完全相同）。a,b 為 BGR 影像，尺寸可不同。"""
    x, y = _norm_gray(a), _norm_gray(b)
    C1, C2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    k = (11, 11)
    mu_x = cv2.GaussianBlur(x, k, 1.5)
    mu_y = cv2.GaussianBlur(y, k, 1.5)
    mu_x2, mu_y2, mu_xy = mu_x * mu_x, mu_y * mu_y, mu_x * mu_y
    sx2 = cv2.GaussianBlur(x * x, k, 1.5) - mu_x2
    sy2 = cv2.GaussianBlur(y * y, k, 1.5) - mu_y2
    sxy = cv2.GaussianBlur(x * y, k, 1.5) - mu_xy
    smap = ((2 * mu_xy + C1) * (2 * sxy + C2)) / \
           ((mu_x2 + mu_y2 + C1) * (sx2 + sy2 + C2))
    return float(np.clip(smap.mean(), 0.0, 1.0))


def diff_regions(a: np.ndarray, b: np.ndarray, thresh: int = 40):
    """回傳 a,b 顯著差異區塊（正規化座標 0~1 的 bbox 清單）。"""
    ga = cv2.resize(cv2.cvtColor(a, cv2.COLOR_BGR2GRAY), NORM_SIZE)
    gb = cv2.resize(cv2.cvtColor(b, cv2.COLOR_BGR2GRAY), NORM_SIZE)
    d = cv2.absdiff(ga, gb)
    _, mask = cv2.threshold(d, thresh, 255, cv2.THRESH_BINARY)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    W, H = NORM_SIZE
    boxes = []
    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        if w * h < 0.002 * W * H:  # 忽略極小雜訊
            continue
        boxes.append((x / W, y / H, (x + w) / W, (y + h) / H))
    return boxes


@dataclass
class Detection:
    black_or_solid: bool = False
    solid_ratio: float = 0.0
    mean_brightness: float = 0.0
    missing_texture: bool = False
    magenta_ratio: float = 0.0
    # 解析度/黑邊
    actual_size: tuple[int, int] | None = None
    expected_size: tuple[int, int] | None = None
    size_ok: bool = True
    letterbox: dict[str, float] = field(default_factory=dict)  # top/bottom/left/right 占比
    notes: list[str] = field(default_factory=list)

    @property
    def has_issue(self) -> bool:
        return self.black_or_solid or self.missing_texture or not self.size_ok \
            or any(v > 0.02 for v in self.letterbox.values())


def detect_black_solid_missing(img: np.ndarray) -> Detection:
    """黑屏 / 純色 / 掉圖 / 破圖(洋紅占位)偵測。"""
    det = Detection()
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    det.mean_brightness = float(g.mean())
    std = float(g.std())

    # 主色占比：用粗量化直方圖找最大宗顏色
    small = cv2.resize(img, (160, 90), interpolation=cv2.INTER_AREA)
    q = (small // 32).reshape(-1, 3)
    _, counts = np.unique(q, axis=0, return_counts=True)
    det.solid_ratio = float(counts.max() / q.shape[0])

    if det.mean_brightness < BLACK_MEAN or std < SOLID_STD or det.solid_ratio > SOLID_RATIO:
        det.black_or_solid = True
        if det.mean_brightness < BLACK_MEAN:
            det.notes.append("接近全黑畫面（可能載入失敗）")
        else:
            det.notes.append(f"大面積純色 {det.solid_ratio:.0%}（可能掉圖）")

    # 洋紅占位（missing texture 常見的 magenta）
    b, gch, r = small[..., 0], small[..., 1], small[..., 2]
    magenta = ((r > 180) & (b > 180) & (gch < 80))
    det.magenta_ratio = float(magenta.mean())
    if det.magenta_ratio > MAGENTA_RATIO:
        det.missing_texture = True
        det.notes.append(f"偵測到洋紅占位 {det.magenta_ratio:.0%}（破圖/材質遺失）")
    return det


def check_resolution_letterbox(img: np.ndarray, det: Detection,
                               expected: tuple[int, int]) -> Detection:
    """驗證實際解析度與黑邊(letterbox)比例。"""
    h, w = img.shape[:2]
    det.actual_size = (w, h)
    det.expected_size = expected
    det.size_ok = (w, h) == expected
    if not det.size_ok:
        det.notes.append(f"實際解析度 {w}x{h} 與預期 {expected[0]}x{expected[1]} 不符")

    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    dark = g < BLACK_MEAN
    col_dark = dark.all(axis=0)   # 整欄皆黑
    row_dark = dark.all(axis=1)   # 整列皆黑

    def edge_run(mask, n):
        top = 0
        for v in mask:
            if v:
                top += 1
            else:
                break
        return top / n

    det.letterbox = {
        "left": edge_run(col_dark, w),
        "right": edge_run(col_dark[::-1], w),
        "top": edge_run(row_dark, h),
        "bottom": edge_run(row_dark[::-1], h),
    }
    big = {k: v for k, v in det.letterbox.items() if v > 0.02}
    if big:
        det.notes.append("黑邊：" + ", ".join(f"{k}={v:.0%}" for k, v in big.items()))
    return det


def is_no_response(before: np.ndarray, after: np.ndarray) -> bool:
    """點擊前後畫面幾乎相同 → 視為無反應。"""
    return ssim(before, after) >= NO_RESPONSE_SSIM


def load_image(path: str | Path) -> np.ndarray | None:
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    return img
