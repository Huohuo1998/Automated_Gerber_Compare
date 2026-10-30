"""Shared branding for source runs, wheels, frozen executables and dialogs."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import tkinter as tk

ASSETS = Path(__file__).resolve().parent / "assets"
APP_ICON = ASSETS / "app.ico"
APP_PNG = ASSETS / "app.png"


def prepare_process_identity() -> None:
    if os.name == "nt":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Huohuo1998.AutomatedGerberCompare")
        except (OSError, AttributeError):
            pass


def apply_window_icon(window) -> None:
    if os.name == "nt":
        window.iconbitmap(str(APP_ICON))
    else:
        photo = tk.PhotoImage(master=window, file=str(APP_PNG))
        window.iconphoto(True, photo)
        window._brand_icon = photo


def place_dialog(window, parent, width, height):
    width = min(width, max(520, window.winfo_screenwidth()-60))
    height = min(height, max(260, window.winfo_screenheight()-80))
    if parent.winfo_viewable():
        x = parent.winfo_rootx()+(parent.winfo_width()-width)//2
        y = parent.winfo_rooty()+(parent.winfo_height()-height)//2
    else:
        x = (window.winfo_screenwidth()-width)//2
        y = (window.winfo_screenheight()-height)//2
    x = max(0, min(x, window.winfo_screenwidth()-width-12))
    y = max(0, min(y, window.winfo_screenheight()-height-40))
    window.geometry(f"{width}x{height}+{x}+{y}")


def app_mark(parent, *, size=48, background="#FFFFFF"):
    canvas = tk.Canvas(parent, width=size, height=size, background=background, highlightthickness=0)
    path = ASSETS / ("app_32.png" if size <= 32 else "app_48.png")
    photo = tk.PhotoImage(master=canvas, file=str(path))
    canvas.create_image(size / 2, size / 2, image=photo)
    canvas._brand_icon = photo
    return canvas
