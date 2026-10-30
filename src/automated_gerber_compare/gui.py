"""Modern native desktop shell. Tk operations stay on the main thread."""
from __future__ import annotations

import queue
import shutil
import threading
import tkinter as tk
import uuid
import webbrowser
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path, PurePosixPath
from tkinter import filedialog, ttk

from . import __version__
from . import dialogs as messagebox
from .branding import app_mark, apply_window_icon, place_dialog, prepare_process_identity
from .discovery import apply_discovery, discover_installations
from .models import PreparedComparison, RunResult
from .rcom import EndpointLock, RcomClient
from .report import STATUS_LABELS
from .service import run_comparison
from .session import ValorSession
from .settings import Settings, config_directory, default_output_directory, load_settings, save_settings
from .ui_components import ACCENT, AMBER, BG, FONT, GREEN, INK, MUTED, PALE, PAPER, RED, Card, ProgressLine, SourcePath, Tooltip, install_theme, label
from .validation import prepare_comparison, warnings_for_pair


class SettingsDialog(tk.Toplevel):
    TYPES = {"get_exe": str, "rcom_exe": str, "env_file": str, "database": str, "port": int,
             "startup_timeout": float, "command_timeout": float, "tolerance_mils": float,
             "map_resolution_mils": int, "margin_inch": float, "image_size": int}

    def __init__(self, parent: App):
        super().__init__(parent)
        self.parent_app = parent
        self.title("Valor 设置")
        apply_window_icon(self)
        place_dialog(self, parent, 740, 690)
        self.minsize(680, 650)
        self.configure(background=PAPER)
        self.transient(parent)
        self.vars = {key: tk.StringVar(value=str(getattr(parent.settings, key))) for key in self.TYPES}
        self.auto_start = tk.BooleanVar(value=parent.settings.auto_start_get)
        self.scan_events: queue.Queue = queue.Queue()
        self.installations = parent.installations[:]
        body = tk.Frame(self, background=PAPER, padx=28, pady=24)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)
        label(body, "工作环境", size=20, bold=True).grid(row=0, column=0, sticky="w")
        label(body, "选择本机 Valor 安装，连接设置会为下次使用保留。", color=MUTED, size=9).grid(row=1, column=0, sticky="w", pady=(5, 18))
        notebook = ttk.Notebook(body)
        notebook.grid(row=2, column=0, sticky="nsew")
        connection = tk.Frame(notebook, background=PAPER, pady=12)
        parameters = tk.Frame(notebook, background=PAPER, pady=16)
        notebook.add(connection, text="连接与启动")
        notebook.add(parameters, text="对比参数")
        discovery = tk.Frame(connection, background=PAPER)
        discovery.pack(fill="x", pady=(0, 12))
        self.installation_var = tk.StringVar()
        self.installation_combo = ttk.Combobox(discovery, state="readonly", textvariable=self.installation_var)
        self.installation_combo.pack(side="left", fill="x", expand=True)
        self.installation_combo.bind("<<ComboboxSelected>>", self._choose_installation)
        self.detect_button = ttk.Button(discovery, text="自动查找", style="Secondary.TButton", command=self._detect)
        self.detect_button.pack(side="left", padx=(10, 0))
        self._fill_installations()
        self._path_field(connection, "get_exe", "get.exe", "选择 Valor get.exe")
        self._path_field(connection, "rcom_exe", "rcom.exe", "选择 Valor rcom.exe")
        row = tk.Frame(connection, background=PAPER)
        row.pack(fill="x", pady=(0, 10))
        row.columnconfigure((0, 1), weight=1)
        self._field(row, "database", "数据库", 0, 0)
        self._field(row, "port", "RCOM 端口", 0, 1)
        ttk.Checkbutton(connection, text="自动连接 Valor；未运行时自动启动", variable=self.auto_start).pack(anchor="w", pady=(0, 6))
        timeout = tk.Frame(connection, background=PAPER)
        timeout.pack(fill="x")
        label(timeout, "启动等待上限（秒）", color=MUTED, size=9).pack(side="left")
        ttk.Entry(timeout, textvariable=self.vars["startup_timeout"], width=9).pack(side="right")
        self.detect_hint = label(connection, "优先使用已有配置与 Valor 环境，再检查常见安装目录。", color=MUTED, size=9, wraplength=640, justify="left")
        self.detect_hint.pack(anchor="w", pady=(5, 0))
        label(connection, "启动的 Valor 会话会保留，便于完成后复查。", color=MUTED, size=9).pack(anchor="w", pady=(5, 0))
        parameters.columnconfigure((0, 1), weight=1)
        for index, (key, title) in enumerate([
            ("tolerance_mils", "比较容差 / mil"), ("map_resolution_mils", "差异网格 / mil"),
            ("margin_inch", "截图外扩 / inch"), ("image_size", "截图边长 / 像素"),
            ("command_timeout", "单条命令超时 / 秒"),
        ]):
            self._field(parameters, key, title, index // 2, index % 2)
        environment = tk.Frame(parameters, background=PAPER)
        environment.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self._path_field(environment, "env_file", "安装环境文件  ·  可选", "选择 Valor env 环境文件")
        label(parameters, "保持源文件坐标。截图颜色使用本机 Valor 的 User 配色。", color=MUTED, size=9,
              wraplength=600).grid(row=4, column=0, columnspan=2, sticky="w", pady=20)
        buttons = tk.Frame(body, background=PAPER)
        buttons.grid(row=3, column=0, sticky="ew", pady=(16, 0))
        self.save_button = ttk.Button(buttons, text="保存设置", style="Primary.TButton", command=self._save)
        self.save_button.pack(side="right")
        ttk.Button(buttons, text="取消", style="Quiet.TButton", command=self.destroy).pack(side="right", padx=10)
        self._scan_job = self.after(100, self._poll_scan)
        self.grab_set()

    def _field(self, parent, key, title, row, column):
        cell = tk.Frame(parent, background=PAPER)
        cell.grid(row=row, column=column, sticky="ew", padx=(0, 14 if column == 0 else 0), pady=(0, 14))
        label(cell, title, color=MUTED, size=9).pack(anchor="w", pady=(0, 5))
        ttk.Entry(cell, textvariable=self.vars[key]).pack(fill="x")

    def _path_field(self, parent, key, title, prompt):
        frame = tk.Frame(parent, background=PAPER)
        frame.pack(fill="x", pady=(8, 8))
        label(frame, title, color=MUTED, size=9).pack(anchor="w", pady=(0, 5))
        row = tk.Frame(frame, background=PAPER)
        row.pack(fill="x")
        ttk.Entry(row, textvariable=self.vars[key]).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="浏览", style="Quiet.TButton", command=lambda: self._browse(key, prompt)).pack(side="right", padx=(8, 0))

    def _browse(self, key, prompt):
        filters = [("可执行文件", "*.exe"), ("所有文件", "*")] if key != "env_file" else [("所有文件", "*")]
        path = filedialog.askopenfilename(parent=self, title=prompt, filetypes=filters)
        if path:
            self.vars[key].set(path)

    def _fill_installations(self):
        self.installation_combo["values"] = [i.label for i in self.installations]
        self.installation_var.set(self.installations[0].label if self.installations else "尚未发现安装 · 可自动查找或手动选择")

    def _choose_installation(self, _event=None):
        index = self.installation_combo.current()
        if index >= 0:
            item = self.installations[index]
            self.vars["get_exe"].set(str(item.get_exe or ""))
            self.vars["rcom_exe"].set(str(item.rcom_exe or ""))

    def _detect(self):
        settings = replace(self.parent_app.settings, get_exe=self.vars["get_exe"].get().strip(),
                           rcom_exe=self.vars["rcom_exe"].get().strip(), env_file=self.vars["env_file"].get().strip())
        self.detect_button.configure(state="disabled")
        self.detect_hint.configure(text="正在检查环境、注册表与常见安装目录…", foreground=MUTED)
        def work():
            try:
                self.scan_events.put((discover_installations(settings), None))
            except Exception as exc:
                self.scan_events.put(([], str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def _poll_scan(self):
        try:
            installations, error = self.scan_events.get_nowait()
            self.installations = installations
            self._fill_installations()
            if installations:
                self._choose_installation()
            text = error or (f"已发现 {len(installations)} 套安装，可在列表中切换版本。" if installations else "未找到安装，请使用“浏览”手动选择两个可执行文件。")
            self.detect_hint.configure(text=text, foreground=RED if error else MUTED)
            self.detect_button.configure(state="normal")
        except queue.Empty:
            pass
        self._scan_job = self.after(100, self._poll_scan)

    def _save(self):
        try:
            values = asdict(self.parent_app.settings)
            values.update({key: convert(self.vars[key].get().strip()) for key, convert in self.TYPES.items()})
            values["auto_start_get"] = self.auto_start.get()
            settings = Settings(**values)
            save_settings(settings)
        except (ValueError, TypeError, OSError) as exc:
            messagebox.showerror("设置需要调整", str(exc), parent=self)
            return
        self.parent_app.settings = settings
        self.parent_app.installations = self.installations
        self.parent_app._update_config_label()
        self.parent_app._backend_status("待连接", MUTED)
        self.destroy()
        if settings.auto_start_get:
            self.parent_app.after_idle(self.parent_app._connect_backend)

    def destroy(self):
        if hasattr(self, "_scan_job"):
            self.after_cancel(self._scan_job)
        super().destroy()


class App(tk.Tk):
    def __init__(self, *, startup=True):
        prepare_process_identity()
        super().__init__()
        apply_window_icon(self)
        self.title(f"Automated Gerber Compare {__version__}")
        width = max(1000, min(1280, self.winfo_screenwidth() - 60))
        height = max(690, min(940, self.winfo_screenheight() - 100))
        self.geometry(f"{width}x{height}")
        self.minsize(1000, 690)
        install_theme(self)
        self.events: queue.Queue = queue.Queue()
        self.cancel_event = threading.Event()
        self.backend_cancel = threading.Event()
        self.busy = self.backend_busy = self._closing = False
        self.prepared: PreparedComparison | None = None
        self.run: RunResult | None = None
        self.session = ValorSession()
        self.installations = []
        self.settings = Settings()
        settings_error = ""
        try:
            self.settings = load_settings()
        except (OSError, ValueError, TypeError) as exc:
            settings_error = f"设置读取失败，请重新配置：{exc}"
        self.path_a, self.path_b = tk.StringVar(), tk.StringVar()
        self.output = tk.StringVar(value=str(default_output_directory()))
        self.status = tk.StringVar(value="选择两个 ZIP 文件，从层级校验开始。")
        self.config_label = tk.StringVar()
        self.input_buttons = []
        self.file_labels = {}
        self.source_labels = {}
        self.metric_vars = {key: tk.StringVar(value="—") for key in ("pairs", "errors", "warnings", "result")}
        self._build()
        self.bind("<Configure>", self._responsive, add="+")
        self._update_config_label()
        self._set_controls()
        if settings_error:
            self._append_log(settings_error)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._poll_job = self.after(100, self._poll)
        if startup:
            self._startup_job = self.after(200, self._startup_discovery)

    def _build(self):
        root = tk.Frame(self, background=BG, padx=30, pady=24)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(4, weight=1)
        heading = tk.Frame(root, background=BG)
        heading.grid(row=0, column=0, sticky="ew", pady=(0, 22))
        app_mark(heading, background=BG).pack(side="left", padx=(0, 14))
        titles = tk.Frame(heading, background=BG)
        titles.pack(side="left")
        label(titles, "Gerber Compare", size=23, bold=True, background=BG).pack(anchor="w")
        label(titles, "自动化图形校验，让每一层变化清晰可见。", size=9, color=MUTED, background=BG).pack(anchor="w", pady=(4, 0))
        self.settings_button = ttk.Button(heading, text="Valor 设置", style="Secondary.TButton", command=lambda: SettingsDialog(self))
        self.settings_button.pack(side="right")
        self.session_button = ttk.Button(heading, text="启动 / 连接", style="Secondary.TButton", command=lambda: self._connect_backend(force_start=True))
        self.session_button.pack(side="right", padx=10)
        self.backend_label = label(heading, "●  待连接", color=MUTED, background=BG, size=9)
        self.backend_label.pack(side="right", padx=(0, 8))
        steps = tk.Frame(root, background=BG)
        self.steps_frame = steps
        steps.grid(row=1, column=0, sticky="ew", pady=(0, 16))
        for index, title in enumerate(("选择资料", "校验与对比", "查看报告")):
            label(steps, f"0{index+1}", background=BG, color=ACCENT, bold=True, size=9).pack(side="left", padx=(0 if index == 0 else 25, 8))
            label(steps, title, background=BG, color=MUTED, size=9).pack(side="left")
            if index < 2:
                label(steps, "—", background=BG, color="#CCD5E5", size=10).pack(side="left", padx=(25, 0))
        inputs = tk.Frame(root, background=BG)
        inputs.grid(row=2, column=0, sticky="ew", pady=(0, 16))
        inputs.columnconfigure((0, 1), weight=1, uniform="inputs")
        for column, (side, title) in enumerate((("A", "基准资料"), ("B", "待比资料"))):
            card = Card(inputs)
            card.grid(row=0, column=column, sticky="nsew", padx=(0, 8) if column == 0 else (8, 0))
            body = card.body
            top = tk.Frame(body, background=PAPER)
            top.pack(fill="x")
            label(top, f" {side} ", color=ACCENT, background=PALE, bold=True, size=12, padx=5, pady=2).pack(side="left")
            label(top, title, bold=True, size=11).pack(side="left", padx=10)
            button = ttk.Button(top, text="选择 ZIP 文件", style="Upload.TButton", command=lambda s=side: self._choose_zip(s))
            button.pack(side="right")
            self.input_buttons.append(button)
            filename = label(body, "添加一份 Gerber 压缩包", size=12, color=MUTED)
            filename.pack(fill="x", pady=(12, 4))
            metadata = tk.Frame(body, background=PAPER)
            metadata.pack(fill="x")
            detail = label(metadata, "支持 Gerber 与钻孔文件", size=9, color=MUTED)
            detail.pack(side="left")
            self.file_labels[side] = (filename, detail)
            source = SourcePath(metadata)
            source.configure(anchor="e")
            source.pack(side="right", fill="x", expand=True, padx=(18, 0))
            self.source_labels[side] = source
            Tooltip(filename, lambda s=side: (self.path_a if s == "A" else self.path_b).get())
        metrics = tk.Frame(root, background=BG)
        self.metrics_frame = metrics
        metrics.grid(row=3, column=0, sticky="ew", pady=(0, 16))
        for index, (key, title, hint) in enumerate((("pairs", "匹配图层", "唯一对应的 A / B 图层"), ("errors", "校验错误", "修正后即可继续"),
                                                   ("warnings", "关注事项", "确认后可继续对比"), ("result", "比较结果", "以 Valor 图形结果判定"))):
            metrics.columnconfigure(index, weight=1, uniform="metrics")
            card = Card(metrics, padding=14)
            card.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 6, 0 if index == 3 else 6))
            label(card.body, title, size=9, color=MUTED).pack(anchor="w")
            value_row = tk.Frame(card.body, background=PAPER)
            value_row.pack(fill="x", pady=(6, 0))
            value_label = label(value_row, variable=self.metric_vars[key], size=20 if key != "result" else 17, bold=True)
            value_label.pack(side="left")
            help_label = label(value_row, hint, size=8, color=MUTED)
            help_label.pack(side="right", padx=(8, 0))
            if key in ("errors", "warnings"):
                for widget in (card.body, value_row, value_label, help_label):
                    widget.configure(cursor="hand2")
                    widget.bind("<Button-1>", lambda _: self.notebook.select(1))
            setattr(self, f"metric_{key}_label", value_label)
        card = Card(root, padding=18, auto_height=False)
        card.grid(row=4, column=0, sticky="nsew")
        card.body.rowconfigure(1, weight=1)
        card.body.columnconfigure(0, weight=1)
        caption = tk.Frame(card.body, background=PAPER)
        caption.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        label(caption, "图层工作区", bold=True, size=12).pack(side="left")
        label(caption, "悬停查看提醒 · 双击查看图层详情", color=MUTED, size=8).pack(side="right")
        self.notebook = ttk.Notebook(card.body)
        self.notebook.grid(row=1, column=0, sticky="nsew")
        self.pair_tree = self._tree_tab("层级配对", [("type", "图层分类", 145), ("a", "基准文件 A", 225), ("b", "待比文件 B", 225),
                                                    ("notice", "校验提醒", 300), ("status", "对比状态", 110), ("boxes", "差异网格", 80)])
        self.pair_frame = self.pair_tree.master
        self.pair_tree.bind("<Double-1>", self._pair_details)
        self.pair_tooltip = Tooltip(self.pair_tree, self._pair_tooltip)
        self.issues_text = self._text_tab("校验详情")
        self.file_tree = self._tree_tab("文件清单", [("side", "版本", 60), ("path", "压缩包内路径", 520), ("category", "分类", 180), ("size", "大小 / 字节", 110)])
        self.log_text = self._text_tab("运行记录")
        self.empty_state = tk.Frame(self.pair_frame, background=PAPER)
        app_mark(self.empty_state, size=50, background=PAPER).pack(pady=(0, 10))
        label(self.empty_state, "从两份资料开始", bold=True, size=13).pack(anchor="center")
        label(self.empty_state, "选择 ZIP 并校验后，在这里查看图层配对与结果。", color=MUTED, size=9).pack(pady=(6, 0))
        self.empty_state.place(relx=0.5, rely=0.58, anchor="center")
        footer = Card(root, padding=16)
        footer.grid(row=5, column=0, sticky="ew", pady=(16, 0))
        actions = tk.Frame(footer.body, background=PAPER)
        actions.pack(fill="x")
        self.validate_button = ttk.Button(actions, text="校验资料", style="Secondary.TButton", command=self._validate)
        self.validate_button.pack(side="left")
        self.start_button = ttk.Button(actions, text="开始对比", style="Primary.TButton", command=self._start)
        self.start_button.pack(side="left", padx=10)
        self.cancel_button = ttk.Button(actions, text="取消", style="Quiet.TButton", command=self._cancel)
        self.cancel_button.pack(side="left")
        self.export_button = ttk.Button(actions, text="导出报告", style="Secondary.TButton", command=self._export)
        self.export_button.pack(side="right")
        self.open_button = ttk.Button(actions, text="打开报告", style="Quiet.TButton", command=self._open_report)
        self.open_button.pack(side="right", padx=10)
        self.progress_bar = ProgressLine(footer.body)
        self.progress_bar.pack(fill="x", pady=(13, 10))
        label(footer.body, variable=self.status, size=9, color=MUTED, wraplength=1100, justify="left").pack(anchor="w")
        bottom = tk.Frame(root, background=BG)
        bottom.grid(row=6, column=0, sticky="ew", pady=(12, 0))
        label(bottom, variable=self.config_label, color=MUTED, background=BG, size=8).pack(side="left")
        output_button = ttk.Button(bottom, text="输出目录…", style="Secondary.TButton", command=self._choose_output)
        output_button.pack(side="right")
        self.input_buttons.append(output_button)

    def _tree_tab(self, title, columns):
        frame = tk.Frame(self.notebook, background=PAPER)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.notebook.add(frame, text=title)
        tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show="headings", selectmode="browse")
        for key, caption, width in columns:
            tree.heading(key, text=caption, anchor="w")
            tree.column(key, width=width, minwidth=55, anchor="w")
        tree.tag_configure("even", background=PAPER)
        tree.tag_configure("odd", background="#FAFBFE")
        tree.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        def update_scrollbar(scrollbar, first, last):
            scrollbar.set(first, last)
            if float(first) <= 0 and float(last) >= 1:
                scrollbar.grid_remove()
            else:
                scrollbar.grid()
        tree.configure(yscrollcommand=lambda first, last: update_scrollbar(vertical, first, last),
                       xscrollcommand=lambda first, last: update_scrollbar(horizontal, first, last))
        return tree

    def _responsive(self, event):
        if event.widget is not self:
            return
        compact = self.winfo_height() < 850
        if compact != getattr(self, "_compact", None):
            self._compact = compact
            if compact:
                self.metrics_frame.grid_remove()
            else:
                self.metrics_frame.grid()
        short = self.winfo_height() < 780
        if short != getattr(self, "_short", None):
            self._short = short
            if short:
                self.steps_frame.grid_remove()
            else:
                self.steps_frame.grid()

    def _text_tab(self, title):
        frame = tk.Frame(self.notebook, background=PAPER)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.notebook.add(frame, text=title)
        text = tk.Text(frame, wrap="word", state="disabled", background=PAPER, foreground=INK,
                       font=(FONT, 10), padx=16, pady=14, relief="flat", highlightthickness=0)
        text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        text.configure(yscrollcommand=scrollbar.set)
        return text

    def _update_config_label(self):
        self.config_label.set(f"容差 {self.settings.tolerance_mils:g} mil    /    网格 {self.settings.map_resolution_mils} mil    /    {self.settings.database}")

    def _backend_status(self, text, color=MUTED):
        self.backend_label.configure(text="●  " + text, foreground=color)

    def _set_controls(self):
        def enable(widget, value):
            widget.configure(state="normal" if value else "disabled")
        for button in self.input_buttons:
            enable(button, not self.busy)
        for button in (self.settings_button, self.session_button):
            enable(button, not self.busy and not self.backend_busy)
        enable(self.validate_button, not self.busy and bool(self.path_a.get()) and bool(self.path_b.get()))
        enable(self.start_button, not self.busy and not self.backend_busy and self.prepared is not None
               and self.prepared.validation.can_compare and self.run is None and not (self.prepared.directory / "run.started").exists())
        enable(self.cancel_button, self.backend_busy or (self.busy and self.prepared is not None and self.run is None))
        has_report = self.prepared is not None and (self.prepared.directory / "report.html").is_file()
        enable(self.open_button, not self.busy and has_report)
        enable(self.export_button, not self.busy and has_report)

    def _startup_discovery(self):
        self.backend_busy = True
        self._backend_status("发现安装中", ACCENT)
        self._set_controls()
        settings = self.settings
        def work():
            try:
                installations = discover_installations(settings)
                discovered = apply_discovery(settings, installations)
                warning = ""
                if discovered != settings and discovered.rcom_exe:
                    try:
                        save_settings(discovered)
                    except (OSError, ValueError) as exc:
                        warning = f"路径已发现，但设置未能保存：{exc}"
                self.events.put(("discovered", (discovered, installations, warning)))
            except Exception as exc:
                self.events.put(("backend_error", str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def _connect_backend(self, *, force_start=False):
        if self.busy or self.backend_busy:
            return
        try:
            self.settings.validate()
        except ValueError as exc:
            self._backend_status("需要设置路径", AMBER)
            self._append_log(str(exc))
            return
        self.backend_busy = True
        self.backend_cancel = threading.Event()
        self._backend_status("连接中", ACCENT)
        self._set_controls()
        settings = replace(self.settings, auto_start_get=True) if force_start else self.settings
        cancel = self.backend_cancel
        def work():
            try:
                directory = config_directory() / "sessions" / (datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6])
                directory.mkdir(parents=True)
                self.events.put(("log", f"Valor 连接记录：{directory}"))
                client = RcomClient(settings, directory, cancel)
                with EndpointLock(settings.port):
                    info = self.session.ensure_ready(settings, client, cancel, lambda msg: self.events.put(("backend_progress", msg)))
                self.events.put(("backend_ready", info))
            except Exception as exc:
                self.events.put(("backend_error", str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def _render_inputs(self):
        for side, variable in (("A", self.path_a), ("B", self.path_b)):
            name, detail = self.file_labels[side]
            path = Path(variable.get()) if variable.get() else None
            if path:
                name.configure(text=path.name, foreground=INK)
                try:
                    size = f"{path.stat().st_size / (1024*1024):.2f} MB"
                except OSError:
                    size = "文件待检查"
                detail.configure(text=f"{size}  ·  已选择，等待校验")
            self.source_labels[side].set_path(path.parent if path else None)

    def _invalidate(self):
        self._cancel_auto_validation()
        self.prepared = None
        self.run = None
        self.pair_tree.delete(*self.pair_tree.get_children())
        self.file_tree.delete(*self.file_tree.get_children())
        self._replace_text(self.issues_text, "")
        for var in self.metric_vars.values():
            var.set("—")
        self.metric_errors_label.configure(foreground=INK)
        self.metric_warnings_label.configure(foreground=INK)
        self.metric_result_label.configure(foreground=INK)
        self.empty_state.place(relx=0.5, rely=0.58, anchor="center")
        self.status.set("资料已更新，校验后即可开始对比。")
        self.progress_bar.set(0)
        self._render_inputs()
        self._set_controls()

    def _choose_zip(self, side):
        path = filedialog.askopenfilename(parent=self, title=f"选择 {side} 侧 Gerber ZIP", filetypes=[("ZIP 压缩包", "*.zip"), ("所有文件", "*")])
        if path:
            (self.path_a if side == "A" else self.path_b).set(path)
            self._invalidate()
            self._schedule_auto_validation()

    def _choose_output(self):
        path = filedialog.askdirectory(parent=self, title="选择工作输出目录", initialdir=self.output.get())
        if path:
            self.output.set(path)
            self._invalidate()
            self._schedule_auto_validation()

    def _cancel_auto_validation(self):
        if getattr(self, "_auto_validate_job", None) is not None:
            self.after_cancel(self._auto_validate_job)
            self._auto_validate_job = None

    def _schedule_auto_validation(self):
        self._cancel_auto_validation()
        if self.path_a.get() and self.path_b.get() and not self.busy:
            self.status.set("两份资料已选齐，即将自动校验…")
            self._auto_validate_job = self.after(250, self._validate)

    def _validate(self):
        self._cancel_auto_validation()
        if self.busy or not self.path_a.get() or not self.path_b.get():
            return
        self._invalidate()
        self.busy = True
        self.status.set("正在整理文件，并检查图层的一对一对应关系…")
        self._set_controls()
        a, b, output = Path(self.path_a.get()), Path(self.path_b.get()), Path(self.output.get())
        def work():
            try:
                self.events.put(("validated", prepare_comparison(a, b, output)))
            except Exception as exc:
                self.events.put(("error", str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def _show_validation(self, prepared):
        self.prepared = prepared
        result = prepared.validation
        for index, pair in enumerate(result.pairs):
            notices = list(dict.fromkeys(issue.message for issue in warnings_for_pair(result, pair)))
            self.pair_tree.insert("", "end", iid=str(index), tags=("even" if index % 2 == 0 else "odd",),
                                  values=(pair.label, PurePosixPath(pair.a.relative_path).name, PurePosixPath(pair.b.relative_path).name,
                                          "；".join(notices) if notices else "—", "已配对" if result.can_compare else "需检查", "—"))
        for index, file in enumerate(result.files):
            self.file_tree.insert("", "end", tags=("even" if index % 2 == 0 else "odd",),
                                  values=(file.side, file.relative_path, file.category, f"{file.size:,}"))
        for key, value in (("pairs", len(result.pairs)), ("errors", len(result.errors)), ("warnings", len(result.warnings))):
            self.metric_vars[key].set(str(value))
        self.metric_vars["result"].set("待开始" if result.can_compare else "需检查资料")
        self.metric_errors_label.configure(foreground=RED if result.errors else GREEN)
        self.metric_warnings_label.configure(foreground=AMBER if result.warnings else INK)
        for side in ("A", "B"):
            files = [f for f in result.files if f.side == side]
            layers = sum(f.kind != "ignored" for f in files)
            self.file_labels[side][1].configure(text=f"{layers} 个比较图层  ·  {len(files)} 个文件")
        if result.pairs:
            self.empty_state.place_forget()
        labels = {"error": "错误", "warning": "提醒", "info": "信息"}
        details = [f"已配对 {len(result.pairs)} 层    错误 {len(result.errors)}    提醒 {len(result.warnings)}\n"]
        for issue in result.issues:
            details.append(f"[{labels[issue.severity]}] {issue.side} {issue.file}\n{issue.message}\n")
        details.append(f"工作目录：{prepared.directory}")
        self._replace_text(self.issues_text, "\n".join(details))
        self.status.set(f"已配对 {len(result.pairs)} 层，{len(result.warnings)} 项提醒。确认资料后，开始对比。" if result.can_compare else f"发现 {len(result.errors)} 项错误，请查看校验详情并更新资料。")
        if not result.can_compare:
            self.notebook.select(1)
        self._append_log("校验完成。工作目录：" + str(prepared.directory))

    def _pair_details(self, _event=None):
        selected = self.pair_tree.selection()
        if not selected or not self.prepared:
            return
        index = int(selected[0])
        pair = self.prepared.validation.pairs[index]
        text = f"A：{pair.a.relative_path}\n\nB：{pair.b.relative_path}"
        notices = warnings_for_pair(self.prepared.validation, pair)
        if notices:
            text += "\n\n校验提醒：\n" + "\n".join(f"{issue.side + '：' if issue.side else ''}{issue.message}" for issue in notices)
        if self.run:
            row = self.run.layers[index]
            text += f"\n\n结果：{STATUS_LABELS[row.status]}\n差异网格：{row.difference_boxes if row.difference_boxes is not None else '未取得'}"
        messagebox.showinfo(pair.label, text, parent=self)

    def _pair_tooltip(self):
        if not self.prepared:
            return ""
        tree = self.pair_tree
        row = tree.identify_row(tree.winfo_pointery()-tree.winfo_rooty())
        column = tree.identify_column(tree.winfo_pointerx()-tree.winfo_rootx())
        if not row:
            return ""
        pair = self.prepared.validation.pairs[int(row)]
        if column == "#4":
            return "\n".join(dict.fromkeys(issue.message for issue in warnings_for_pair(self.prepared.validation, pair)))
        if column in ("#2", "#3"):
            return pair.a.relative_path if column == "#2" else pair.b.relative_path
        return ""

    def _start(self):
        if not self.prepared or not self.prepared.validation.can_compare or self.backend_busy:
            return
        try:
            self.settings.validate()
        except ValueError as exc:
            messagebox.showerror("请先完成 Valor 设置", str(exc), parent=self)
            return
        validation = self.prepared.validation
        if not messagebox.askyesno("开始图形对比", f"本次将比较 {len(validation.pairs)} 对图层，包含 {len(validation.warnings)} 项校验提醒。\n\n确认资料后，程序将连接 Valor 并生成带截图的报告。\n\n现在开始？", parent=self):
            return
        self.busy = True
        self.cancel_event = threading.Event()
        self.metric_vars["result"].set("对比中")
        self._set_controls()
        prepared, settings, cancel = self.prepared, self.settings, self.cancel_event
        def work():
            try:
                result = run_comparison(prepared, settings, cancel,
                                        lambda n, total, msg: self.events.put(("progress", (n, total, msg))), session=self.session)
                self.events.put(("finished", result))
            except Exception as exc:
                self.events.put(("error", str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def _poll(self):
        try:
            while True:
                event, value = self.events.get_nowait()
                if event == "log":
                    self._append_log(value)
                elif event == "discovered":
                    self.backend_busy = False
                    self.settings, self.installations, warning = value
                    self._update_config_label()
                    if warning:
                        self._append_log(warning)
                    if self.settings.rcom_exe:
                        self._backend_status("路径已发现", ACCENT)
                        if self.settings.auto_start_get and not self._closing and not self.backend_cancel.is_set():
                            self._connect_backend()
                    else:
                        self._backend_status("请配置 Valor", AMBER)
                elif event == "backend_progress":
                    self._backend_status(value.replace("…", ""), ACCENT)
                    self._append_log(value)
                elif event in ("backend_ready", "backend_error"):
                    self.backend_busy = False
                    self._backend_status(f"已连接 · {value.port}" if event == "backend_ready" else "连接待检查", GREEN if event == "backend_ready" else AMBER)
                    self._append_log(value.message if event == "backend_ready" else str(value))
                elif event == "progress":
                    current, total, text = value
                    self.progress_bar.set(100 * current / total)
                    self.status.set(text)
                    self._append_log(text)
                elif event == "validated":
                    self.busy = False
                    self._show_validation(value)
                elif event == "finished":
                    self.busy = False
                    self.run = value
                    for index, row in enumerate(value.layers):
                        self.pair_tree.set(str(index), "status", STATUS_LABELS[row.status])
                        self.pair_tree.set(str(index), "boxes", "—" if row.difference_boxes is None else row.difference_boxes)
                    self.metric_vars["result"].set(STATUS_LABELS[value.status])
                    self.metric_result_label.configure(foreground=GREEN if value.status == "consistent" else AMBER if value.status == "different" else RED)
                    self.status.set(f"{STATUS_LABELS[value.status]}。报告已生成，可以打开或导出。")
                    self._append_log(STATUS_LABELS[value.status] + "\n" + value.error + "\n" + "\n".join(value.warnings))
                    self._backend_status("会话待检查" if value.status in ("failed", "cancelled") else f"已连接 · {self.settings.port}", AMBER if value.status in ("failed", "cancelled") else GREEN)
                    if value.status in ("failed", "cancelled"):
                        messagebox.showwarning(STATUS_LABELS[value.status], (value.error + "\n\n" + "\n".join(value.warnings)).strip() + "\n\n报告和日志已保存在工作目录。", parent=self)
                elif event == "error":
                    self.busy = False
                    self.metric_vars["result"].set("未完成")
                    self.status.set("处理未完成，请检查运行记录。")
                    self._append_log(value)
                    messagebox.showerror("处理需要检查", value, parent=self)
                self._set_controls()
                if self._closing and not self.backend_busy and not self.busy:
                    self.destroy()
                    return
        except queue.Empty:
            pass
        self._poll_job = self.after(100, self._poll)

    def _cancel(self):
        if self.backend_busy:
            self.backend_cancel.set()
        if self.busy:
            self.cancel_event.set()
        self.status.set("已请求取消，正在整理当前结果…")
        self.cancel_button.configure(state="disabled")

    def _open_report(self):
        if self.prepared:
            webbrowser.open((self.prepared.directory / "report.html").as_uri())

    def _export(self):
        if not self.prepared:
            return
        destination = filedialog.asksaveasfilename(parent=self, title="导出报告", initialfile="Gerber_Compare_Report.zip", defaultextension=".zip",
                                                 filetypes=[("报告与证据包", "*.zip"), ("独立 HTML 报告", "*.html")])
        if destination:
            target = Path(destination)
            source = self.prepared.directory / ("report.html" if target.suffix.lower() in (".html", ".htm") else "report_bundle.zip")
            try:
                if source.resolve() != target.resolve():
                    shutil.copy2(source, target)
            except OSError as exc:
                messagebox.showerror("导出失败", str(exc), parent=self)
                return
            self.status.set("报告已保存：" + str(target))

    @staticmethod
    def _replace_text(widget, text):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", text)
        widget.configure(state="disabled")

    def _append_log(self, text):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _close(self):
        if self.busy:
            messagebox.showinfo("任务正在进行", "请先取消对比并等待结果整理完成；资料校验期间请等待校验完成。", parent=self)
            return
        if self.backend_busy:
            self._closing = True
            self.backend_cancel.set()
            return
        self.destroy()

    def destroy(self):
        self._cancel_auto_validation()
        self.backend_cancel.set()
        for name in ("_poll_job", "_startup_job"):
            if hasattr(self, name):
                self.after_cancel(getattr(self, name))
        super().destroy()


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
