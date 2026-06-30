"""影片監看：掃描影片來源資料夾，對「尚無對應腳本」的新影片自動抽幀。

腳本內容需由 AI 看圖判讀後產生，故 watcher 只負責：偵測新影片 → 抽幀 →
列出待分析清單。產出後再請 Claude 依影格產生 scripts/<影片名>.yaml。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .video import extract_frames


@dataclass
class PendingVideo:
    video: Path
    frames_dir: Path
    frame_count: int


def _is_video(path: Path, cfg: Config) -> bool:
    return path.is_file() and path.suffix.lower() in cfg.watch_extensions


def _has_script(cfg: Config, video: Path) -> bool:
    """影片是否已有對應腳本（查 scripts/.video_index.json 對應表）。"""
    from .scriptgen import script_for_video
    name = script_for_video(cfg, video.name)
    if not name:
        return False
    return (cfg.scripts_dir / f"{name}.yaml").exists() or \
           (cfg.scripts_dir / f"{name}.yml").exists()


def _frames_done(cfg: Config, video: Path) -> bool:
    """影片是否已抽過幀（frames/<stem>/ 已有圖）。"""
    d = cfg.frames_dir / video.stem
    return d.exists() and any(d.glob("*.png"))


def scan_once(cfg: Config, force: bool = False) -> list[PendingVideo]:
    """掃描一次來源資料夾，對缺腳本且未抽幀的影片抽幀。回傳待分析清單。"""
    cfg.ensure_dirs()
    src = cfg.video_source_dir
    if not src.exists():
        print(f"[警告] 影片來源資料夾不存在：{src}")
        return []

    pending: list[PendingVideo] = []
    videos = sorted(p for p in src.iterdir() if _is_video(p, cfg))
    if not videos:
        print(f"來源資料夾沒有影片：{src}")
        return []

    for video in videos:
        if _has_script(cfg, video):
            continue  # 已有腳本，跳過
        if _frames_done(cfg, video) and not force:
            # 已抽幀但還沒腳本 → 仍列為待分析
            d = cfg.frames_dir / video.stem
            pending.append(PendingVideo(video, d, len(list(d.glob("*.png")))))
            continue

        print(f"\n▶ 偵測到新影片：{video.name}")
        try:
            frames = extract_frames(cfg, video, every_sec=cfg.watch_every_sec)
            if frames:
                pending.append(PendingVideo(video, frames[0].parent, len(frames)))
        except Exception as e:  # noqa: BLE001
            print(f"  [錯誤] 抽幀失敗：{e}")

    return pending


def _print_pending(pending: list[PendingVideo]) -> None:
    if not pending:
        print("\n沒有待分析的影片（全部都已有對應腳本）。")
        return
    print("\n===== 待分析影片（請交給 Claude 產生腳本）=====")
    for p in pending:
        print(f"  • {p.video.name}")
        print(f"      影格：{p.frames_dir}（{p.frame_count} 張）")
    print("\n下一步：在對話中跟 Claude 說「分析 <影片名>」，我會看影格產生腳本，"
          "依當天日期自動命名（YYYYMMDD_NN）並自動推上 git。")


def watch_loop(cfg: Config) -> None:
    """常駐監看：每隔 poll_interval 秒掃描一次。Ctrl+C 結束。"""
    print(f"開始監看：{cfg.video_source_dir}")
    print(f"輪詢間隔 {cfg.watch_poll_interval}s，按 Ctrl+C 結束。")
    seen_pending: set[str] = set()
    try:
        while True:
            pending = scan_once(cfg)
            new = [p for p in pending if p.video.name not in seen_pending]
            if new:
                _print_pending(new)
                seen_pending.update(p.video.name for p in new)
            time.sleep(cfg.watch_poll_interval)
    except KeyboardInterrupt:
        print("\n已停止監看。")


def run(cfg: Config, watch: bool = False, force: bool = False) -> None:
    if watch:
        watch_loop(cfg)
    else:
        pending = scan_once(cfg, force=force)
        _print_pending(pending)
