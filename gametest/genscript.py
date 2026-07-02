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
import numpy as np

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

    def sharpest_frame(self, t: float, back: float = 0.5, fwd: float = 0.1,
                       n: int = 9):
        """在 [t-back, t+fwd] 取數幀，回傳最清晰(拉普拉斯變異最大)那張，避開轉場糊幀。"""
        best, best_s = None, -1.0
        for k in range(n):
            tt = max(0.0, t - back + (back + fwd) * k / (n - 1))
            fr = self.frame_at(tt)
            if fr is None:
                continue
            s = cv2.Laplacian(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY),
                              cv2.CV_64F).var()
            if s > best_s:
                best, best_s = fr, s
        return best


def _element_bbox(frame, cx, cy):
    """偵測被點按鈕的邊界（大小/邊緣）。

    先用「邊緣輪廓」找包住點擊點、且尺寸合理的最小外框（對有邊框/圖示的按鈕最準，
    不會像顏色泛洪只填到按鈕的單一色塊）；找不到再退回顏色泛洪。
    回傳 (x1,y1,x2,y2) 或 None（偵測不可靠時，呼叫端會用固定小框）。
    """
    h, w = frame.shape[:2]

    # ===== 方法1：邊緣輪廓找按鈕外框 =====
    ew, eh = int(0.18 * w), int(0.16 * h)
    ex0, ey0 = max(0, cx - ew), max(0, cy - eh)
    ex1, ey1 = min(w, cx + ew), min(h, cy + eh)
    ewin = frame[ey0:ey1, ex0:ex1]
    if ewin.size:
        lcx, lcy = cx - ex0, cy - ey0
        gray = cv2.cvtColor(ewin, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 50, 150)
        edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=2)
        cnts, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        best, best_area = None, 0.0
        for c in cnts:
            bx, by, bw2, bh2 = cv2.boundingRect(c)
            if not (bx <= lcx <= bx + bw2 and by <= lcy <= by + bh2):
                continue  # 外框必須包住點擊點
            if bw2 < 0.04 * w or bh2 < 0.03 * h:
                continue  # 太小（雜訊/圖示局部）
            if bw2 > 0.34 * w or bh2 > 0.16 * h:
                continue  # 太大（整片背景/多元件）
            # 取按鈕尺寸範圍內「最大」的外框：框住整顆按鈕（含內部圖示/文字），
            # 而非按鈕內某個小圖示的局部輪廓，特徵較完整、比對更穩。
            area = bw2 * bh2
            if area > best_area:
                best_area, best = area, (bx, by, bw2, bh2)
        if best:
            bx, by, bw2, bh2 = best
            pad = 3
            return (ex0 + max(0, bx - pad), ey0 + max(0, by - pad),
                    ex0 + min(ewin.shape[1], bx + bw2 + pad),
                    ey0 + min(ewin.shape[0], by + bh2 + pad))

    # ===== 方法2：顏色泛洪（後援）=====
    ww, wh = int(0.12 * w), int(0.11 * h)      # 搜尋窗
    x0, y0 = max(0, cx - ww), max(0, cy - wh)
    x1, y1 = min(w, cx + ww), min(h, cy + wh)
    win = cv2.GaussianBlur(frame[y0:y1, x0:x1], (5, 5), 0)
    if win.size == 0:
        return None
    mask = np.zeros((win.shape[0] + 2, win.shape[1] + 2), np.uint8)
    seed = (min(win.shape[1] - 1, cx - x0), min(win.shape[0] - 1, cy - y0))
    try:
        cv2.floodFill(win.copy(), mask, seed, 255,
                      loDiff=(16, 16, 16), upDiff=(16, 16, 16),
                      flags=cv2.FLOODFILL_MASK_ONLY | (255 << 8))
    except Exception:
        return None
    ys, xs = np.where(mask[1:-1, 1:-1] > 0)
    if len(xs) < 40:
        return None
    bw, bh = xs.max() - xs.min(), ys.max() - ys.min()
    # 太小(沒抓到) → 不可靠
    if bw < 0.12 * win.shape[1] or bh < 0.12 * win.shape[0]:
        return None
    # 跨多個元件/大背景外漏（相鄰同色元件會連在一起）→ 拒絕，改用固定小框
    if bw > 0.16 * w or bh > 0.11 * h:
        return None
    pad = 4
    return (x0 + max(0, xs.min() - pad), y0 + max(0, ys.min() - pad),
            x0 + min(win.shape[1], xs.max() + pad),
            y0 + min(win.shape[0], ys.max() + pad))


