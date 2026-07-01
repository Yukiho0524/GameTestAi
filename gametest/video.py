"""影片抽幀：把上傳的遊戲錄影切成圖片，供 AI 分析以產生測試腳本。"""
from __future__ import annotations

import json
from pathlib import Path

import cv2

from .config import Config


def session_parts(source: Path) -> list[Path] | None:
    """若 source 是分段 session 資料夾（含 session.json），回傳有序片段路徑；否則 None。"""
    source = Path(source)
    manifest = source / "session.json"
    if source.is_dir() and manifest.exists():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        return [source / p for p in data.get("parts", [])]
    return None


def taps_json_for(source: Path) -> Path | None:
    """回傳來源對應的 taps.json（session 資料夾內或 <影片>.taps.json）。"""
    source = Path(source)
    if source.is_dir():
        p = source / "taps.json"
    else:
        p = Path(str(source) + ".taps.json")
    return p if p.exists() else None


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

    # 分段 session：依序抽所有片段，時間軸連續
    parts = session_parts(video_path)
    if parts is not None:
        return _extract_session(cfg, video_path, parts, every_sec, max_frames)

    out_dir = cfg.frames_dir / video_path.stem
    saved = _extract_one(video_path, out_dir, every_sec, 0.0, 0, max_frames)
    print(f"抽出 {len(saved)} 張，輸出至 {out_dir}")
    return saved


def _extract_one(video_path: Path, out_dir: Path, every_sec: float,
                 t_offset: float, seq_start: int, max_frames: int) -> list[Path]:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"無法開啟影片（缺編解碼器？）: {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, int(round(fps * every_sec)))
    out_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % step == 0:
            ts = t_offset + idx / fps
            seq = seq_start + len(saved)
            name = out_dir / f"frame_{seq:04d}_t{ts:06.1f}s.png"
            cv2.imwrite(str(name), frame)
            saved.append(name)
            if max_frames and len(saved) >= max_frames:
                break
        idx += 1
    cap.release()
    return saved


def _video_duration(video_path: Path) -> float:
    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    cap.release()
    return total / fps if fps else 0.0


def _extract_session(cfg: Config, session_dir: Path, parts: list[Path],
                     every_sec: float, max_frames: int) -> list[Path]:
    """跨多段連續抽幀：時間軸與影格序號連續（part2 接在 part1 之後）。"""
    out_dir = cfg.frames_dir / session_dir.name
    out_dir.mkdir(parents=True, exist_ok=True)
    all_saved: list[Path] = []
    t_offset = 0.0
    for part in parts:
        if not part.exists():
            continue
        saved = _extract_one(part, out_dir, every_sec, t_offset,
                             len(all_saved), max_frames)
        all_saved.extend(saved)
        t_offset += _video_duration(part)
        if max_frames and len(all_saved) >= max_frames:
            break
    print(f"抽出 {len(all_saved)} 張（session {len(parts)} 段），輸出至 {out_dir}")
    return all_saved
