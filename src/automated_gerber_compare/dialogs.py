"""Consistent application dialogs; native file navigation remains handled by Tk."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .branding import app_mark, apply_window_icon, place_dialog
from .ui_components import AMBER, FONT, INK, MUTED, PAPER, RED, label


class MessageWindow(tk.Toplevel):
    def __init__(self, parent, title, message, *, kind="info", confirm=False):
        super().__init__(parent)
        self.result = False
        self.title(title)
        self.configure(background=PAPER)
        self.transient(parent)
        self.resizable(True, True)
        apply_window_icon(self)
        lines = sum(max(1, (len(line)+49)//50) for line in message.splitlines())
        height = min(560, max(280, 170+lines*23), max(280, self.winfo_screenheight()-120))
        place_dialog(self, parent, 660, height)
        self.minsize(520, 260)
        body = tk.Frame(self, background=PAPER, padx=26, pady=24)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)
        header = tk.Frame(body, background=PAPER)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 18))
        app_mark(header, size=32).pack(side="left", padx=(0, 12))
        color = RED if kind == "error" else AMBER if kind == "warning" else INK
        label(header, title, size=16, bold=True, color=color).pack(side="left")
        content = tk.Frame(body, background=PAPER)
        content.grid(row=1, column=0, sticky="nsew")
        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=1)
        text = tk.Text(content, font=(FONT, 10), wrap="word", background=PAPER, foreground=INK,
                       borderwidth=0, highlightthickness=0, padx=0, pady=4, spacing3=7)
        text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(content, orient="vertical", command=text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        text.configure(yscrollcommand=scrollbar.set)
        text.insert("1.0", message)
        text.configure(state="disabled")
        actions = tk.Frame(body, background=PAPER)
        actions.grid(row=2, column=0, sticky="ew", pady=(18, 0))
        self.confirm_button = ttk.Button(actions, text="确认" if confirm else "知道了", style="Primary.TButton", command=lambda: self.finish(True))
        self.confirm_button.pack(side="right")
        if confirm:
            ttk.Button(actions, text="取消", style="Quiet.TButton", command=lambda: self.finish(False)).pack(side="right", padx=(0, 12))
        label(actions, "可选择文本并复制", size=8, color=MUTED).pack(side="left")
        self.protocol("WM_DELETE_WINDOW", lambda: self.finish(False))
        self.bind("<Escape>", lambda _: self.finish(False))
        self.bind("<Return>", lambda _: self.finish(True))

    def finish(self, result):
        self.result = result
        self.destroy()


def _show(title, message, *, parent, kind="info", confirm=False):
    previous_grab = parent.grab_current()
    window = MessageWindow(parent, title, message, kind=kind, confirm=confirm)
    window.wait_visibility()
    window.grab_set()
    window.confirm_button.focus_set()
    parent.wait_window(window)
    if previous_grab is not None and previous_grab.winfo_exists():
        previous_grab.grab_set()
    return window.result


def askyesno(title, message, *, parent):
    return _show(title, message, parent=parent, confirm=True)


def showinfo(title, message, *, parent):
    return _show(title, message, parent=parent)


def showwarning(title, message, *, parent):
    return _show(title, message, parent=parent, kind="warning")


def showerror(title, message, *, parent):
    return _show(title, message, parent=parent, kind="error")