def _crop(frame, nx, ny, w_frac=0.05, h_frac=0.045):
    """裁被點元件：先自動偵測元件邊界；偵測不到才退回較小的固定框（緊貼點擊點）。"""
    h, w = frame.shape[:2]
    cx, cy = int(nx * w), int(ny * h)
    box = _element_bbox(frame, cx, cy)
    if box:
        x1, y1, x2, y2 = box
    else:
        hw, hh = int(w_frac * w), int(h_frac * h)
        x1, y1 = max(0, cx - hw), max(0, cy - hh)
        x2, y2 = min(w, cx + hw), min(h, cy + hh)
    # 純色守門：裁到近純色（如按鈕平面/色帶）→無法穩定比對，會比到畫面上任一同色塊。
    # 對稱往外擴框（中心仍對準點擊點），直到框進足夠紋理或達上限，抓到鄰近可辨識圖案。
    gx, gy = int(0.03 * w), int(0.028 * h)
    for _ in range(6):
        crop = frame[y1:y2, x1:x2]
        if crop.size and float(crop.std()) >= 18.0:
            break
        x1, y1 = max(0, x1 - gx), max(0, y1 - gy)
        x2, y2 = min(w, x2 + gx), min(h, y2 + gy)
    return frame[y1:y2, x1:x2], x1, y1


# taps.json 時間基準(device_uptime)比影片起點快的秒數（screenrecord 啟動延遲）。
# 裁模板/scene 時把 taps 時間往前補這個量，才對齊「實際點擊當下」的影格。
# 這是「量測失敗時的預設值」；實際生成時會 per-影片自動量測（measure_tap_lag）。
TAP_LAG = 0.6


