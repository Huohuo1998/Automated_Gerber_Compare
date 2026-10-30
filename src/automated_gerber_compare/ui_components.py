"""Small native Tk components; no asset downloads or third-party theme dependency."""
from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

BG = "#F5F7FB"
PAPER = "#FFFFFF"
INK = "#202D43"
MUTED = "#63738C"
BORDER = "#E6EBF3"
ACCENT = "#4568F2"
PALE = "#EDF2FF"
GREEN = "#209477"
AMBER = "#B67A20"
RED = "#D04D61"
FONT = "Segoe UI"


def install_theme(root: tk.Tk):
    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")
    root.configure(background=BG)
    root.option_add("*Font", (FONT, 10))
    style.configure("TFrame", background=BG)
    style.configure("Paper.TFrame", background=PAPER)
    style.configure("TLabel", background=BG, foreground=INK, font=(FONT, 10))
    style.configure("Paper.TLabel", background=PAPER)
    style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=(FONT, 9))
    style.configure("Muted.Paper.TLabel", background=PAPER, foreground=MUTED, font=(FONT, 9))
    for name, color, foreground, hover in [
        ("Primary", ACCENT, PAPER, "#3659DF"),
        ("Secondary", "#F0F3FA", "#43516C", "#E6ECF8"),
        ("Quiet", PAPER, MUTED, "#F4F6FB"),
        ("Upload", PALE, ACCENT, "#DFE7FF"),
    ]:
        style.configure(f"{name}.TButton", font=(FONT, 10), background=color, foreground=foreground,
                        borderwidth=0, padding=(16, 10), focusthickness=0, anchor="center")
        style.map(f"{name}.TButton", background=[("disabled", "#EEF1F7"), ("active", hover)],
                  foreground=[("disabled", "#A6B0C1")])
    style.configure("TEntry", padding=9, borderwidth=1, relief="flat", fieldbackground="#FAFBFE",
                    bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER)
    style.map("TEntry", bordercolor=[("focus", "#A7B8FC")], lightcolor=[("focus", "#A7B8FC")])
    style.configure("TCombobox", padding=8, borderwidth=1, fieldbackground="#FAFBFE", background="#F0F3FA", arrowcolor=MUTED,
                    bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER)
    style.map("TCombobox", fieldbackground=[("readonly", "#FAFBFE")], background=[("readonly", "#F0F3FA")],
              foreground=[("readonly", INK)], selectbackground=[("readonly", PALE)], selectforeground=[("readonly", INK)])
    style.configure("TCheckbutton", background=PAPER, foreground=INK, padding=6, font=(FONT, 9))
    style.map("TCheckbutton", background=[("active", PAPER)])
    style.configure("TNotebook", background=PAPER, borderwidth=0, tabmargins=(0, 0, 0, 12),
                    bordercolor=PAPER, lightcolor=PAPER, darkcolor=PAPER)
    style.configure("TNotebook.Tab", background=PAPER, foreground=MUTED, borderwidth=0,
                    padding=(18, 10), font=(FONT, 10), bordercolor=PAPER, lightcolor=PAPER, darkcolor=PAPER)
    style.map("TNotebook.Tab", foreground=[("selected", ACCENT)], background=[("selected", PALE), ("active", "#F6F8FC")],
              bordercolor=[("selected", PALE)], lightcolor=[("selected", PALE)], darkcolor=[("selected", PALE)])
    style.configure("Treeview", background=PAPER, fieldbackground=PAPER, foreground=INK, borderwidth=0,
                    relief="flat", rowheight=39, font=(FONT, 10), bordercolor=PAPER, lightcolor=PAPER, darkcolor=PAPER)
    style.configure("Treeview.Heading", background="#F7F9FD", foreground=MUTED,
                    borderwidth=0, padding=(12, 12), font=(FONT, 10), bordercolor="#F7F9FD", lightcolor="#F7F9FD", darkcolor="#F7F9FD")
    style.map("Treeview", background=[("selected", PALE)], foreground=[("selected", ACCENT)])
    style.map("Treeview.Heading", background=[("active", "#F1F4FB")])
    style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor="#E9EEF8", borderwidth=0,
                    lightcolor=ACCENT, darkcolor=ACCENT, thickness=4)
    for orientation in ("Vertical", "Horizontal"):
        style.layout(f"{orientation}.TScrollbar", [
            (f"{orientation}.Scrollbar.trough", {"sticky": "nswe", "children": [
                (f"{orientation}.Scrollbar.thumb", {"sticky": "nswe", "expand": True})]})])
        style.configure(f"{orientation}.TScrollbar", background="#D4DDEB", troughcolor=PAPER, borderwidth=0,
                        bordercolor=PAPER, lightcolor="#D4DDEB", darkcolor="#D4DDEB", arrowsize=8, gripcount=0)


