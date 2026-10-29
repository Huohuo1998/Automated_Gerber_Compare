"""Tkinter desktop shell. Workers communicate with Tk only through a queue."""

from __future__ import annotations

import queue
import shutil
import threading
import tkinter as tk
import webbrowser
from dataclasses import asdict
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .models import PreparedComparison, RunResult
from .report import STATUS_LABELS
from .service import run_comparison
from .settings import Settings, default_output_directory, load_settings, save_settings
from .validation import prepare_comparison


class SettingsDialog(tk.Toplevel):
    FIELDS = [
        ("rcom_exe", "rcom.exe 路径", str), ("database", "Valor 数据库", str),
        ("port", "RCOM 端口", int), ("command_timeout", "单命令超时 (秒)", float),
        ("tolerance_mils", "比较容差 (mil)", float), ("map_resolution_mils", "差异网格 (mil)", int),
        ("margin_inch", "截图边界外扩 (inch)", float), ("image_size", "截图边长 (像素)", int),
    ]

    def __init__(self, parent: App):
        super().__init__(parent)
        self.title("Valor 配置")
        self.parent_app = parent
        self.transient(parent)
        self.resizable(False, False)
        self.vars: dict[str, tk.StringVar] = {}
        frame = ttk.Frame(self, padding=24)
        frame.grid(sticky="nsew")
        for row, (key, label, _) in enumerate(self.FIELDS):
            ttk.Label(frame, text=label).grid(row=row, column=0, padx=(0, 18), pady=7, sticky="w")
            self.vars[key] = tk.StringVar(value=str(getattr(parent.settings, key)))
            ttk.Entry(frame, textvariable=self.vars[key], width=53).grid(row=row, column=1, pady=7, sticky="ew")
        ttk.Button(frame, text="浏览…", command=self._choose_exe).grid(row=0, column=2, padx=(8, 0))
        ttk.Label(frame, text="先在 Valor 环境启动 get -a11000（或自定义端口）。\n使用专用会话；运行期间保持该会话空闲。\n显示槽 1/2/3 的颜色由本机 Valor User 配色控制。", foreground="#5e7085").grid(row=8, column=0, columnspan=3, pady=14, sticky="w")
        buttons = ttk.Frame(frame)
        buttons.grid(row=9, column=0, columnspan=3, sticky="e")
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="left", padx=6)
        ttk.Button(buttons, text="保存配置", command=self._save).pack(side="left")
        self.grab_set()

    def _choose_exe(self):
        path = filedialog.askopenfilename(parent=self, title="选择 Valor rcom.exe", filetypes=[("RCOM", "rcom.exe"), ("所有文件", "*")])
        if path:
            self.vars["rcom_exe"].set(path)

    def _save(self):
        try:
            values = {key: convert(self.vars[key].get().strip()) for key, _, convert in self.FIELDS}
            settings = Settings(**values)
            save_settings(settings)
        except (ValueError, TypeError, OSError) as exc:
            messagebox.showerror("配置无效", str(exc), parent=self)
            return
        self.parent_app.settings = settings
        self.parent_app._update_config_label()
        self.destroy()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Automated Gerber Compare {__version__}")
        self.geometry("1180x850")
        self.minsize(980, 720)
        self.configure(background="#f3f6fa")
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("TFrame", background="#f3f6fa")
        style.configure("TLabel", background="#f3f6fa", foreground="#18314c", font=("Microsoft YaHei UI", 10))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 22, "bold"))
        style.configure("Sub.TLabel", foreground="#586f86")
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=(14, 8))
        style.configure("Treeview", rowheight=30, font=("Microsoft YaHei UI", 9))
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"))
        self.events: queue.Queue = queue.Queue()
        self.cancel_event = threading.Event()
        self.busy = False
        self.prepared: PreparedComparison | None = None
        self.run: RunResult | None = None
        self.settings = Settings()
        settings_error = ""
        try:
            self.settings = load_settings()
        except (OSError, ValueError, TypeError) as exc:
            settings_error = f"配置读取失败，请重新配置：{exc}"
        self.path_a, self.path_b = tk.StringVar(), tk.StringVar()
        self.output = tk.StringVar(value=str(default_output_directory()))
        self.status = tk.StringVar(value="请选择 A / B 两个 Gerber ZIP，再校验文件和配对关系。")
        self.config_label = tk.StringVar()
        self.input_buttons: list[ttk.Button] = []
        self._build()
        self._update_config_label()
        self._set_controls()
        if settings_error:
            self._append_log(settings_error)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._poll_job = self.after(100, self._poll)

    def _build(self):
        root = ttk.Frame(self, padding=24)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(4, weight=1)
        heading = ttk.Frame(root)
        heading.grid(row=0, column=0, sticky="ew", pady=(0, 20))
        ttk.Label(heading, text="Gerber 自动对比", style="Title.TLabel").pack(side="left")
        self.settings_button = ttk.Button(heading, text="Valor 配置…", command=lambda: SettingsDialog(self))
        self.settings_button.pack(side="right")
        ttk.Label(root, text="01 选择资料    →    02 校验与确认    →    03 Valor 比较与报告", style="Sub.TLabel").grid(row=1, column=0, sticky="w", pady=(0, 16))
        form = ttk.Frame(root)
        form.grid(row=2, column=0, sticky="ew")
        form.columnconfigure(1, weight=1)
        for row, (label, variable, choose) in enumerate([
            ("A · 基准资料", self.path_a, lambda: self._choose_zip("A")),
            ("B · 待比资料", self.path_b, lambda: self._choose_zip("B")),
            ("工作输出目录", self.output, self._choose_output),
        ]):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", padx=(0, 14), pady=5)
            ttk.Entry(form, textvariable=variable, state="readonly").grid(row=row, column=1, sticky="ew", pady=5)
            button = ttk.Button(form, text="选择…", command=choose)
            button.grid(row=row, column=2, padx=(10, 0), pady=5)
            self.input_buttons.append(button)
        ttk.Label(form, textvariable=self.config_label, style="Sub.TLabel").grid(row=3, column=0, columnspan=3, sticky="w", pady=(8, 14))
        actions = ttk.Frame(root)
        actions.grid(row=3, column=0, sticky="ew", pady=(0, 16))
        self.validate_button = ttk.Button(actions, text="校验资料", command=self._validate)
        self.validate_button.pack(side="left")
        self.start_button = ttk.Button(actions, text="确认并开始对比", command=self._start)
        self.start_button.pack(side="left", padx=8)
        self.cancel_button = ttk.Button(actions, text="取消任务", command=self._cancel)
        self.cancel_button.pack(side="left")
        self.export_button = ttk.Button(actions, text="另存报告…", command=self._export)
        self.export_button.pack(side="right")
        self.open_button = ttk.Button(actions, text="打开报告", command=self._open_report)
        self.open_button.pack(side="right", padx=8)
        self.notebook = ttk.Notebook(root)
        self.notebook.grid(row=4, column=0, sticky="nsew")
        self.pair_tree = self._tree_tab("层配对 / 对比结果", [("type", "分类", 145), ("a", "A 文件", 290), ("b", "B 文件", 290), ("status", "状态", 145), ("boxes", "差异网格", 90)])
        self.issues_text = self._text_tab("校验信息")
        self.file_tree = self._tree_tab("完整文件清单", [("side", "来源", 60), ("path", "包内相对路径", 510), ("category", "分类", 170), ("size", "字节数", 100)])
        self.log_text = self._text_tab("运行日志")
        footer = ttk.Frame(root)
        footer.grid(row=5, column=0, sticky="ew", pady=(16, 0))
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status, wraplength=1000).grid(row=0, column=0, sticky="w")
        self.progress_bar = ttk.Progressbar(footer, mode="determinate", maximum=100)
        self.progress_bar.grid(row=1, column=0, sticky="ew", pady=(10, 0))

    def _tree_tab(self, label, columns):
        frame = ttk.Frame(self.notebook)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.notebook.add(frame, text=label)
        tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show="headings")
        for key, title, width in columns:
            tree.heading(key, text=title)
            tree.column(key, width=width, minwidth=60)
        tree.grid(row=0, column=0, sticky="nsew")
        ttk.Scrollbar(frame, orient="vertical", command=tree.yview).grid(row=0, column=1, sticky="ns")
        yscroll = frame.grid_slaves(row=0, column=1)[0]
        tree.configure(yscrollcommand=yscroll.set)
        xscroll = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        xscroll.grid(row=1, column=0, sticky="ew")
        tree.configure(xscrollcommand=xscroll.set)
        return tree

    def _text_tab(self, label):
        frame = ttk.Frame(self.notebook)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.notebook.add(frame, text=label)
        text = tk.Text(frame, wrap="word", state="disabled", background="white", foreground="#18314c",
                       font=("Microsoft YaHei UI", 10), padx=14, pady=14, relief="flat")
        text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        text.configure(yscrollcommand=scrollbar.set)
        return text

    def _update_config_label(self):
        self.config_label.set(f"Valor：{self.settings.database} · 端口 {self.settings.port} · 容差 {self.settings.tolerance_mils:g} mil · 网格 {self.settings.map_resolution_mils} mil" + ("" if self.settings.rcom_exe else " · 请先配置 rcom.exe"))

    def _set_controls(self):
        def enable(widget, enabled):
            widget.configure(state="normal" if enabled else "disabled")
        for button in self.input_buttons + [self.settings_button]:
            enable(button, not self.busy)
        enable(self.validate_button, not self.busy and bool(self.path_a.get()) and bool(self.path_b.get()))
        enable(self.start_button, not self.busy and self.prepared is not None and self.prepared.validation.can_compare and self.run is None
               and not (self.prepared.directory / "run.started").exists())
        enable(self.cancel_button, self.busy and self.run is None and self.prepared is not None)
        has_report = self.prepared is not None and (self.prepared.directory / "report.html").is_file()
        enable(self.open_button, not self.busy and has_report)
        enable(self.export_button, not self.busy and has_report)

    def _invalidate(self):
        self.prepared = None
        self.run = None
        self.pair_tree.delete(*self.pair_tree.get_children())
        self.file_tree.delete(*self.file_tree.get_children())
        self._replace_text(self.issues_text, "")
        self.status.set("资料已更改，请重新校验。")
        self.progress_bar["value"] = 0
        self._set_controls()

    def _choose_zip(self, side):
        path = filedialog.askopenfilename(parent=self, title=f"选择 {side} 侧 Gerber ZIP", filetypes=[("ZIP 压缩包", "*.zip"), ("所有文件", "*")])
        if path:
            (self.path_a if side == "A" else self.path_b).set(path)
            self._invalidate()

    def _choose_output(self):
        path = filedialog.askdirectory(parent=self, title="选择工作输出目录")
        if path:
            self.output.set(path)
            self._invalidate()

    def _validate(self):
        self._invalidate()
        self.busy = True
        self.status.set("正在解压、识别文件并校验一对一配对…")
        self._set_controls()
        a, b, output = Path(self.path_a.get()), Path(self.path_b.get()), Path(self.output.get())
        def work():
            try:
                prepared = prepare_comparison(a, b, output)
                self.events.put(("validated", prepared))
            except Exception as exc:
                self.events.put(("error", str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def _show_validation(self, prepared: PreparedComparison):
        self.prepared = prepared
        result = prepared.validation
        for index, pair in enumerate(result.pairs):
            self.pair_tree.insert("", "end", iid=str(index), values=(pair.label, pair.a.relative_path, pair.b.relative_path, "已配对" if result.can_compare else "执行被拦截", "—"))
        for file in result.files:
            self.file_tree.insert("", "end", values=(file.side, file.relative_path, file.category, f"{file.size:,}"))
        labels = {"error": "错误", "warning": "警告", "info": "信息"}
        details = [f"可执行：{'是' if result.can_compare else '否'}    已配对 {len(result.pairs)} 层    错误 {len(result.errors)}    警告 {len(result.warnings)}\n"]
        for issue in result.issues:
            details.append(f"[{labels[issue.severity]}] {issue.side} {issue.file}\n{issue.message}\n")
        details.append(f"工作目录：{prepared.directory}")
        self._replace_text(self.issues_text, "\n".join(details))
        if result.can_compare:
            self.status.set(f"校验通过：{len(result.pairs)} 层已唯一配对，{len(result.warnings)} 项警告。请检查校验信息，再确认开始。")
        else:
            self.status.set(f"校验不通过：{len(result.errors)} 项错误，请更新 ZIP 并重新校验。")
            self.notebook.select(1)
        self._append_log("校验完成。工作目录：" + str(prepared.directory))

    def _start(self):
        if self.prepared is None or not self.prepared.validation.can_compare:
            return
        try:
            self.settings.validate()
        except ValueError as exc:
            messagebox.showerror("请先完成 Valor 配置", str(exc), parent=self)
            return
        validation = self.prepared.validation
        confirmed = messagebox.askyesno("确认开始对比", f"已唯一配对 {len(validation.pairs)} 层，校验警告 {len(validation.warnings)} 项。\n\n将连接本机 RCOM 端口 {self.settings.port}，在 {self.settings.database} 中创建独立 Job。\n请确认已检查校验信息，且 Valor 专用会话已开启 RCOM。\n\n现在开始？", parent=self)
        if not confirmed:
            return
        self.busy = True
        self.cancel_event = threading.Event()
        self._set_controls()
        prepared, settings, cancel = self.prepared, self.settings, self.cancel_event
        def work():
            try:
                result = run_comparison(prepared, settings, cancel,
                                        lambda n, total, text: self.events.put(("progress", (n, total, text))))
                self.events.put(("finished", result))
            except Exception as exc:
                self.events.put(("error", str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def _poll(self):
        try:
            while True:
                event, value = self.events.get_nowait()
                if event == "progress":
                    current, total, text = value
                    self.progress_bar["value"] = 100 * current / total
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
                    label = STATUS_LABELS[value.status]
                    self.status.set(f"{label}。报告已生成，可打开或另存。")
                    self._append_log(label + "\n" + (value.error or "") + "\n" + "\n".join(value.warnings))
                    if value.status in ("failed", "cancelled"):
                        messagebox.showwarning(label, (value.error + "\n\n" + "\n".join(value.warnings)).strip() + "\n\n报告和日志已保存在工作目录。", parent=self)
                elif event == "error":
                    self.busy = False
                    self.status.set("处理未完成，请查看日志并重新校验。")
                    self._append_log(value)
                    messagebox.showerror("处理失败", value, parent=self)
                self._set_controls()
        except queue.Empty:
            pass
        self._poll_job = self.after(100, self._poll)

    def _cancel(self):
        self.cancel_event.set()
        self.status.set("已请求取消，正在停止命令并整理结果…")
        self.cancel_button.configure(state="disabled")

    def _open_report(self):
        if self.prepared:
            webbrowser.open((self.prepared.directory / "report.html").as_uri())

    def _export(self):
        if self.prepared is None:
            return
        destination = filedialog.asksaveasfilename(parent=self, title="另存报告", initialfile="Gerber_Compare_Report.zip", defaultextension=".zip",
                                                 filetypes=[("报告与证据包", "*.zip"), ("独立 HTML 报告", "*.html")])
        if not destination:
            return
        target = Path(destination)
        source = self.prepared.directory / ("report.html" if target.suffix.lower() in (".html", ".htm") else "report_bundle.zip")
        try:
            if source.resolve() != target.resolve():
                shutil.copy2(source, target)
        except OSError as exc:
            messagebox.showerror("保存失败", str(exc), parent=self)
            return
        self.status.set("报告已另存：" + str(target))

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
            messagebox.showinfo("任务正在进行", "请先取消对比并等待结果整理完成后关闭窗口；资料校验期间请等待校验完成。", parent=self)
            return
        self.destroy()

    def destroy(self):
        if hasattr(self, "_poll_job"):
            self.after_cancel(self._poll_job)
        super().destroy()


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
