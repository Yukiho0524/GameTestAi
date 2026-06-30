"""測試腳本資料模型。腳本用 YAML 撰寫，座標一律正規化 (0~1) 以跨解析度。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# 支援的動作型別
ACTIONS = {
    "tap",          # 點擊正規化座標     params: x, y
    "tap_image",    # 找到圖片後點擊     params: template, [region], [timeout]
    "swipe",        # 滑動               params: x1,y1,x2,y2,[duration_ms]
    "wait",         # 等待秒數           params: seconds
    "wait_image",   # 等待圖片出現       params: template, [timeout], [region]
    "assert_image", # 斷言圖片存在(判定成敗) params: template, [region]
    "assert_absent",# 斷言圖片不存在     params: template, [region]
    "input_text",   # 輸入文字           params: text
    "key",          # 按鍵               params: keycode | "back" | "home"
    "screenshot",   # 主動截圖           params: -（用 name 命名）
}


@dataclass
class Step:
    action: str
    name: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    # 此步驟是否計入成敗判定（assert_* 預設 True，其餘 False）
    critical: bool = False

    def __post_init__(self):
        if self.action not in ACTIONS:
            raise ValueError(f"未知動作 '{self.action}'，可用: {sorted(ACTIONS)}")
        if not self.name:
            self.name = self.action


@dataclass
class TestScript:
    name: str
    description: str
    steps: list[Step]
    # 每步動作後的預設停頓（秒）
    step_delay: float = 0.8
    source: Path | None = None

    @classmethod
    def load(cls, path: str | Path) -> "TestScript":
        path = Path(path)
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        steps = []
        for raw in data.get("steps", []):
            action = raw.pop("action")
            name = raw.pop("name", "")
            critical = raw.pop("critical", action in ("assert_image", "assert_absent"))
            steps.append(Step(action=action, name=name, params=raw, critical=critical))
        if not steps:
            raise ValueError(f"腳本 {path} 沒有任何步驟")
        return cls(
            name=data.get("name", path.stem),
            description=data.get("description", ""),
            steps=steps,
            step_delay=float(data.get("step_delay", 0.8)),
            source=path,
        )
