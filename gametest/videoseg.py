"""影片畫面分段：把錄影切成「穩定畫面段」與「轉場」，重建操作流程結構。

用途（genscript v2）：
- 每個點擊歸屬到所在畫面段 → scene-gate 用該段的穩定幀（避開轉場/彈窗瞬間）。
- 點擊後 2.5s 內出現段邊界 → 該點擊「觸發轉場」→ 生成 until 後置條件
  （點擊後等下一段畫面出現，等不到且按鈕還在就補點 → 根治點擊被吞）。
- 兩個點擊之間的無點擊轉場（載入/輪播）＝自動轉場，由下一步 scene-gate 的
  gap 縮放 timeout 吸收。
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class Segment:
    start: float
    end: float

    @property
    def mid(self) -> float:
        return (self.start + self.end) / 2.0

    @property
    def length(self) -> float:
        return self.end - self.start


def segment_video(src, sample_step: float = 0.25, stable_thr: float = 0.035,
                  min_len: float = 1.0) -> list[Segment]:
    """把影片切成穩定畫面段。src 是 genscript._Src（支援分段 session）。

    取樣縮圖算相鄰差異；連續 diff < stable_thr 且長度 >= min_len 為一段。
    stable_thr 容許小動畫（吉祥物晃動/游標閃爍），大轉場/載入輪播會切開。
    """
    dur = sum(src.durations)
    prev = None
    diffs: list[tuple[float, float]] = []
    t = 0.0
    while t < dur:
        fr = src.frame_at(t)
        if fr is None:
            t += sample_step
            continue
        g = cv2.cvtColor(cv2.resize(fr, (160, 90)),
                         cv2.COLOR_BGR2GRAY).astype(np.float32)
        if prev is not None:
            diffs.append((t, float(np.mean(np.abs(g - prev))) / 255.0))
        prev = g
        t += sample_step

    segs: list[Segment] = []
    cur = None
    for tt, d in diffs:
        if d < stable_thr:
            if cur is None:
                cur = tt - sample_step
        else:
            if cur is not None and (tt - cur) >= min_len:
                segs.append(Segment(cur, tt))
            cur = None
    if cur is not None and (dur - cur) >= min_len:
        segs.append(Segment(cur, dur))
    return segs


def seg_at(segs: list[Segment], t: float) -> int | None:
    """t 所在段的 index；不在任何段內（轉場中）回傳 None。"""
    for i, s in enumerate(segs):
        if s.start <= t <= s.end:
            return i
    return None


def next_seg_after(segs: list[Segment], t: float) -> int | None:
    """t 之後最近開始的段 index。"""
    for i, s in enumerate(segs):
        if s.start > t:
            return i
    return None


def stable_frame(src, seg: Segment):
    """取該段中最穩定清晰的一幀（段中間 60% 範圍取拉普拉斯最清晰）。"""
    lo = seg.start + 0.2 * seg.length
    hi = seg.start + 0.8 * seg.length
    return src.sharpest_frame((lo + hi) / 2.0, back=(hi - lo) / 2.0,
                              fwd=(hi - lo) / 2.0)
