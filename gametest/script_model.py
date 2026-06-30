"""測試腳本資料模型。腳本用 YAML 撰寫，座標一律正規化 (0~1) 以跨解析度。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# 支援的動作型別
ACTIONS = {
    "tap",            # 點擊正規化座標       params: x, y
    "tap_image",      # 找到圖片後點擊       params: template, [region], [timeout]
    "long_press",     # 長壓座標            params: x, y, [duration_ms=800]
    "long_press_image",  # 找到圖片後長壓    params: template, [duration_ms=800], [timeout], [region]
    "swipe",          # 滑動               params: x1,y1,x2,y2,[duration_ms]
    "wait",           # 等待秒數            params: seconds
    "wait_image",     # 等待圖片出現        params: template, [timeout], [region]
    "assert_image",   # 斷言圖片存在(判定成敗) params: template, [region]
    "assert_absent",  # 斷言圖片不存在      params: template, [region]
    "input_text",     # 輸入文字（英數；中文需 ADBKeyboard）params: text
    "key",            # 按鍵               params: keycode | "back" | "home"
    "screenshot",     # 主動截圖            params: -（用 name 命名）
}


# 會「觸控」的動作（需做觸控前後截圖與適配比對）
CLICK_ACTIONS = {"tap", "tap_image", "long_press", "long_press_image"}


@dataclass
class Step:
    action: str
    name: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    # 此步驟是否計入成敗判定（assert_* 預設 True，其餘 False）
    critical: bool = False
    # 適配比對用：原影片中此步驟「點擊前」的預期畫面（相對 assets/ 或絕對路徑）
    reference: str | None = None
    # 原影片中「點擊後」的預期畫面（選用，用來判定點擊後是否跑掉）
    reference_after: str | None = None
    # 點擊後畫面是否應該改變（用於「點擊無反應」偵測），click 動作預設 True
    expect_change: bool | None = None
    # 觸控方式（僅 tap/tap_image 有意義）：
    #   "tap"=短點(預設)  "long"=長壓  "auto"=先短點，無反應自動改長壓並記錄實際結果
    press: str = "tap"

    def __post_init__(self):
        if self.action not in ACTIONS:
            raise ValueError(f"未知動作 '{self.action}'，可用: {sorted(ACTIONS)}")
        if not self.name:
            self.name = self.action
        if self.expect_change is None:
            self.expect_change = self.action in CLICK_ACTIONS
        if self.press not in ("tap", "long", "auto"):
            raise ValueError(f"press 只能是 tap/long/auto，得到 '{self.press}'")

    @property
    def is_click(self) -> bool:
        return self.action in CLICK_ACTIONS


@dataclass
class TestScript:
    name: str
    description: str
    steps: list[Step]
    # 每步動作後的預設停頓（秒）
    step_delay: float = 0.8
    # 起始狀態同步：冷啟動後先等此畫面出現才開始跑步驟（第 3 點方案 A）
    #   {"template": str, "timeout": float, "region": [x1,y1,x2,y2]}
    anchor: dict[str, Any] | None = None
    # 前置導航腳本：先跑這支把 App 從 launch 帶到 baseline（第 3 點方案 B）
    #   相對 scripts/ 的檔名，或絕對路徑
    prelude: str | None = None
    source: Path | None = None

    @staticmethod
    def _parse_steps(raw_steps: list[dict]) -> list[Step]:
        steps = []
        for raw in raw_steps:
            raw = dict(raw)  # 不破壞原 dict
            action = raw.pop("action")
            name = raw.pop("name", "")
            critical = raw.pop("critical", action in ("assert_image", "assert_absent"))
            reference = raw.pop("reference", None)
            reference_after = raw.pop("reference_after", None)
            expect_change = raw.pop("expect_change", None)
            press = raw.pop("press", "tap")
            steps.append(Step(action=action, name=name, params=raw, critical=critical,
                              reference=reference, reference_after=reference_after,
                              expect_change=expect_change, press=press))
        return steps

    @classmethod
    def load(cls, path: str | Path) -> "TestScript":
        path = Path(path)
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        steps = cls._parse_steps(data.get("steps", []))
        if not steps:
            raise ValueError(f"腳本 {path} 沒有任何步驟")
        return cls(
            name=data.get("name", path.stem),
            description=data.get("description", ""),
            steps=steps,
            step_delay=float(data.get("step_delay", 0.8)),
            anchor=data.get("anchor"),
            prelude=data.get("prelude"),
            source=path,
        )

    def resolve_prelude(self) -> Path | None:
        """把 prelude 檔名解析成絕對路徑（相對 scripts/ 目錄）。"""
        if not self.prelude:
            return None
        p = Path(self.prelude)
        if p.is_absolute():
            return p
        base = self.source.parent if self.source else Path("scripts")
        return base / self.prelude