class ProgressLine(tk.Canvas):
    def __init__(self, parent):
        super().__init__(parent, background=PAPER, height=4, highlightthickness=0, borderwidth=0)
        self.value = 0.0
        self.track = self.create_line(2, 2, 2, 2, fill="#E9EEF8", width=4, capstyle="round")
        self.fill = self.create_line(2, 2, 2, 2, fill=ACCENT, width=4, capstyle="round", state="hidden")
        self.bind("<Configure>", lambda _: self.set(self.value))

    def set(self, value):
        self.value = max(0, min(100, float(value)))
        width = max(4, self.winfo_width())
        self.coords(self.track, 2, 2, width-2, 2)
        self.coords(self.fill, 2, 2, 2+(width-4)*self.value/100, 2)
        self.itemconfigure(self.fill, state="normal" if self.value else "hidden")


def label(parent, text=None, *, variable=None, size=10, color=INK, bold=False, background=PAPER, **kwargs):
    options = dict(background=background, foreground=color, anchor="w", borderwidth=0,
                   highlightthickness=0, padx=0, pady=0)
    options.update(kwargs)
    return tk.Label(parent, text=text, textvariable=variable, font=(FONT, size, "bold" if bold else "normal"), **options)


class Card(tk.Canvas):
    def __init__(self, parent, *, padding=20, auto_height=True, **kwargs):
        super().__init__(parent, background=BG, highlightthickness=0, borderwidth=0, height=100, **kwargs)
        self.padding = padding
        self.auto_height = auto_height
        self.body = tk.Frame(self, background=PAPER)
        self.surface = self.create_polygon(0, 0, 0, 0, 0, 0, fill=PAPER, outline=BORDER, smooth=True, splinesteps=24)
        self.window = self.create_window(padding, padding, window=self.body, anchor="nw")
        self.bind("<Configure>", self._resize)
        self.body.bind("<Configure>", self._measure)

    def _measure(self, _event=None):
        if self.auto_height:
            requested = self.body.winfo_reqheight() + self.padding * 2
            if int(float(self["height"])) != requested:
                self.configure(height=requested)

    def _resize(self, event):
        w, h, r = event.width - 1, event.height - 1, 16
        self.coords(self.surface, r, 1, w-r, 1, w, 1, w, r, w, h-r, w, h, w-r, h,
                    r, h, 1, h, 1, h-r, 1, r, 1, 1)
        self.itemconfigure(self.window, width=max(1, w - self.padding * 2))
        if not self.auto_height:
            self.itemconfigure(self.window, height=max(1, h - self.padding * 2))
        self._measure()


class Tooltip:
    def __init__(self, widget, text):
        self.widget, self.text = widget, text
        self.window = None
        self.timer = None
        self.pending = ""
        widget.bind("<Enter>", self.refresh, add="+")
        widget.bind("<Motion>", self.refresh, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")
        widget.bind("<Destroy>", self.hide, add="+")

    def refresh(self, _event=None):
        value = self.text() if callable(self.text) else self.text
        if value == self.pending and (self.timer is not None or self.window is not None):
            return
        self.hide()
        self.pending = value
        if value:
            self.timer = self.widget.after(400, self.show)

    def show(self):
        self.timer = None
        if not self.pending or not self.widget.winfo_exists():
            return
        tip = self.window = tk.Toplevel(self.widget)
        tip.overrideredirect(True)
        label(tip, self.pending, background=INK, color=PAPER, size=9, padx=12, pady=8,
              wraplength=500, justify="left").pack()
        tip.update_idletasks()
        x = min(self.widget.winfo_pointerx()+12, self.widget.winfo_screenwidth()-tip.winfo_reqwidth()-8)
        y = min(self.widget.winfo_pointery()+16, self.widget.winfo_screenheight()-tip.winfo_reqheight()-8)
        tip.geometry(f"+{max(0,x)}+{max(0,y)}")

    def hide(self, _event=None):
        if self.timer is not None:
            self.widget.after_cancel(self.timer)
            self.timer = None
        if self.window is not None:
            if self.window.winfo_exists():
                self.window.destroy()
            self.window = None
        self.pending = ""


class SourcePath(tk.Label):
    """One-line source context with the full path available by hover/copy."""
    def __init__(self, parent):
        super().__init__(parent, background=PAPER, foreground=MUTED, font=(FONT, 9),
                         borderwidth=0, padx=0, pady=0, anchor="w", cursor="hand2")
        self.full_path = ""
        self.bind("<Configure>", self._fit)
        self.bind("<Button-1>", self._copy)
        self.tooltip = Tooltip(self, lambda: f"点击复制来源文件夹\n{self.full_path}" if self.full_path else "")

    def set_path(self, path):
        self.full_path = str(path) if path else ""
        self._fit()

    def _fit(self, _event=None):
        text = "来源文件夹：" + self.full_path if self.full_path else "来源文件夹将在选择文件后显示"
        font = tkfont.Font(font=self["font"])
        width = max(40, self.winfo_width())
        if font.measure(text) <= width:
            self.configure(text=text)
            return
        low, high = 0, len(text)
        candidate = "…"
        while low <= high:
            length = (low+high)//2
            left, right = (length+1)//2, length//2
            shortened = text[:left]+"…"+(text[-right:] if right else "")
            if font.measure(shortened) <= width:
                candidate = shortened
                low = length+1
            else:
                high = length-1
        self.configure(text=candidate)

    def _copy(self, _event=None):
        if self.full_path:
            self.clipboard_clear()
            self.clipboard_append(self.full_path)
