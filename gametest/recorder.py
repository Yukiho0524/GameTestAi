"""分段自動接續錄影：對使用者是「一次錄影」，內部超過單段上限就無縫接下一段。

screenrecord 單段上限 180 秒；本模組在一段快結束時自動起下一段，getevent 連續擷取。
停止後：
 - 單段 → 存 rec_<ts>.mp4 + rec_<ts>.mp4.taps.json（與舊格式相容）
 - 多段 → 存 rec_<ts>/part01.mp4...、rec_<ts>/session.json、rec_<ts>/taps.json
生成端把整個 session 當一個連續流程 → 一支腳本。
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path

from . import geteventcap as ge
from .adb import Adb
from .config import Config


class RecordingSession:
    def __init__(self, cfg: Config, adb: Adb, seg_seconds: int = 175):
        self.cfg = cfg
        self.adb = adb
        self.seg_seconds = seg_seconds
        self.stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.tmp_dir = cfg.recordings_dir / f".rec_tmp_{self.stamp}"
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self._stop = threading.Event()
        self._thread = None
        self._cur_popen = None
        self._parts: list[Path] = []
        self._cap = None
        self.t0 = 0.0
        self.maxr = (1279, 719)

    # ---- 生命週期 ----
    def start(self):
        self.t0 = ge.device_uptime(self.adb)
        # 自動偵測觸控裝置節點（雷電上不一定是 event2）
        self.touch_dev = ge.detect_touch_device(self.adb)
        self.maxr = ge.touch_range(self.adb, self.touch_dev)
        # PTY 行緩衝擷取（落地成檔會因區塊緩衝在 pkill 時丟失，反而更糟）
        self._cap = ge.Capture(self.adb, max_seconds=3600, dev=self.touch_dev)
        self._cap.start()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        idx = 0
        while not self._stop.is_set():
            idx += 1
            dev = f"/sdcard/gt_seg{idx:02d}.mp4"
            self._cur_popen = self.adb.screenrecord_seg_start(dev, self.seg_seconds)
            # 等這段結束：自然到時限、或被 stop() INT
            self._cur_popen.wait()
            local = self.tmp_dir / f"part{idx:02d}.mp4"
            self.adb.pull_file(dev, str(local))
            if local.exists() and local.stat().st_size > 0:
                self._parts.append(local)
            # 若使用者已按停止就結束；否則無縫接下一段
            if self._stop.is_set():
                break

    def stop(self) -> dict:
        """停止錄影，組裝輸出。回傳 {video/dir, taps_json, n_taps, n_parts}。"""
        self._stop.set()
        self.adb.screenrecord_intr()          # 讓目前這段收尾
        if self._thread:
            self._thread.join(timeout=60)
        text = self._cap.stop() if self._cap else ""
        self._raw_text = text
        touches = ge.parse(text, self.t0, self.maxr[0], self.maxr[1])
        return self._assemble(touches)

    # ---- 落檔 ----
    def _assemble(self, touches) -> dict:
        src = self.cfg.video_source_dir
        src.mkdir(parents=True, exist_ok=True)
        n_parts = len(self._parts)

        if n_parts == 0:
            self._cleanup()
            return {"error": "沒有錄到任何片段", "n_parts": 0, "n_taps": 0}

        if n_parts == 1:
            # 單段：與舊格式相容
            out = src / f"rec_{self.stamp}.mp4"
            self._parts[0].replace(out)
            jp = ge.save_taps_json(out, touches)
            # 存原始 getevent log 供日後診斷漏抓
            try:
                (Path(str(out) + ".taps.raw.log")).write_text(
                    getattr(self, "_raw_text", ""), encoding="utf-8")
            except Exception:
                pass
            self._cleanup()
            return {"video": str(out), "taps_json": str(jp),
                    "n_taps": len(touches), "n_parts": 1}

        # 多段：session 資料夾
        sess = src / f"rec_{self.stamp}"
        sess.mkdir(parents=True, exist_ok=True)
        parts = []
        for i, p in enumerate(self._parts, 1):
            dst = sess / f"part{i:02d}.mp4"
            p.replace(dst)
            parts.append(dst.name)
        (sess / "session.json").write_text(json.dumps(
            {"stamp": self.stamp, "parts": parts}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        # taps.json（時間軸連續，t0=session 起點）
        jp = sess / "taps.json"
        data = [{"t": round(t.t_down, 3), "duration_ms": t.duration_ms,
                 "x": t.x, "y": t.y, "nx": round(t.nx, 4), "ny": round(t.ny, 4),
                 "end_nx": round(t.end_x / t.max_x, 4),
                 "end_ny": round(t.end_y / t.max_y, 4),
                 "kind": t.kind()} for t in touches]
        jp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self._cleanup()
        return {"dir": str(sess), "taps_json": str(jp),
                "n_taps": len(touches), "n_parts": n_parts}

    def _cleanup(self):
        try:
            for f in self.tmp_dir.glob("*"):
                f.unlink()
            self.tmp_dir.rmdir()
        except Exception:
            pass
