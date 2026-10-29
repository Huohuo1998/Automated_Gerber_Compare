"""Small native Tk components; no asset downloads or third-party theme dependency."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

BG = "#F5F7FB"
PAPER = "#FFFFFF"
INK = "#202D43"
MUTED = "#7A879C"
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
                    relief="flat", rowheight=39, font=(FONT, 9), bordercolor=PAPER, lightcolor=PAPER, darkcolor=PAPER)
    style.configure("Treeview.Heading", background="#F7F9FD", foreground=MUTED,
                    borderwidth=0, padding=(12, 12), font=(FONT, 9), bordercolor="#F7F9FD", lightcolor="#F7F9FD", darkcolor="#F7F9FD")
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
    return tk.Label(parent, text=text, textvariable=variable, font=(FONT, size, "bold" if bold else "normal"),
                    background=background, foreground=color, anchor="w", **kwargs)


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


def layer_mark(parent, *, size=48, background=BG):
    canvas = tk.Canvas(parent, width=size, height=size, background=background, highlightthickness=0)
    s = size / 48
    for offset, color in [(12, "#D5DFFD"), (6, "#95ACFC"), (0, ACCENT)]:
        canvas.create_polygon(*[v*s for v in (24, 6+offset, 43, 16+offset, 24, 26+offset, 5, 16+offset)],
                              fill=color, outline="")
    return canvas