def measure_tap_lag(src: "_Src", taps: list[dict],
                    default: float = TAP_LAG) -> float:
    """自動量測「taps 時間 − 影片實際點擊時刻」的偏移（每支錄影不同）。

    原理：點擊當下按鈕會出現按壓反饋（高亮/灰態/跳轉起點），對每個 tap 在
    [t-1.3, t+0.25] 內掃描「點擊座標局部小塊」的相鄰取樣差異，首次顯著變化
    即影片中的實際點擊時刻；lag = taps_t − 該時刻。取多個 tap 的中位數。
    量不到（無視覺反饋/全程動畫干擾）就回傳 default。
    """
    lags = []
    for tp in taps[:6]:
        if tp.get("kind", "tap") != "tap":
            continue  # swipe 起點會拖動清單，量測易失真
        t, nx, ny = tp["t"], tp["nx"], tp["ny"]
        step = 0.08
        times = [max(0.0, t - 1.3 + k * step) for k in range(int(1.55 / step) + 1)]
        patches = []
        for tt in times:
            fr = src.frame_at(tt)
            if fr is None:
                patches.append(None); continue
            h, w = fr.shape[:2]
            cx, cy = int(nx * w), int(ny * h)
            hw, hh = max(8, int(0.055 * w)), max(8, int(0.05 * h))
            x1, y1 = max(0, cx - hw), max(0, cy - hh)
            x2, y2 = min(w, cx + hw), min(h, cy + hh)
            p = cv2.cvtColor(fr[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
            patches.append(p.astype(np.float32))
        diffs = []   # (時刻, 局部變化量)
        for a, b, tt in zip(patches, patches[1:], times[1:]):
            if a is None or b is None or a.shape != b.shape:
                diffs.append((tt, 0.0)); continue
            diffs.append((tt, float(np.mean(np.abs(a - b)))))
        if not diffs:
            continue
        vals = sorted(d for _, d in diffs)
        base = vals[len(vals) // 2]                    # 中位數當背景動畫基線
        thr = max(6.0, base * 4.0)
        hit = next((tt for tt, d in diffs if d >= thr), None)
        if hit is None:
            continue
        lag = t - hit
        if -0.1 <= lag <= 1.5:                         # 合理範圍才採信
            lags.append(max(0.0, lag))
    if len(lags) >= 2:
        lags.sort()
        return lags[len(lags) // 2]
    return default


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

    # scene 參考（整張畫面）存 refs/<name>/scene_NN.png
    sdir = cfg.assets_dir / "refs" / (out_subdir or name)
    sdir.mkdir(parents=True, exist_ok=True)

    # 時間校正：taps.json 的 t 以 device_uptime 為基準，比影片起點快（screenrecord 啟動延遲）。
    # 每支錄影自動量測偏移（量不到才用預設 TAP_LAG）。不校正的話 video=taps_t 會
    # 裁到「實際點擊後」的轉場/目的地畫面（模板老是抓到下一畫面）。
    lag = measure_tap_lag(src, taps)
    print(f"  [genscript] 時間偏移校正 lag={lag:.2f}s（taps 比影片快）")

    results = []
    for i, tp in enumerate(taps):
        vt = max(0.0, tp["t"] - lag)
        # 取「按壓前一刻」的清晰幀（窗口嚴格在 vt 之前 [vt-0.4, vt-0.04]）：
        # vt 是量測到的按壓反饋起點，之前的影格才是按鈕「常態」外觀（規範：裁常態當模板）。
        frame = src.sharpest_frame(vt, back=0.4, fwd=-0.04)
        if frame is None:
            frame = src.frame_at(vt)
        if frame is None:
            frame = src.frame_at(tp["t"])
        if frame is None:
            continue
        crop, _, _ = _crop(frame, tp["nx"], tp["ny"])
        fn = f"tap{i:02d}.png"
        cv2.imwrite(str(adir / fn), crop)
        scene_fn = f"scene_{i:02d}.png"
        cv2.imwrite(str(sdir / scene_fn), frame)
        results.append((tp, f"{out_subdir or name}/{fn}",
                        f"refs/{out_subdir or name}/{scene_fn}"))
    return results, name


def _tpl_std(cfg: Config, tpl: str) -> float:
    im = cv2.imread(str(cfg.assets_dir / tpl))
    return float(im.std()) if im is not None else 0.0


_KP_SIFT = cv2.SIFT_create() if hasattr(cv2, "SIFT_create") else None


def _tpl_keypoints(cfg: Config, tpl: str) -> int:
    """模板的 SIFT 關鍵點數：用來判斷是否「有可辨識圖案」。
    近純色/平面色帶關鍵點極少 → 不該用 tap_image（會比到畫面上任一同色塊），
    改走 tap_scene（座標＋畫面驗證）。"""
    if _KP_SIFT is None:
        return 999
    im = cv2.imread(str(cfg.assets_dir / tpl), cv2.IMREAD_GRAYSCALE)
    if im is None:
        return 0
    return len(_KP_SIFT.detect(im, None) or [])


def generate_yaml(cfg: Config, source: Path, min_std: float = 16.0) -> tuple[str, str]:
    """從 taps.json 產出確定性腳本 YAML。回傳 (yaml_text, name)。

    - 進場：anchor 等第一個「可辨識」畫面出現才開始（吸收冷啟動+載入）。
    - 模糊/純色（過場載入）的點：std 太低 → 不當按鈕，改為等待通過。
    """
    results, name = crop_tap_templates(cfg, source)
    quals = [_tpl_std(cfg, tpl) for _, tpl, _ in results]
    good = [i for i, q in enumerate(quals) if q >= min_std]
    a_i = good[0] if good else 0
    a_tp, a_tpl, _ = results[a_i]

    lines = [
        f"# 由 taps.json（getevent 實測點擊）確定性生成 — 來源 {Path(source).name}",
        "# 每步點的是影片中實際被點的圖案（tap_image 多尺度比對，跨解析度）。",
        "# anchor：冷啟動後先等第一個可辨識畫面出現才開始比對點擊。",
        f"# 略過的點（過場/載入，無穩定按鈕）：{[i for i, (tp, _, _) in enumerate(results) if i not in good and tp.get('kind', 'tap') != 'swipe']}",
        "",
        f"name: {name}",
        f"description: 由 {Path(source).name} 精確點擊生成",
        "step_delay: 1.0",
        "",
        "anchor:",
        f"  template: {a_tpl}",
        f"  timeout: {min(150, int(a_tp['t'] + 45))}",
        "",
        "steps:",
    ]
    def scene_block(scene_rel, gap=0.0, indent="    "):
        # 每步先確認在對的畫面（穩定 UI 區、寬鬆門檻）才動作。
        # scene-gate timeout 要涵蓋前一步到本步的間隔（載入/過場可能很長，
        # 例如「進入遊戲」後 ~40s 載入頁）：等待只短點一下，其餘交給 scene 輪詢。
        timeout = min(90, max(20, int(gap) + 15))
        return [f"{indent}scene:",
                f"{indent}  template: {scene_rel}",
                f"{indent}  timeout: {timeout}"]

    prev_t = None
    for i, (tp, tpl, scene_rel) in enumerate(results):
        gap = 0.0
        if prev_t is not None:
            gap = tp["t"] - prev_t
            if gap > 1.2:
                lines += ["  - action: wait",
                          f"    name: 等待 {gap:.0f}s",
                          f"    seconds: {min(gap, 6):.1f}", ""]
        prev_t = tp["t"]
        kind = tp.get("kind", "tap")
        # 低紋理過濾（該處無穩定按鈕→跳過）只適用「圖像式點擊」：
        # swipe 是座標式操作、不靠模板比對，即使起點裁到低紋理也必須保留。
        if i not in good and kind != "swipe":
            lines += ["  - action: wait",
                      f"    name: 過場等待 t={tp['t']:.1f}s（該處無穩定按鈕，跳過）",
                      "    seconds: 2.0", ""]
            continue
        to = 30 if i == a_i else 12
        # 關鍵點太少＝無可辨識圖案（純色/色帶）→ tap_image 會亂命中；改 tap_scene（座標＋畫面驗證）
        low_feature = kind in ("tap", "long_press") and _tpl_keypoints(cfg, tpl) < 10
        if low_feature:
            press = "long" if kind == "long_press" else "auto"
            lines += ["  - action: tap_scene",
                      f"    name: 點擊(座標) t={tp['t']:.1f}s〔無可辨識圖案，改座標+畫面驗證〕",
                      f"    x: {tp['nx']}", f"    y: {tp['ny']}",
                      f"    reference: {scene_rel}",
                      "    scene_threshold: 0.6",
                      f"    press: {press}", ""]
        elif kind == "long_press":
            lines += ["  - action: long_press_image",
                      f"    name: 長壓 t={tp['t']:.1f}s",
                      f"    template: {tpl}",
                      f"    duration_ms: {max(400, tp['duration_ms'])}",
                      f"    timeout: {to}"]
            lines += scene_block(scene_rel, gap) + [""]
        elif kind == "swipe":
            # getevent 偶爾在 swipe 抓到異常原始座標 → end_nx/ny 可能爆量；
            # 正規化座標必為 0~1，clamp 以免 adb swipe 滑到螢幕外變無效操作。
            ex = min(1.0, max(0.0, float(tp["end_nx"])))
            ey = min(1.0, max(0.0, float(tp["end_ny"])))
            lines += ["  - action: swipe",
                      f"    name: 滑動 t={tp['t']:.1f}s",
                      f"    x1: {tp['nx']}", f"    y1: {tp['ny']}",
                      f"    x2: {ex}", f"    y2: {ey}",
                      f"    duration_ms: {max(200, tp['duration_ms'])}"]
            lines += scene_block(scene_rel, gap) + [""]
        else:
            lines += ["  - action: tap_image",
                      f"    name: 點擊 t={tp['t']:.1f}s",
                      f"    template: {tpl}",
                      f"    timeout: {to}", "    press: auto"]
            lines += scene_block(scene_rel, gap) + [""]
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
