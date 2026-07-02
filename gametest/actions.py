"""步驟執行器：執行動作，並對點擊步驟做「點擊前後截圖＋比對原影片＋適配偵測」。"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2

from . import compare
from .config import Config
from .device import Device
from .matcher import match_template
from .script_model import Step


@dataclass
class StepResult:
    index: int
    name: str
    action: str
    ok: bool
    critical: bool
    message: str = ""
    score: float | None = None          # 模板比對分數（tap_image/assert 用）
    screenshot: str | None = None       # 主要截圖（動作後）相對檔名
    before_shot: str | None = None      # 點擊前截圖
    ref_shot: str | None = None         # 原影片預期畫面（複製供報告顯示）
    ref_similarity: float | None = None  # 與原影片(點擊前)相似度
    after_similarity: float | None = None  # 與原影片(點擊後)相似度
    bug: bool = False                   # 是否判定為 BUG（需特別列出）
    bug_reason: str = ""
    detection: dict | None = None       # 適配偵測結果（黑屏/掉圖/解析度/黑邊）
    diff_boxes: list | None = None      # 點擊前後顯著差異區塊（正規化 bbox）
    resolved_press: str | None = None   # press:auto 實際生效的觸控（tap/long）
    escalated: bool = False             # 是否從短點自動升級為長壓
    scene_score: float | None = None    # scene-gate：動作前畫面相似度
    scene_ok: bool | None = None        # scene-gate：是否在對的畫面


class StepError(Exception):
    pass


def _template_path(cfg: Config, name: str) -> Path:
    p = Path(name)
    return p if p.is_absolute() else (cfg.assets_dir / name)


def _ref_path(cfg: Config, name: str) -> Path:
    """reference 影格：先找絕對路徑，再找 assets/，最後當作專案相對路徑。"""
    p = Path(name)
    if p.is_absolute():
        return p
    cand = cfg.assets_dir / name
    if cand.exists():
        return cand
    return cfg.root / name


def _save(out_dir: Path, name: str, img) -> str | None:
    try:
        cv2.imwrite(str(out_dir / name), img)
        return name
    except Exception:
        return None


def execute_step(
    device: Device,
    cfg: Config,
    step: Step,
    index: int,
    out_dir: Path,
    delay: float = 0.0,
    expected_size: tuple[int, int] | None = None,
) -> StepResult:
    """執行單一步驟，必要時截圖、比對原影片並做適配偵測。"""
    p = step.params
    ok, msg, score = True, "", None
    bug, bug_reason = False, ""
    ref_sim = after_sim = None
    before_name = ref_name = None
    detection = diff_boxes = None
    before_img = None
    resolved_press = None
    escalated = False
    touch_loc = None          # ("norm", x, y) 或 ("pixel", px, py)，供 auto 升級長壓用
    dur = int(p.get("duration_ms", 800))
    scene_score = scene_ok = None

    # ===== scene-gate：執行動作前先確認在對的畫面（不符就等，等不到則不執行）=====
    if step.scene:
        sref = compare.load_image(_ref_path(cfg, step.scene["template"])) \
            if step.scene.get("template") else None
        if sref is not None:
            thr = float(step.scene.get("threshold", getattr(cfg, "scene_threshold", 0.70)))
            s_to = float(step.scene.get("timeout", 20.0))
            mode = step.scene.get("mode", "bands")
            region = step.scene.get("region")
            deadline = time.time() + s_to
            while True:
                try:
                    cur = device.screencap()
                except Exception:
                    cur = None
                scene_score = compare.scene_similarity(cur, sref, mode=mode, region=region) \
                    if cur is not None else 0.0
                if scene_score >= thr:
                    scene_ok = True
                    break
                if time.time() >= deadline:
                    scene_ok = False
                    break
                time.sleep(0.5)
            if not scene_ok:
                # 畫面不符 → 不執行動作，明確標記（區分於「按鈕沒找到」）
                sr = StepResult(index=index, name=step.name, action=step.action,
                                ok=False, critical=step.critical,
                                message=f"畫面不符：未到達預期畫面（相似度 {scene_score:.2f} < {thr}）",
                                scene_score=scene_score, scene_ok=False)
                try:
                    img = device.screencap()
                    sr.screenshot = _save(out_dir, f"{index:02d}_scene_mismatch.png", img)
                except Exception:
                    pass
                if delay > 0:
                    time.sleep(delay)
                return sr

    # 點擊類動作：先截「點擊前」畫面
    if step.is_click:
        try:
            before_img = device.screencap()
        except Exception:
            before_img = None

    try:
        if step.action == "tap":
            x, y = float(p["x"]), float(p["y"])
            touch_loc = ("norm", x, y)
            if step.press == "long":
                device.long_press(x, y, dur)
            else:
                device.tap(x, y)

        elif step.action == "tap_scene":
            # 先比對「目前畫面 vs 錄影參考畫面」，相符才點同一位置
            x, y = float(p["x"]), float(p["y"])
            touch_loc = ("norm", x, y)
            thr = float(p.get("scene_threshold", 0.65))
            rimg = compare.load_image(_ref_path(cfg, step.reference)) if step.reference else None
            scene = before_img if before_img is not None else device.screencap()
            sim = compare.ssim(scene, rimg) if rimg is not None else 0.0
            ref_sim = sim
            score = sim
            if rimg is None:
                ok = False
                msg = f"tap_scene 需要有效的 reference 畫面：{step.reference}"
            elif sim >= thr:
                if step.press == "long":
                    device.long_press(x, y, dur)
                else:
                    device.tap(x, y)
            else:
                ok = False
                msg = f"畫面與錄影不符（相似度 {sim:.2f} < {thr}），不點擊以免誤觸"

        elif step.action == "long_press":
            device.long_press(float(p["x"]), float(p["y"]),
                              int(p.get("duration_ms", 800)))

        elif step.action == "swipe":
            # 滑動前後截圖比對，驗證是否真的捲動/有效果（避免滑到不可捲區卻誤報成功）
            try:
                pre = device.screencap()
            except Exception:
                pre = None
            device.swipe(float(p["x1"]), float(p["y1"]),
                         float(p["x2"]), float(p["y2"]),
                         int(p.get("duration_ms", 300)))
            time.sleep(0.4)
            try:
                post = device.screencap()
            except Exception:
                post = None
            if pre is not None and post is not None:
                sim = compare.ssim(pre, post)
                score = 1.0 - sim   # 變化量（越大代表越有效果）
                if sim >= 0.96:
                    # 幾乎沒變化：滑動未生效（起點非可捲區／該區無可捲內容）
                    msg = (f"滑動後畫面幾乎無變化（相似度 {sim:.3f}），"
                           f"可能未捲動：起點非可捲區或該清單無可捲內容")

        elif step.action == "wait":
            time.sleep(float(p["seconds"]))

        elif step.action == "input_text":
            device.input_text(str(p["text"]))

        elif step.action == "key":
            device.key(p["keycode"])

        elif step.action == "screenshot":
            pass

        elif step.action in ("tap_image", "long_press_image", "wait_image",
                             "assert_image", "assert_absent"):
            tpl = _template_path(cfg, p["template"])
            region = p.get("region")
            timeout = float(p.get("timeout", 8.0))
            thr_override = p.get("threshold")
            deadline = time.time() + timeout
            result = None
            while True:
                screen = device.screencap()
                result = match_template(screen, tpl, cfg, region=region,
                                        threshold=thr_override)
                if step.action == "assert_absent":
                    if not result.found:
                        break
                else:
                    if result.found:
                        break
                if time.time() >= deadline:
                    break
                time.sleep(0.5)

            score = result.score
            if step.action in ("tap_image", "long_press_image"):
                if result.found and result.center:
                    touch_loc = ("pixel", result.center[0], result.center[1])
                    if step.action == "long_press_image" or step.press == "long":
                        device.long_press_pixel(*result.center, dur)
                    else:
                        device.tap_pixel(*result.center)
                elif scene_ok and p.get("fallback_x") is not None \
                        and p.get("fallback_y") is not None:
                    # 座標後備：scene-gate 已確認在對的畫面、模板卻比不到
                    # → 點錄影實測座標（畫面對了，座標點擊是安全的）
                    fx, fy = float(p["fallback_x"]), float(p["fallback_y"])
                    touch_loc = ("norm", fx, fy)
                    if step.action == "long_press_image" or step.press == "long":
                        device.long_press(fx, fy, dur)
                    else:
                        device.tap(fx, fy)
                    msg = (f"模板未中 (score={result.score:.3f})，"
                           f"畫面已確認→改點錄影座標")
                else:
                    ok = False
                    msg = f"找不到圖片 {p['template']} (score={result.score:.3f})"
            elif step.action == "wait_image":
                ok = result.found
                if not ok:
                    msg = f"等待逾時，未出現 {p['template']} (score={result.score:.3f})"
            elif step.action == "assert_image":
                ok = result.found
                msg = ("斷言通過" if ok else
                       f"斷言失敗：未找到 {p['template']} (score={result.score:.3f})")
            elif step.action == "assert_absent":
                ok = not result.found
                msg = ("斷言通過" if ok else
                       f"斷言失敗：不應出現的 {p['template']} 仍存在 (score={result.score:.3f})")

    except KeyError as e:
        ok = False
        msg = f"步驟參數缺少 {e}"
    except Exception as e:  # noqa: BLE001
        ok = False
        msg = f"執行錯誤: {e}"

    # 動作後截圖（主要截圖）
    after_img = None
    try:
        after_img = device.screencap()
    except Exception:
        after_img = None

    # ===== press:auto 自我修正：短點若無反應，自動升級為長壓並記錄 =====
    if (step.press == "auto" and ok and step.expect_change and touch_loc is not None
            and before_img is not None and after_img is not None):
        if compare.is_no_response(before_img, after_img):
            kind = touch_loc[0]
            if kind == "norm":
                device.long_press(touch_loc[1], touch_loc[2], dur)
            else:
                device.long_press_pixel(touch_loc[1], touch_loc[2], dur)
            time.sleep(0.6)
            try:
                after_img = device.screencap()
            except Exception:
                pass
            escalated = True
            resolved_press = "long"
            msg = (msg + "；" if msg else "") + "短點無反應，已自動改長壓"
        else:
            resolved_press = "tap"

    # ===== until 後置條件：點擊後等「下一畫面」出現；按鈕還在原地就自動補點 =====
    # 根治「點擊被吞」（載入中按鈕未生效）：等不到目標畫面時，若原按鈕仍在→補點（短/長交替）；
    # 若原畫面已離開但目標未確認→軟通過（過場畫面隨機，交給下一步 scene-gate 仲裁）。
    u = p.get("until")
    if u and ok and touch_loc is not None:
        uref = compare.load_image(_ref_path(cfg, u["template"]))
        if uref is not None:
            uthr = float(u.get("threshold", getattr(cfg, "scene_threshold", 0.70)))
            u_deadline = time.time() + float(u.get("timeout", 20.0))
            max_re = int(u.get("retries", 4))
            reclicks = 0
            reached = False
            origin_here = True
            long_next = escalated or step.press == "long" \
                or step.action == "long_press_image"
            last_try = time.time()
            cur = after_img
            while True:
                if cur is not None and compare.scene_similarity(
                        cur, uref, mode="bands") >= uthr:
                    reached = True
                    break
                if time.time() >= u_deadline:
                    break
                # 判斷是否還停在原畫面（點擊沒生效的訊號）
                origin_here = False
                if cur is not None:
                    if step.action in ("tap_image", "long_press_image"):
                        r2 = match_template(cur, tpl, cfg, region=region,
                                            threshold=thr_override)
                        origin_here = bool(r2.found and r2.center)
                        if origin_here and reclicks < max_re and \
                                time.time() - last_try >= 2.5:
                            if long_next:
                                device.long_press_pixel(*r2.center, dur)
                            else:
                                device.tap_pixel(*r2.center)
                            reclicks += 1
                            last_try = time.time()
                            if step.press == "auto":
                                long_next = not long_next
                    elif step.action in ("tap", "tap_scene"):
                        rr = compare.load_image(_ref_path(cfg, step.reference)) \
                            if step.reference else None
                        origin_here = rr is not None and compare.ssim(cur, rr) >= \
                            float(p.get("scene_threshold", 0.65))
                        if origin_here and reclicks < max_re and \
                                time.time() - last_try >= 2.5:
                            if long_next:
                                device.long_press(float(p["x"]), float(p["y"]), dur)
                            else:
                                device.tap(float(p["x"]), float(p["y"]))
                            reclicks += 1
                            last_try = time.time()
                            if step.press == "auto":
                                long_next = not long_next
                time.sleep(0.8)
                try:
                    cur = device.screencap()
                except Exception:
                    cur = None
            if cur is not None:
                after_img = cur
            if reached:
                if reclicks:
                    msg = (msg + "；" if msg else "") + \
                        f"until 已到達下一畫面（補點 {reclicks} 次）"
            elif not origin_here:
                # 已離開原畫面（點擊有效），只是目標畫面沒確認到（過場隨機）→ 軟通過
                msg = (msg + "；" if msg else "") + \
                    "已離開原畫面但未確認到達 until 目標（交由下一步 scene-gate 仲裁）"
            else:
                ok = False
                msg = (msg + "；" if msg else "") + \
                    f"點擊無效：補點 {reclicks} 次仍停在原畫面（until 未到達）"

    # ===== 適配偵測（黑屏/掉圖/解析度/黑邊）：對動作後畫面 =====
    detect_img = after_img if after_img is not None else before_img
    if detect_img is not None:
        det = compare.detect_black_solid_missing(detect_img)
        if expected_size:
            compare.check_resolution_letterbox(detect_img, det, expected_size)
        detection = asdict(det)
        if det.has_issue:
            bug = True
            bug_reason = "；".join(det.notes) or "適配偵測異常"

    # ===== 與原影片比對 + 點擊無反應（點擊步驟）=====
    if step.is_click and before_img is not None:
        # 點擊前 vs 原影片預期畫面
        if step.reference:
            rimg = compare.load_image(_ref_path(cfg, step.reference))
            if rimg is not None:
                ref_sim = compare.ssim(before_img, rimg)
                ref_name = _save(out_dir, f"{index:02d}_ref.png", rimg)
                if ref_sim < compare.SIMILARITY_WARN:
                    bug = True
                    bug_reason = (bug_reason + "；" if bug_reason else "") + \
                        f"點擊前畫面與原影片差異過大 (相似度 {ref_sim:.2f})"
        # 點擊前後差異 / 無反應
        if after_img is not None:
            diff_boxes = compare.diff_regions(before_img, after_img)
            if step.expect_change and compare.is_no_response(before_img, after_img):
                bug = True
                bug_reason = (bug_reason + "；" if bug_reason else "") + \
                    "點擊後畫面無變化（疑似按鈕無反應）"
            # 點擊後 vs 原影片預期結果畫面
            if step.reference_after:
                raimg = compare.load_image(_ref_path(cfg, step.reference_after))
                if raimg is not None:
                    after_sim = compare.ssim(after_img, raimg)
                    if after_sim < compare.SIMILARITY_WARN:
                        bug = True
                        bug_reason = (bug_reason + "；" if bug_reason else "") + \
                            f"點擊後畫面與預期不符 (相似度 {after_sim:.2f})"
        before_name = _save(out_dir, f"{index:02d}_before.png", before_img)

    # 主要截圖
    shot_name = None
    need_shot = cfg.screenshot_every_step or (not ok) or bug or step.action == "screenshot"
    if need_shot and after_img is not None:
        shot_name = _save(out_dir, f"{index:02d}_{step.action}_"
                          f"{'ok' if ok else 'fail'}.png", after_img)

    if delay > 0:
        time.sleep(delay)

    return StepResult(
        index=index, name=step.name, action=step.action,
        ok=ok, critical=step.critical, message=msg, score=score,
        screenshot=shot_name, before_shot=before_name, ref_shot=ref_name,
        ref_similarity=ref_sim, after_similarity=after_sim,
        bug=bug, bug_reason=bug_reason, detection=detection, diff_boxes=diff_boxes,
        resolved_press=resolved_press, escalated=escalated,
        scene_score=scene_score, scene_ok=scene_ok,
    )
