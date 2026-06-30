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
from .script_model import TestScript


@dataclass
class RunResult:
    run_index: int
    resolution: str
    passed: bool
    error: str = ""
    steps: list[StepResult] = field(default_factory=list)
    screenshot_dir: str = ""
    duration_sec: float = 0.0


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


def _run_once(device: Device, cfg: Config, script: TestScript,
              run_index: int, res: Resolution, out_dir: Path) -> RunResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    result = RunResult(run_index=run_index, resolution=res.label,
                       passed=True, screenshot_dir=str(out_dir))
    t0 = time.time()
    try:
        if cfg.restart_app_each_run:
            device.stop_app()
            time.sleep(1.5)
        device.start_app()
        time.sleep(2.0)

        for i, step in enumerate(script.steps):
            sr = execute_step(device, cfg, step, i, out_dir, delay=script.step_delay)
            result.steps.append(sr)
            # 關鍵步驟失敗 → 整輪判失敗，但仍執行剩餘步驟以收集畫面
            if sr.critical and not sr.ok:
                result.passed = False
    except Exception as e:  # noqa: BLE001
        result.passed = False
        result.error = f"{e}\n{traceback.format_exc()}"
    result.duration_sec = round(time.time() - t0, 2)

    # 若沒有任何關鍵步驟，至少要有一個 assert 才算有效；否則以「無錯誤」為準
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
