"""影片抽幀：把上傳的遊戲錄影切成圖片，供 AI 分析以產生測試腳本。"""
from __future__ import annotations

from pathlib import Path

import cv2

from .config import Config


def extract_frames(
    cfg: Config,
    video_path: str | Path,
    every_sec: float = 1.0,
    max_frames: int = 0,
) -> list[Path]:
    """每 every_sec 秒抽一張，輸出到 cfg.frames_dir/<影片名>/。

    回傳產生的圖片路徑清單。max_frames>0 時限制張數。
    """
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"找不到影片: {video_path}")

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"無法開啟影片（缺編解碼器？）: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    step = max(1, int(round(fps * every_sec)))

    out_dir = cfg.frames_dir / video_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    saved: list[Path] = []
    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % step == 0:
            ts = idx / fps
            name = out_dir / f"frame_{len(saved):04d}_t{ts:06.1f}s.png"
            cv2.imwrite(str(name), frame)
            saved.append(name)
            if max_frames and len(saved) >= max_frames:
                break
        idx += 1

    cap.release()
    print(f"抽出 {len(saved)} 張（來源 {total} 幀 @ {fps:.1f}fps），輸出至 {out_dir}")
    return saved
