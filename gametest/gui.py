"""Tkinter 控制台：選解析度（直版/橫版分區）、輸入包體名並驗證 ADB 開啟、
啟動雷電、執行跨解析度測試。所有耗時操作都在背景執行緒，避免凍結視窗。
"""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, ttk

from . import resolutions as R
from .config import Config, load_config


class App(tk.Tk):
    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.title("雷電手遊自動化測試 控制台")
        self.geometry("720x680")
        self._preset_vars: dict[str, tk.BooleanVar] = {}
        self._build()

    # ---- UI 組裝 ----
    def _build(self):
        pad = {"padx": 8, "pady": 4}

        # 區塊一：模擬器 / 包體名
        top = ttk.LabelFrame(self, text="模擬器與受測 App")
        top.pack(fill="x", **pad)

        ttk.Label(top, text="實例 index：").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        self.idx_var = tk.IntVar(value=self.cfg.instance_index)
        ttk.Spinbox(top, from_=0, to=20, width=6, textvariable=self.idx_var)\
            .grid(row=0, column=1, sticky="w")

        ttk.Label(top, text="包體名：").grid(row=1, column=0, sticky="w", padx=6, pady=4)
        self.pkg_var = tk.StringVar(value=self.cfg.package_name)
        ttk.Entry(top, textvariable=self.pkg_var, width=42)\
            .grid(row=1, column=1, columnspan=2, sticky="we")
        ttk.Button(top, text="啟動雷電", command=self._on_launch)\
            .grid(row=1, column=3, padx=4)
        ttk.Button(top, text="驗證 App 可開啟", command=self._on_verify)\
            .grid(row=1, column=4, padx=4)
        top.columnconfigure(2, weight=1)

        # 區塊二：解析度（直版 / 橫版）
        mid = ttk.LabelFrame(self, text="測試解析度（可複選）")
        mid.pack(fill="both", expand=True, **pad)

        land = ttk.LabelFrame(mid, text="橫版 Landscape")
        land.pack(side="left", fill="both", expand=True, padx=6, pady=6)
        self._fill_presets(land, R.LANDSCAPE)

        port = ttk.LabelFrame(mid, text="直版 Portrait")
        port.pack(side="left", fill="both", expand=True, padx=6, pady=6)
        self._fill_presets(port, R.PORTRAIT)

        # 預設勾選目前 settings.yaml 的解析度（依寬高比對）
        current = {(r.width, r.height) for r in self.cfg.resolutions}
        for p in R.ALL:
            if (p.width, p.height) in current and p.key in self._preset_vars:
                self._preset_vars[p.key].set(True)

        # 區塊三：執行參數
        run = ttk.LabelFrame(self, text="執行")
        run.pack(fill="x", **pad)
        ttk.Label(run, text="每解析度重複次數：").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        self.repeat_var = tk.IntVar(value=self.cfg.repeat)
        ttk.Spinbox(run, from_=1, to=999, width=6, textvariable=self.repeat_var)\
            .grid(row=0, column=1, sticky="w")
        ttk.Label(run, text="測試腳本：").grid(row=0, column=2, sticky="e", padx=6)
        self.script_var = tk.StringVar()
        self.script_combo = ttk.Combobox(run, textvariable=self.script_var,
                                          width=28, state="readonly")
        self.script_combo.grid(row=0, column=3, sticky="we", padx=6)
        ttk.Button(run, text="執行測試", command=self._on_run_test)\
            .grid(row=0, column=4, padx=6)
        ttk.Button(run, text="刪除腳本", command=self._on_delete_script)\
            .grid(row=0, column=5, padx=6)
        run.columnconfigure(3, weight=1)
        self._refresh_scripts()

        # 狀態列 + log
        self.status = tk.StringVar(value="就緒")
        ttk.Label(self, textvariable=self.status, anchor="w", relief="sunken")\
            .pack(fill="x", side="bottom")
        self.log = tk.Text(self, height=8, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=False, padx=8, pady=4)

    def _refresh_scripts(self):
        scripts = [p.name for p in sorted(self.cfg.scripts_dir.glob("*.y*ml"))]
        self.script_combo["values"] = scripts
        if scripts and self.script_var.get() not in scripts:
            self.script_combo.current(0)
        elif not scripts:
            self.script_var.set("")

    def _on_delete_script(self):
        script = self.script_var.get()
        if not script:
            messagebox.showwarning("提醒", "沒有可刪除的腳本")
            return
        if not messagebox.askyesno(
                "確認刪除", f"確定要刪除腳本 {script} 嗎？\n"
                            "會一併清除影片對應，並自動 commit + push 到 git。"):
            return
        self._set_status(f"刪除 {script} ...")

        def task():
            from . import scriptgen
            return scriptgen.delete_script(self.cfg, script, push=True)

        def done(res, err):
            self._refresh_scripts()
            if err:
                self._set_status("刪除失敗")
                self._log(f"[刪除] 錯誤：{err}")
                messagebox.showerror("刪除失敗", str(err))
                return
            existed, gitmsg = res
            self._log(f"[刪除] {script}：{gitmsg or '本機已刪除'}")
            self._set_status(f"已刪除 {script}")
            messagebox.showinfo("已刪除", f"{script} 已刪除。\n{gitmsg}")
        self._run_bg(task, done)

    def _fill_presets(self, parent, presets):
        for p in presets:
            var = tk.BooleanVar(value=False)
            self._preset_vars[p.key] = var
            ttk.Checkbutton(parent, text=p.label, variable=var)\
                .pack(anchor="w", padx=6, pady=2)

    # ---- 工具 ----
    def _selected_keys(self) -> list[str]:
        return [k for k, v in self._preset_vars.items() if v.get()]

    def _log(self, msg: str):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _set_status(self, msg: str):
        self.status.set(msg)

    def _run_bg(self, fn, on_done=None):
        """在背景執行緒跑 fn，完成後在主執行緒呼叫 on_done(result/exception)。"""
        def worker():
            try:
                res = fn()
                err = None
            except Exception as e:  # noqa: BLE001
                res, err = None, e
            self.after(0, lambda: (on_done(res, err) if on_done else None))
        threading.Thread(target=worker, daemon=True).start()

    def _adb(self):
        from .adb import connect_instance
        from .ldplayer import LDConsole
        idx = self.idx_var.get()
        console = LDConsole(self.cfg)
        if not console.is_running(idx):
            console.launch(idx)
        adb = connect_instance(self.cfg, idx)
        adb.wait_boot(self.cfg.boot_timeout)
        return adb

    # ---- 事件 ----
    def _on_launch(self):
        idx = self.idx_var.get()
        keys = self._selected_keys()
        if not keys:
            messagebox.showwarning("提醒", "請至少勾選一個解析度")
            return
        first = R.get(keys[0])
        self._set_status(f"啟動雷電 index={idx}，套用 {first.label} ...")
        self._log(f"[啟動] index={idx} 解析度={first.label}")

        def task():
            from .ldplayer import LDConsole
            console = LDConsole(self.cfg)
            console.apply_resolution_and_launch(idx, first.to_resolution())
            return first

        def done(res, err):
            if err:
                self._set_status("啟動失敗")
                self._log(f"[錯誤] {err}")
                messagebox.showerror("啟動失敗", str(err))
            else:
                self._set_status(f"已啟動，解析度 {res.label}")
                self._log("[啟動] 完成")
        self._run_bg(task, done)

    def _on_verify(self):
        pkg = self.pkg_var.get().strip()
        if not pkg:
            messagebox.showwarning("提醒", "請先輸入包體名")
            return
        self._set_status(f"驗證 {pkg} ...")
        self._log(f"[驗證] {pkg}")

        def task():
            from .appcheck import launch_and_verify
            return launch_and_verify(self._adb(), pkg)

        def done(res, err):
            if err:
                self._set_status("驗證失敗")
                self._log(f"[錯誤] {err}")
                messagebox.showerror("驗證失敗", str(err))
                return
            self._log(f"[驗證] 已安裝={res.installed} 啟動={res.launched} "
                      f"前景={res.foreground}")
            self._log(f"[驗證] {res.message}")
            self._set_status("✅ 可開啟" if res.ok else "❌ 無法確認可開啟")
            (messagebox.showinfo if res.ok else messagebox.showwarning)(
                "驗證結果", res.message)
        self._run_bg(task, done)

    def _on_run_test(self):
        keys = self._selected_keys()
        script = self.script_var.get()
        if not keys:
            messagebox.showwarning("提醒", "請至少勾選一個解析度")
            return
        if not script:
            messagebox.showwarning("提醒", "請選擇測試腳本（scripts/ 內需有 .yaml）")
            return
        if not messagebox.askyesno(
                "確認", f"將以 {len(keys)} 個解析度 × {self.repeat_var.get()} 次"
                        f"執行 {script}，期間請勿操作模擬器。開始？"):
            return

        # 套用 GUI 選擇到 cfg
        self.cfg.instance_index = self.idx_var.get()
        self.cfg.package_name = self.pkg_var.get().strip()
        self.cfg.repeat = self.repeat_var.get()
        self.cfg.resolutions = R.resolve_keys(keys)
        self._set_status("執行測試中 ...")
        self._log(f"[測試] {script} 解析度={keys} 重複={self.cfg.repeat}")

        def task():
            from .report_excel import write_excel
            from .runner import run_suite
            from .script_model import TestScript
            ts = TestScript.load(self.cfg.scripts_dir / script)
            suite, root = run_suite(self.cfg, ts)
            xlsx = write_excel(suite, root)
            return suite, xlsx

        def done(res, err):
            if err:
                self._set_status("測試失敗")
                self._log(f"[錯誤] {err}")
                messagebox.showerror("測試失敗", str(err))
                return
            suite, xlsx = res
            rate = suite.success_rate()
            bugs = sum(r.bug_count for r in suite.runs)
            self._set_status(f"完成，總成功率 {rate:.1f}%，BUG {bugs}")
            self._log(f"[測試] 完成，總成功率 {rate:.1f}%，BUG 步驟 {bugs}，報告：{xlsx}")
            import webbrowser
            webbrowser.open(xlsx.as_uri())
        self._run_bg(task, done)


def launch(cfg: Config | None = None):
    cfg = cfg or load_config()
    cfg.ensure_dirs()
    App(cfg).mainloop()
