"""Tkinter 控制台：選解析度（直版/橫版分區）、輸入包體名並驗證 ADB 開啟、
啟動雷電、執行跨解析度測試。所有耗時操作都在背景執行緒，避免凍結視窗。
"""
from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

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
        # 錄影（含觸控標記，供生成腳本）
        self.rec_btn_start = ttk.Button(top, text="● 開始錄影", command=self._on_rec_start)
        self.rec_btn_start.grid(row=2, column=3, padx=4, pady=4)
        self.rec_btn_stop = ttk.Button(top, text="■ 停止錄影",
                                       command=self._on_rec_stop, state="disabled")
        self.rec_btn_stop.grid(row=2, column=4, padx=4, pady=4)
        ttk.Label(top, text="錄影（自動開觸控標記，最長180秒）：")\
            .grid(row=2, column=0, columnspan=3, sticky="w", padx=6)
        # 生成腳本：分析尚未生成的新影片（呼叫 Claude）
        self.gen_btn = ttk.Button(top, text="⚙ 生成腳本（分析新影片）",
                                  command=self._on_autogen)
        self.gen_btn.grid(row=3, column=0, columnspan=2, sticky="w", padx=6, pady=4)
        ttk.Label(top, text="↑ 錄完按此，由 Claude 看影片自動生成腳本並推 git")\
            .grid(row=3, column=2, columnspan=3, sticky="w", padx=6)
        self._rec_session = None
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

        # 區塊四：AI 白話測試（自然語言命令 → Claude 自主開遊戲操作+截圖記錄；
        # 可存成任務檔重用、可帶記錄事項、套用上方解析度×次數跑套件出報告）
        ai = ttk.LabelFrame(self, text="🤖 AI 白話測試（輸入命令，AI 自己開遊戲摸索執行並截圖記錄）")
        ai.pack(fill="x", **pad)
        ttk.Label(ai, text="命令：").grid(row=0, column=0, sticky="w", padx=6)
        self.ai_cmd_var = tk.StringVar()
        ttk.Entry(ai, textvariable=self.ai_cmd_var)\
            .grid(row=0, column=1, columnspan=2, sticky="we", padx=6, pady=4)
        self.ai_btn = ttk.Button(ai, text="立即執行(單次)", command=self._on_ai_mission)
        self.ai_btn.grid(row=0, column=3, padx=6)
        ttk.Label(ai, text="記錄事項：").grid(row=1, column=0, sticky="w", padx=6)
        self.ai_checks_var = tk.StringVar()
        ttk.Entry(ai, textvariable=self.ai_checks_var)\
            .grid(row=1, column=1, columnspan=2, sticky="we", padx=6, pady=2)
        ttk.Label(ai, text="（分號隔開，例：記錄購買前後金幣數量並確認扣款；記錄商品剩餘次數）",
                  foreground="gray").grid(row=2, column=1, columnspan=2,
                                          sticky="w", padx=6)
        ttk.Label(ai, text="已存任務：").grid(row=3, column=0, sticky="w", padx=6)
        self.ai_mission_var = tk.StringVar()
        self.ai_mission_combo = ttk.Combobox(ai, textvariable=self.ai_mission_var,
                                             width=28, state="readonly")
        self.ai_mission_combo.grid(row=3, column=1, sticky="we", padx=6, pady=4)
        self.ai_save_btn = ttk.Button(ai, text="💾 存成任務(含解析度×次數)",
                                      command=self._on_ai_save)
        self.ai_save_btn.grid(row=3, column=2, padx=6)
        self.ai_run_btn = ttk.Button(ai, text="▶ 執行任務套件→報告",
                                     command=self._on_ai_run_saved)
        self.ai_run_btn.grid(row=3, column=3, padx=6)
        ttk.Label(ai, text="例：進入遊戲，使用右下角便利機開啟商城，購買一次雞精",
                  foreground="gray").grid(row=4, column=1, columnspan=3,
                                          sticky="w", padx=6)
        ai.columnconfigure(1, weight=1)
        self._refresh_missions()

        # 狀態列 + log
        self.status = tk.StringVar(value="就緒")
        ttk.Label(self, textvariable=self.status, anchor="w", relief="sunken")\
            .pack(fill="x", side="bottom")
        self.log = tk.Text(self, height=8, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=False, padx=8, pady=4)

        # 定時偵測 scripts 資料夾，有新腳本立即出現在清單
        self.after(3000, self._poll_scripts)

    def _list_scripts(self):
        return [p.name for p in sorted(self.cfg.scripts_dir.glob("*.y*ml"))]

    def _refresh_scripts(self, select_newest: bool = False):
        scripts = self._list_scripts()
        self._script_cache = scripts
        self.script_combo["values"] = scripts
        if select_newest and scripts:
            newest = max(self.cfg.scripts_dir.glob("*.y*ml"),
                         key=lambda p: p.stat().st_mtime).name
            self.script_var.set(newest)
        elif scripts and self.script_var.get() not in scripts:
            self.script_combo.current(0)
        elif not scripts:
            self.script_var.set("")

    def _poll_scripts(self):
        """定時偵測 scripts 資料夾變化，有新/刪腳本就更新清單（保留目前選擇）。"""
        try:
            if self._list_scripts() != getattr(self, "_script_cache", None):
                self._refresh_scripts()
        finally:
            self.after(3000, self._poll_scripts)

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

    def _on_autogen(self):
        if not messagebox.askyesno(
                "生成腳本", "將分析「來源夾中尚未生成腳本」的影片，"
                "呼叫 Claude 自動生成並推 git。\n"
                "每支影片會花一次 Claude 額度、可能需數分鐘。開始？"):
            return
        self.gen_btn.config(state="disabled")
        self._set_status("分析新影片、生成腳本中（呼叫 Claude，請稍候）...")
        self._log("[生成] 開始分析來源夾新影片 ...")

        def task():
            from .autogen import scan_once
            return scan_once(self.cfg)

        def done(res, err):
            self.gen_btn.config(state="normal")
            self._refresh_scripts(select_newest=True)
            if err:
                self._set_status("生成失敗")
                self._log(f"[生成] 錯誤：{err}")
                messagebox.showerror("生成失敗", str(err))
                return
            n = res or 0
            self._set_status(f"生成完成：新增 {n} 支腳本")
            self._log(f"[生成] 完成，新增 {n} 支腳本（詳見主控台輸出）")
            messagebox.showinfo("生成完成",
                                f"已處理新影片，新增 {n} 支腳本。\n"
                                "（若顯示 0：可能沒有待處理影片，或 Claude 未登入/被擋，"
                                "詳見主控台訊息）")
        self._run_bg(task, done)

    def _on_ai_mission(self):
        command = self.ai_cmd_var.get().strip()
        if not command:
            messagebox.showwarning("AI 指令", "請先輸入命令")
            return
        if not messagebox.askyesno(
                "AI 指令", f"AI 將自主開啟遊戲執行：\n\n{command}\n\n"
                "過程截圖會存 results/ai_mission_*/。會花一次 Claude 額度、"
                "可能需要數分鐘～十幾分鐘。開始？"):
            return
        self.ai_btn.config(state="disabled")
        self._set_status("AI 任務執行中（自主操作遊戲，請勿動模擬器）...")
        self._log(f"[AI] 任務開始：{command}")

        checks = self._ai_checks()

        def task():
            from .aimission import run_mission
            return run_mission(self.cfg, command, checks)

        def done(res, err):
            self.ai_btn.config(state="normal")
            if err:
                self._set_status("AI 任務錯誤")
                self._log(f"[AI] 錯誤：{err}")
                messagebox.showerror("AI 任務錯誤", str(err))
                return
            ok, out = res
            tail = "\n".join(out.strip().splitlines()[-8:])
            self._log(f"[AI] 輸出（尾段）：\n{tail}")
            self._set_status("AI 任務達成" if ok else "AI 任務未達成")
            (messagebox.showinfo if ok else messagebox.showwarning)(
                "AI 任務結果",
                ("✅ 已達成\n\n" if ok else "⚠ 未達成\n\n") + tail +
                "\n\n截圖記錄在 results/ai_mission_*/")
        self._run_bg(task, done)

    # ---- AI 白話測試：任務檔（存/選/套件執行）----
    def _ai_checks(self) -> list[str]:
        raw = self.ai_checks_var.get().replace("；", ";")
        return [c.strip() for c in raw.split(";") if c.strip()]

    def _refresh_missions(self):
        from .aimission import list_missions
        try:
            names = list_missions(self.cfg)
        except Exception:
            names = []
        self.ai_mission_combo["values"] = names
        if names and not self.ai_mission_var.get():
            self.ai_mission_var.set(names[0])

    def _on_ai_save(self):
        command = self.ai_cmd_var.get().strip()
        if not command:
            messagebox.showwarning("存成任務", "請先輸入命令")
            return
        keys = self._selected_keys()
        if not keys:
            messagebox.showwarning("存成任務", "請勾選至少一個解析度（任務會記住）")
            return
        name = simpledialog.askstring("存成任務", "任務名稱：", parent=self)
        if not name:
            return
        from . import resolutions as R
        from .aimission import save_mission
        labels = [r.label for r in R.resolve_keys(keys)]
        p = save_mission(self.cfg, name, command, self._ai_checks(),
                         self.repeat_var.get(), labels)
        self._log(f"[AI] 已存任務：{p.name}（{'、'.join(labels)} × {self.repeat_var.get()} 次）")
        self._refresh_missions()
        self.ai_mission_var.set(p.stem)

    def _on_ai_run_saved(self):
        name = self.ai_mission_var.get()
        if not name:
            messagebox.showwarning("執行任務", "請先選擇已存任務（或先存一個）")
            return
        from .aimission import load_mission
        try:
            mission = load_mission(self.cfg, name)
        except Exception as e:
            messagebox.showerror("執行任務", str(e))
            return
        n_runs = len(mission["resolutions"]) * int(mission["repeat"])
        if not messagebox.askyesno(
                "執行任務套件",
                f"任務：{mission['name']}\n命令：{mission['command']}\n"
                f"記錄事項：{'；'.join(mission['checks']) or '（無）'}\n"
                f"解析度：{', '.join(mission['resolutions'])} × {mission['repeat']} 次"
                f"（共 {n_runs} 輪，每輪重開模擬器+花一次 Claude 額度）\n\n開始？"):
            return
        self.ai_run_btn.config(state="disabled")
        self._log(f"[AI] 任務套件開始：{mission['name']}（{n_runs} 輪）")

        def task():
            from .aimission import run_mission_suite
            return run_mission_suite(self.cfg, mission,
                                     on_progress=self._set_status)

        def done(res, err):
            self.ai_run_btn.config(state="normal")
            if err:
                self._set_status("AI 任務套件錯誤")
                messagebox.showerror("AI 任務套件錯誤", str(err))
                return
            report, ok = res
            self._set_status("AI 任務套件完成" + ("（全部達成）" if ok else "（有未達成）"))
            self._log(f"[AI] 報告：{report}")
            (messagebox.showinfo if ok else messagebox.showwarning)(
                "AI 任務套件結果",
                ("✅ 全部達成\n\n" if ok else "⚠ 有未達成\n\n") + f"報告：\n{report}")
        self._run_bg(task, done)

    def _on_rec_start(self):
        self.rec_btn_start.config(state="disabled")
        self._set_status("準備錄影（連線模擬器中）...")

        def task():
            from .recorder import RecordingSession
            adb = self._adb()
            sess = RecordingSession(self.cfg, adb)
            sess.start()
            return sess

        def done(res, err):
            if err:
                self.rec_btn_start.config(state="normal")
                self._set_status("錄影啟動失敗")
                self._log(f"[錄影] 錯誤：{err}")
                messagebox.showerror("錄影失敗", str(err))
                return
            self._rec_session = res
            self.rec_btn_stop.config(state="normal")
            self._set_status("● 錄影中…（超過3分會自動接續）請操作，完成按「停止錄影」")
            self._log("[錄影] 開始（自動接續分段）")
        self._run_bg(task, done)

    def _on_rec_stop(self):
        if not getattr(self, "_rec_session", None):
            return
        self.rec_btn_stop.config(state="disabled")
        self._set_status("停止錄影、串接分段、解析觸控中...")
        sess = self._rec_session

        def task():
            return sess.stop()

        def done(res, err):
            self._rec_session = None
            self.rec_btn_start.config(state="normal")
            if err:
                self._set_status("存檔失敗")
                self._log(f"[錄影] 錯誤：{err}")
                messagebox.showerror("錄影存檔失敗", str(err))
                return
            if res.get("error"):
                self._set_status("錄影失敗")
                self._log(f"[錄影] {res['error']}")
                messagebox.showwarning("錄影", res["error"])
                return
            n_taps = res.get("n_taps", 0)
            n_parts = res.get("n_parts", 1)
            target = res.get("dir") or res.get("video")
            self._refresh_scripts()
            self._set_status(f"錄影完成：{Path(target).name}（{n_parts}段，觸控 {n_taps} 筆）")
            self._log(f"[錄影] 完成：{target}（{n_parts} 段，精確觸控 {n_taps} 筆）")
            messagebox.showinfo("錄影完成",
                                f"已存到來源夾：\n{target}\n\n"
                                f"共 {n_parts} 段，解析出 {n_taps} 筆精確點擊。\n"
                                "可按「⚙ 生成腳本」由 Claude 用精確座標裁圖案生成。")
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
