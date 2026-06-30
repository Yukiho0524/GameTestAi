"""步驟執行器：將 Step 轉成對 Device 的實際操作，並回傳結果。"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import cv2

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
    score: float | None = None
    screenshot: str | None = None  # 相對檔名


class StepError(Exception):
    pass


def _template_path(cfg: Config, name: str) -> Path:
    p = Path(name)
    return p if p.is_absolute() else (cfg.assets_dir / name)


def execute_step(
    device: Device,
    cfg: Config,
    step: Step,
    index: int,
    out_dir: Path,
    delay: float = 0.0,
) -> StepResult:
    """執行單一步驟，必要時截圖到 out_dir。"""
    p = step.params
    ok = True
    msg = ""
    score = None

    try:
        if step.action == "tap":
            device.tap(float(p["x"]), float(p["y"]))

        elif step.action == "swipe":
            device.swipe(float(p["x1"]), float(p["y1"]),
                         float(p["x2"]), float(p["y2"]),
                         int(p.get("duration_ms", 300)))

        elif step.action == "wait":
            time.sleep(float(p["seconds"]))

        elif step.action == "input_text":
            device.input_text(str(p["text"]))

        elif step.action == "key":
            device.key(p["keycode"])

        elif step.action == "screenshot":
            pass  # 統一在最後截圖

        elif step.action in ("tap_image", "wait_image", "assert_image", "assert_absent"):
            tpl = _template_path(cfg, p["template"])
            region = p.get("region")
            timeout = float(p.get("timeout", 8.0))
            interval = 0.5
            deadline = time.time() + timeout
            result = None
            while True:
                screen = device.screencap()
                result = match_template(screen, tpl, cfg, region=region)
                if step.action == "assert_absent":
                    # 不存在才算成功 → 找到就繼續等到消失
                    if not result.found:
                        break
                else:
                    if result.found:
                        break
                if time.time() >= deadline:
                    break
                time.sleep(interval)

            score = result.score
            if step.action == "tap_image":
                if result.found and result.center:
                    device.tap_pixel(*result.center)
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
    except Exception as e:  # noqa: BLE001 — 單步錯誤不應中斷整輪
        ok = False
        msg = f"執行錯誤: {e}"

    # 截圖
    shot_name = None
    need_shot = cfg.screenshot_every_step or (not ok) or step.action == "screenshot"
    if need_shot:
        try:
            img = device.screencap()
            shot_name = f"{index:02d}_{step.action}_{'ok' if ok else 'fail'}.png"
            cv2.imwrite(str(out_dir / shot_name), img)
        except Exception:
            shot_name = None

    # 預設步驟停頓
    if delay > 0:
        time.sleep(delay)

    return StepResult(
        index=index, name=step.name, action=step.action,
        ok=ok, critical=step.critical, message=msg,
        score=score, screenshot=shot_name,
    )
