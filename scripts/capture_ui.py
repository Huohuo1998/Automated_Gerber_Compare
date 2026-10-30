"""Capture the real Windows Tk UI for layout review. Pillow is a CI-only dependency."""
from __future__ import annotations

import ctypes
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from PIL import ImageGrab

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from automated_gerber_compare.gui import App, SettingsDialog
from automated_gerber_compare.dialogs import MessageWindow
from automated_gerber_compare.settings import Settings
from automated_gerber_compare.validation import prepare_comparison


def capture(window, output):
    window.update()
    window.lift()
    window.update_idletasks()
    user32 = ctypes.windll.user32
    user32.GetParent.argtypes = [ctypes.c_void_p]
    user32.GetParent.restype = ctypes.c_void_p
    hwnd = user32.GetParent(window.winfo_id()) or window.winfo_id()
    ImageGrab.grab(window=hwnd).save(output)


def main():
    destination = Path(sys.argv[1] if len(sys.argv) > 1 else "ui-preview")
    destination.mkdir(parents=True, exist_ok=True)
    if os.name == "nt" and os.environ.get("GITHUB_ACTIONS") == "true":
        # Use a large real desktop so Windows paints the entire capture, including
        # controls which would be clipped by the hosted runner's 1024x768 default.
        import win32api
        import win32con
        mode = win32api.EnumDisplaySettings(None, win32con.ENUM_CURRENT_SETTINGS)
        mode.PelsWidth, mode.PelsHeight = 1920, 1080
        mode.Fields |= win32con.DM_PELSWIDTH | win32con.DM_PELSHEIGHT
        result = win32api.ChangeDisplaySettings(mode, 0)
        print(f"Capture desktop resolution result: {result}")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with patch("automated_gerber_compare.gui.load_settings", return_value=Settings()):
            app = App(startup=False)
        try:
            app.maxsize(2200, 1600)
            app.geometry("1280x940+20+20")
            capture(app, destination / "01-welcome.png")
            import zipfile
            gerber = "%FSLAX26Y26*%\n%MOIN*%\n%ADD10C,0.01*%\nD10*\nX00000000Y00000000D03*\nM02*"
            paths = []
            for side in ("A", "B"):
                path = root / f"Board_Rev_{side}.zip"
                with zipfile.ZipFile(path, "w") as z:
                    for name in ["GF_Top_Layer.gdo", "GF_Bottom_Layer.gdo", "GF_Inner_1.gdo", "GF_Inner_2.gdo", "GF_Board_Outline.gdo", "GF_Solder_Mask_Top.gdo", "GF_Solder_Mask_Bottom.gdo"]:
                        content = gerber.replace("26Y26", "24Y24") if side == "B" and "Mask" in name else gerber
                        z.writestr("Gerber/" + name, content)
                paths.append(path)
            app.path_a.set(str(paths[0]))
            app.path_b.set(str(paths[1]))
            app._render_inputs()
            app._show_validation(prepare_comparison(*paths, root / "output"))
            app._set_controls()
            capture(app, destination / "02-paired.png")
            app.geometry("1080x760+20+20")
            capture(app, destination / "04-compact.png")
            dialog = SettingsDialog(app)
            try:
                capture(dialog, destination / "03-settings.png")
            finally:
                dialog.destroy()
            for name,title,message,confirm,kind in [
                ("05-confirmation.png", "开始图形对比", "本次将比较 7 对图层，包含 2 项校验提醒。\n\n确认资料后，程序将连接 Valor 并生成带截图的报告。\n\n现在开始？", True, "info"),
                ("06-layer-details.png", "阻焊 / 顶层", "A：Gerber/GF_Solder_Mask_Top.gdo\n\nB：Gerber/GF_Solder_Mask_Top.gdo\n\n校验提醒：\n两侧导入格式不同，将分别使用各自文件头参数；保持原坐标，不自动平移对齐。", False, "info"),
                ("07-warning.png", "连接需要检查", "Valor 尚未连接。\n请检查本机连接状态或打开 Valor 设置。\n\n运行记录已保存在本次工作目录。", False, "warning"),
            ]:
                dialog = MessageWindow(app, title, message, confirm=confirm, kind=kind)
                try:
                    capture(dialog, destination/name)
                finally:
                    dialog.destroy()
        finally:
            app.destroy()


if __name__ == "__main__":
    main()
