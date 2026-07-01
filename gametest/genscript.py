"""確定性腳本生成：直接用 taps.json 的精確點擊座標，在該時刻的影格上以點擊點為中心
裁出被點的圖案 → 產生 tap_image（長壓→long_press_image、滑動→swipe）。不靠 AI 猜座標。

這是「點到影片中實際點的按鈕」最可靠的路：點擊位置是 getevent 輸入層實測，
模板是那一刻畫面上該位置的圖案；runtime 用多尺度比對在當前畫面找到它再點中心。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import cv2

from .config import Config
from .video import session_parts, taps_json_for


@dataclass
class _Src:
    """把單檔或分段 session 抽象成「依全域時間取影格」。"""
    parts: list[Path]
    fps: list[float]
    durations: list[float]

    @classmethod
    def open(cls, source: Path):
        parts = session_parts(source) or [source]
        fps, durs = [], []
        for p in parts:
            cap = cv2.VideoCapture(str(p))
            f = cap.get(cv2.CAP_PROP_FPS) or 30.0
            n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            cap.release()
            fps.append(f); durs.append(n / f if f else 0)
        return cls(parts, fps, durs)

    def frame_at(self, t: float):
        """取全域時間 t（秒）的影格 BGR。"""
        acc = 0.0
        for p, f, d in zip(self.parts, self.fps, self.durations):
            if t <= acc + d or p is self.parts[-1]:
                local = max(0.0, t - acc)
                cap = cv2.VideoCapture(str(p))
                cap.set(cv2.CAP_PROP_POS_FRAMES, int(local * f))
                ok, frame = cap.read()
                cap.release()
                return frame if ok else None
            acc += d
        return None


def _crop(frame, nx, ny, w_frac=0.11, h_frac=0.09):
    """以 (nx,ny) 為中心裁一塊（涵蓋被點元件）。回傳 (crop, 左上x, 左上y)。"""
    h, w = frame.shape[:2]
    cx, cy = int(nx * w), int(ny * h)
    hw, hh = int(w_frac * w), int(h_frac * h)
    x1, y1 = max(0, cx - hw), max(0, cy - hh)
    x2, y2 = min(w, cx + hw), min(h, cy + hh)
    return frame[y1:y2, x1:x2], x1, y1


def crop_tap_templates(cfg: Config, source: Path, out_subdir: str | None = None):
    """對 taps.json 每筆裁模板存 assets/<name>/tapNN.png。回傳 [(tap, template相對路徑)]。"""
    source = Path(source)
    name = source.stem if source.is_file() else source.name
    tj = taps_json_for(source)
    if not tj:
        raise FileNotFoundError(f"找不到 taps.json：{source}")
    taps = json.loads(tj.read_text(encoding="utf-8"))
    src = _Src.open(source)
    adir = cfg.assets_dir / (out_subdir or name)
    adir.mkdir(parents=True, exist_ok=True)

    results = []
    for i, tp in enumerate(taps):
        # 取點擊前一瞬間的影格（-0.15s，避免動畫/轉場）
        frame = src.frame_at(max(0.0, tp["t"] - 0.15))
        if frame is None:
            frame = src.frame_at(tp["t"])
        if frame is None:
            continue
        crop, _, _ = _crop(frame, tp["nx"], tp["ny"])
        fn = f"tap{i:02d}.png"
        cv2.imwrite(str(adir / fn), crop)
        results.append((tp, f"{out_subdir or name}/{fn}"))
    return results, name


def generate_yaml(cfg: Config, source: Path) -> tuple[str, str]:
    """從 taps.json 產出確定性腳本 YAML。回傳 (yaml_text, name)。"""
    results, name = crop_tap_templates(cfg, source)
    lines = [
        f"# 由 taps.json（getevent 實測點擊）確定性生成 — 來源 {Path(source).name}",
        "# 每步點的是影片中實際被點的圖案（tap_image 多尺度比對，跨解析度）。",
        "# 進場/等待/斷言可再補；座標型步驟一律避免。",
        "",
        f"name: {name}",
        f"description: 由 {Path(source).name} 精確點擊生成",
        "step_delay: 1.0",
        "",
        "steps:",
    ]
    prev_t = None
    for tp, tpl in results:
        # 依點擊間隔補等待
        if prev_t is not None:
            gap = tp["t"] - prev_t
            if gap > 1.2:
                lines += ["  - action: wait",
                          f"    name: 等待 {gap:.0f}s",
                          f"    seconds: {min(gap, 8):.1f}", ""]
        prev_t = tp["t"]
        kind = tp.get("kind", "tap")
        if kind == "long_press":
            lines += ["  - action: long_press_image",
                      f"    name: 長壓 t={tp['t']:.1f}s",
                      f"    template: {tpl}",
                      f"    duration_ms: {max(400, tp['duration_ms'])}",
                      "    timeout: 12", ""]
        elif kind == "swipe":
            lines += ["  - action: swipe",
                      f"    name: 滑動 t={tp['t']:.1f}s",
                      f"    x1: {tp['nx']}", f"    y1: {tp['ny']}",
                      f"    x2: {tp['end_nx']}", f"    y2: {tp['end_ny']}",
                      f"    duration_ms: {max(200, tp['duration_ms'])}", ""]
        else:
            lines += ["  - action: tap_image",
                      f"    name: 點擊 t={tp['t']:.1f}s",
                      f"    template: {tpl}",
                      "    timeout: 12", "    press: auto", ""]
    return "\n".join(lines), name


def has_taps(source: Path) -> bool:
    tj = taps_json_for(Path(source))
    if not tj:
        return False
    try:
        return len(json.loads(tj.read_text(encoding="utf-8"))) > 0
    except Exception:
        return False


def generate_and_push(cfg: Config, source: Path, push: bool = True):
    """確定性生成腳本（含裁模板）+ 落檔登記 + 推 git（含 assets）。回傳 (path, msg)。"""
    from . import scriptgen as SG
    source = Path(source)
    yaml_text, _ = generate_yaml(cfg, source)
    key = source.stem if source.is_file() else source.name
    name = SG.next_script_name(cfg)
    path = SG.save_script(cfg, yaml_text, video_name=key, name=name)
    msg = ""
    if push:
        idx = cfg.scripts_dir / ".video_index.json"
        assets = cfg.assets_dir / key
        msg = SG.autopush(cfg, [path, idx, assets],
                          f"確定性生成測試腳本 {name}（來源 {source.name}，taps.json 精確點擊）")
    return path, msg
