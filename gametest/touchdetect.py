"""觸控標記偵測：從開啟雷電「顯示點按操作」的錄影中，自動抓出每個點擊的
時間、正規化座標、持續時間（長壓判定），供生成腳本時精準裁出被點的圖案。

原理：標記是「短暫出現」的圓形亮塊。對每幀與約 baseline_lag 幀前的畫面做差異，
找出新出現的圓形 blob → 視為觸控點。場景轉場是大面積非圓形變化，會被
面積/圓度過濾掉。

⚠️ 標記的顏色/大小因雷電/Android 版本而異，預設參數是通用估計，需用一支
   實際開了標記的錄影、配合 --debug 偵錯圖校準（調 MarkerParams）。
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .config import Config


@dataclass
class MarkerParams:
    diff_thresh: int = 30        # 與 baseline 的亮度差門檻
    baseline_lag: int = 8        # 取幾幀前當 baseline（約 0.25s @30fps）
    min_circularity: float = 0.55
    r_min_frac: float = 0.008    # 標記半徑下限（佔畫面寬比例）
    r_max_frac: float = 0.06     # 標記半徑上限
    cluster_gap: int = 6         # 偵測點間隔幾幀內視為同一次點擊
    long_press_ms: int = 400


@dataclass
class Tap:
    t_start: float
    t_end: float
    x: float                     # 正規化 0~1
    y: float
    @property
    def duration_ms(self) -> int:
        return int((self.t_end - self.t_start) * 1000)
    def is_long(self, p: MarkerParams) -> bool:
        return self.duration_ms >= p.long_press_ms


def _find_marker(cur_gray, base_gray, w, h, p: MarkerParams):
    """回傳當前幀的觸控標記中心 (px,py) 或 None。"""
    diff = cv2.absdiff(cur_gray, base_gray)
    _, mask = cv2.threshold(diff, p.diff_thresh, 255, cv2.THRESH_BINARY)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    r_min, r_max = p.r_min_frac * w, p.r_max_frac * w
    best = None
    for c in cnts:
        area = cv2.contourArea(c)
        if area < np.pi * r_min * r_min * 0.4:
            continue
        peri = cv2.arcLength(c, True)
        if peri == 0:
            continue
        circ = 4 * np.pi * area / (peri * peri)
        if circ < p.min_circularity:
            continue
        (cx, cy), r = cv2.minEnclosingCircle(c)
        if not (r_min <= r <= r_max):
            continue
        score = circ
        if best is None or score > best[0]:
            best = (score, cx, cy)
    return (best[1], best[2]) if best else None


def detect_taps_in_video(cfg: Config, video_path, params: MarkerParams | None = None,
                         debug: bool = False) -> list[Tap]:
    params = params or MarkerParams()
    video_path = Path(video_path)
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"無法開啟影片：{video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    dbg_dir = None
    if debug:
        dbg_dir = cfg.frames_dir / f"{video_path.stem}_taps_debug"
        dbg_dir.mkdir(parents=True, exist_ok=True)

    buf: deque = deque(maxlen=params.baseline_lag + 1)
    detections: list[tuple[int, float, float]] = []  # (frame_idx, px, py)
    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        buf.append(gray)
        if len(buf) > params.baseline_lag:
            m = _find_marker(gray, buf[0], w, h, params)
            if m:
                detections.append((idx, m[0], m[1]))
                if dbg_dir is not None:
                    vis = frame.copy()
                    cv2.circle(vis, (int(m[0]), int(m[1])), 18, (0, 0, 255), 3)
                    cv2.imwrite(str(dbg_dir / f"f{idx:05d}.png"), vis)
        idx += 1
    cap.release()

    # 時間聚類成離散點擊
    taps: list[Tap] = []
    if detections:
        group = [detections[0]]
        for d in detections[1:]:
            if d[0] - group[-1][0] <= params.cluster_gap:
                group.append(d)
            else:
                taps.append(_group_to_tap(group, fps, w, h))
                group = [d]
        taps.append(_group_to_tap(group, fps, w, h))

    if debug:
        print(f"偵測到 {len(taps)} 次點擊（影片 {w}x{h} @ {fps:.0f}fps）")
        for i, t in enumerate(taps):
            kind = "長壓" if t.is_long(params) else "點擊"
            print(f"  #{i} {kind} t={t.t_start:.2f}s 時長{t.duration_ms}ms "
                  f"位置=({t.x:.3f},{t.y:.3f})")
        if dbg_dir:
            print(f"偵錯圖：{dbg_dir}")
    return taps


def _group_to_tap(group, fps, w, h) -> Tap:
    xs = np.median([g[1] for g in group])
    ys = np.median([g[2] for g in group])
    return Tap(t_start=group[0][0] / fps, t_end=group[-1][0] / fps,
               x=float(xs / w), y=float(ys / h))
