"""腳本自動命名（當天日期＋當天第幾隻）、影片↔腳本對應表、自動推 git。

腳本內容仍由 AI 看影格產生；本模組負責命名規則、落檔、登記對應、commit/push。
命名規則：YYYYMMDD_NN，例 20260630_01、20260630_02 ...
"""
from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path

from .config import Config

_INDEX = ".video_index.json"   # 放在 scripts/ 下：{影片檔名: 腳本名}


def _index_path(cfg: Config) -> Path:
    return cfg.scripts_dir / _INDEX


def _load_index(cfg: Config) -> dict[str, str]:
    p = _index_path(cfg)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_index(cfg: Config, idx: dict[str, str]) -> None:
    _index_path(cfg).write_text(
        json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")


def script_for_video(cfg: Config, video_name: str) -> str | None:
    """回傳此影片已對應的腳本名（不含副檔名），沒有則 None。"""
    return _load_index(cfg).get(video_name)


def next_script_name(cfg: Config, today: date | None = None) -> str:
    """計算當天下一個腳本名 YYYYMMDD_NN（掃 scripts/ 既有檔 + 對應表）。"""
    today = today or date.today()
    prefix = today.strftime("%Y%m%d")
    used = set()
    for p in cfg.scripts_dir.glob(f"{prefix}_*.y*ml"):
        used.add(p.stem)
    used.update(v for v in _load_index(cfg).values() if v.startswith(prefix))
    n = 1
    while f"{prefix}_{n:02d}" in used:
        n += 1
    return f"{prefix}_{n:02d}"


def save_script(cfg: Config, yaml_text: str, video_name: str | None = None,
                name: str | None = None) -> Path:
    """落檔到 scripts/<name>.yaml（name 預設用當天序號），並登記影片對應。"""
    cfg.scripts_dir.mkdir(parents=True, exist_ok=True)
    name = name or next_script_name(cfg)
    path = cfg.scripts_dir / f"{name}.yaml"
    path.write_text(yaml_text, encoding="utf-8")
    if video_name:
        idx = _load_index(cfg)
        idx[video_name] = name
        _save_index(cfg, idx)
    return path


def _git(cfg: Config, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cfg.root),
                          capture_output=True, text=True, timeout=120)


def autopush(cfg: Config, paths: list[Path], message: str,
             branch: str = "main") -> str:
    """git add 指定檔 → commit（[Hibari] 前綴）→ push origin。回傳結果訊息。"""
    if not message.startswith("[Hibari]"):
        message = f"[Hibari] {message}"
    rels = [str(p) for p in paths]
    add = _git(cfg, "add", *rels)
    if add.returncode != 0:
        return f"git add 失敗：{add.stderr.strip()}"
    commit = _git(cfg, "commit", "-m", message)
    if commit.returncode != 0:
        # 沒有變更時 commit 會非零
        if "nothing to commit" in (commit.stdout + commit.stderr):
            return "沒有變更可提交"
        return f"git commit 失敗：{commit.stderr.strip() or commit.stdout.strip()}"
    push = _git(cfg, "push", "origin", branch)
    if push.returncode != 0:
        return f"已 commit 但 push 失敗：{push.stderr.strip()}"
    return f"已 commit 並推送：{message}"


def delete_script(cfg: Config, name: str, push: bool = True) -> tuple[bool, str]:
    """刪除 scripts/<name>.yaml、清掉對應表中指向它的影片，並（選用）推 git。

    回傳 (是否原本存在, git 訊息)。
    """
    name = name[:-5] if name.endswith(".yaml") else name
    path = cfg.scripts_dir / f"{name}.yaml"
    existed = path.exists()
    if existed:
        path.unlink()
    # 移除對應表中指向此腳本的影片
    idx = _load_index(cfg)
    for v in [k for k, val in idx.items() if val == name]:
        del idx[v]
    _save_index(cfg, idx)
    msg = ""
    if push and existed:
        msg = autopush(cfg, [path, _index_path(cfg)], f"刪除測試腳本 {name}")
    return existed, msg


def save_and_push(cfg: Config, yaml_text: str, video_name: str | None = None,
                  name: str | None = None, push: bool = True) -> tuple[Path, str]:
    """落檔 + 登記 + （選用）自動推 git。回傳 (腳本路徑, git 訊息)。"""
    path = save_script(cfg, yaml_text, video_name=video_name, name=name)
    msg = ""
    if push:
        files = [path, _index_path(cfg)]
        msg = autopush(cfg, files, f"自動產生測試腳本 {path.stem}"
                       + (f"（來源影片 {video_name}）" if video_name else ""))
    return path, msg
