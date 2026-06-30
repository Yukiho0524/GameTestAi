"""測試編排：每個解析度 × N 次重複，逐步截圖並記錄結果。"""
from __future__ import annotations

import time
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from .actions import StepResult, execute_step
from .config import Config, Resolution
from .device import Device
from .script_model import Step, TestScript


@dataclass
class RunResult:
    run_index: int
    resolution: str
    passed: bool
    error: str = ""
    steps: list[StepResult] = field(default_factory=list)
    screenshot_dir: str = ""
    duration_sec: float = 0.0
    crashes: list[str] = field(default_factory=list)   # logcat 崩潰/ANR

    @property
    def bug_count(self) -> int:
        return sum(1 for s in self.steps if s.bug)

    @property
    def bug_steps(self):
        return [s for s in self.steps if s.bug]


@dataclass
class Suite:
    script_name: str
    started_at: str
    resolutions: list[str]
    repeat: int
    runs: list[RunResult] = field(default_factory=list)

    def success_rate(self, resolution: str | None = None) -> float:
        runs = self.runs if resolution is None else [r for r in self.runs if r.resolution == resolution]
        if not runs:
            return 0.0
        return sum(1 for r in runs if r.passed) / len(runs) * 100.0


def _exec_steps(device, cfg, steps, result, out_dir, delay,
                base_index=0, expected_size=None):
    """執行一串步驟，結果累加進 result。回傳是否全部關鍵步驟通過。"""
    ok_all = True
    for j, step in enumerate(steps):
        sr = execute_step(device, cfg, step, base_index + j, out_dir,
                          delay=delay, expected_size=expected_size)
        result.steps.append(sr)
        if sr.critical and not sr.ok:
            result.passed = False
            ok_all = False
        # 適配 BUG（圖歪/掉圖/無反應/點擊後跑掉）也讓該輪判失敗
        if sr.bug:
            result.passed = False
    return ok_all


def _run_once(device: Device, cfg: Config, script: TestScript,
              run_index: int, res: Resolution, out_dir: Path) -> RunResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    result = RunResult(run_index=run_index, resolution=res.label,
                       passed=True, screenshot_dir=str(out_dir))
    exp = (res.width, res.height)
    t0 = time.time()
    try:
        if cfg.restart_app_each_run:
            device.stop_app()
            time.sleep(1.5)
        if device.adb:
            device.adb.logcat_clear()        # 清 log，待會掃這輪的崩潰
        device.start_app()
        time.sleep(2.0)

        idx = 0

        # === 方案 B：前置導航腳本，把 App 從 launch 帶到 baseline ===
        prelude_path = script.resolve_prelude()
        if prelude_path:
            if not prelude_path.exists():
                raise FileNotFoundError(f"找不到前置導航腳本 prelude: {prelude_path}")
            prelude = TestScript.load(prelude_path)
            pre_ok = _exec_steps(device, cfg, prelude.steps, result, out_dir,
                                 prelude.step_delay, base_index=idx, expected_size=exp)
            idx += len(prelude.steps)
            if not pre_ok:
                result.error = "前置導航腳本失敗，無法到達 baseline，略過後續步驟"
                result.duration_sec = round(time.time() - t0, 2)
                return result

        # === 方案 A：anchor 同步，等起始畫面出現才開始跑主步驟 ===
        if script.anchor:
            anchor_step = Step(
                action="wait_image",
                name="anchor 起始狀態同步",
                params={
                    "template": script.anchor["template"],
                    "timeout": float(script.anchor.get("timeout", 30)),
                    **({"region": script.anchor["region"]}
                       if script.anchor.get("region") else {}),
                },
                critical=True,
            )
            sr = execute_step(device, cfg, anchor_step, idx, out_dir,
                              delay=0.0, expected_size=exp)
            result.steps.append(sr)
            idx += 1
            if not sr.ok:
                result.passed = False
                result.error = ("起始狀態錯位：冷啟動後未在時限內到達錄影的起始畫面。"
                                "請從 App 啟動點重錄，或設定 prelude 導航腳本。")
                result.duration_sec = round(time.time() - t0, 2)
                return result

        # === 主步驟 ===
        _exec_steps(device, cfg, script.steps, result, out_dir,
                    script.step_delay, base_index=idx, expected_size=exp)
    except Exception as e:  # noqa: BLE001
        result.passed = False
        result.error = f"{e}\n{traceback.format_exc()}"

    # === App 崩潰 / ANR 掃描 ===
    try:
        if device.adb:
            crashes = device.adb.logcat_scan_crashes(cfg.package_name)
            if crashes:
                result.crashes = crashes
                result.passed = False
    except Exception:
        pass

    result.duration_sec = round(time.time() - t0, 2)
    return result


def run_suite(cfg: Config, script: TestScript) -> tuple[Suite, Path]:
    """執行整個測試套件，回傳 (Suite, 結果根目錄)。"""
    cfg.ensure_dirs()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    root = cfg.results_dir / f"{script.name}_{stamp}"
    root.mkdir(parents=True, exist_ok=True)

    suite = Suite(
        script_name=script.name,
        started_at=stamp,
        resolutions=[r.label for r in cfg.resolutions],
        repeat=cfg.repeat,
    )

    device = Device(cfg)
    try:
        for res in cfg.resolutions:
            print(f"\n=== 解析度 {res.label} (dpi={res.dpi}) ===")
            device.prepare(res)
            for n in range(1, cfg.repeat + 1):
                out_dir = root / res.label / f"run_{n:02d}"
                print(f"  ▶ 第 {n}/{cfg.repeat} 次 ...", end=" ", flush=True)
                rr = _run_once(device, cfg, script, n, res, out_dir)
                suite.runs.append(rr)
                print("PASS" if rr.passed else f"FAIL ({rr.error[:40] or '斷言未過'})")
            device.shutdown()
            time.sleep(2)
    finally:
        device.shutdown()

    return suite, root
