"""Capture the real Windows Tk UI for layout review. Pillow is a CI-only dependency."""
from __future__ import annotations

import ctypes
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from PIL import ImageGrab

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from automated_gerber_compare.gui import App, SettingsDialog
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
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with patch("automated_gerber_compare.gui.load_settings", return_value=Settings(auto_start_get=False)):
            app = App(startup=False)
        try:
            app.geometry("1280x940+20+20")
            capture(app, destination / "01-welcome.png")
            import zipfile
            gerber = "%FSLAX26Y26*%\n%MOIN*%\n%ADD10C,0.01*%\nD10*\nX00000000Y00000000D03*\nM02*"
            paths = []
            for side in ("A", "B"):
                path = root / f"Board_Rev_{side}.zip"
                with zipfile.ZipFile(path, "w") as z:
                    for name in ["GF_Top_Layer.gdo", "GF_Bottom_Layer.gdo", "GF_Inner_1.gdo", "GF_Inner_2.gdo", "GF_Board_Outline.gdo", "GF_Solder_Mask_Top.gdo", "GF_Solder_Mask_Bottom.gdo"]:
                        z.writestr("Gerber/" + name, gerber)
                paths.append(path)
            app.path_a.set(str(paths[0]))
            app.path_b.set(str(paths[1]))
            app._render_inputs()
            app._show_validation(prepare_comparison(*paths, root / "output"))
            app._set_controls()
            capture(app, destination / "02-paired.png")
            dialog = SettingsDialog(app)
            try:
                capture(dialog, destination / "03-settings.png")
            finally:
                dialog.destroy()
        finally:
            app.destroy()


if __name__ == "__main__":
    main()
