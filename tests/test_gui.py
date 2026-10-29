"""Run on Windows or an X display; safely skipped on a headless Linux host."""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from test_inputs import GERBER, make_zip
from test_execution import FakeValor


@unittest.skipUnless(sys.platform == "win32" or os.environ.get("DISPLAY"), "Desktop display is not available")
class GuiSmokeTests(unittest.TestCase):
    def setUp(self):
        from automated_gerber_compare.gui import App
        from automated_gerber_compare.settings import Settings
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        exe = self.root / "rcom.exe"
        exe.touch()
        with patch("automated_gerber_compare.gui.load_settings", return_value=Settings(rcom_exe=str(exe), port=41072)):
            self.app = App(startup=False)
        self.addCleanup(self.app.destroy)
        self.app.withdraw()
        self.app.output.set(str(self.root / "out"))
        self.a = make_zip(self.root / "a.zip", {"top.gtl": GERBER, "mask.gts": GERBER})

    def wait_for_worker(self):
        deadline = time.monotonic() + 8
        while self.app.busy and time.monotonic() < deadline:
            self.app.update()
            time.sleep(0.01)
        self.app.update()
        self.assertFalse(self.app.busy, "GUI worker did not finish")

    def choose(self, side, path):
        with patch("automated_gerber_compare.gui.filedialog.askopenfilename", return_value=str(path)):
            self.app._choose_zip(side)

    def test_selection_validation_worker_and_comparison(self):
        self.assertEqual(str(self.app.validate_button["state"]), "disabled")
        self.choose("A", self.a)
        self.assertEqual(str(self.app.validate_button["state"]), "disabled")
        self.choose("B", self.a)
        self.assertEqual(str(self.app.validate_button["state"]), "normal")
        self.app._validate()
        self.wait_for_worker()
        self.assertTrue(self.app.prepared.validation.can_compare)
        self.assertEqual(str(self.app.start_button["state"]), "normal")
        fake = FakeValor(counts=(0, 65))
        with patch("automated_gerber_compare.gui.messagebox.askyesno", return_value=True):
            with patch("automated_gerber_compare.rcom.subprocess.Popen", side_effect=fake):
                self.app._start()
                self.wait_for_worker()
        self.assertEqual(self.app.run.status, "different")
        self.assertEqual(str(self.app.open_button["state"]), "normal")
        self.assertEqual(str(self.app.export_button["state"]), "normal")
        self.assertEqual(str(self.app.start_button["state"]), "disabled")
        target = self.root / "saved_report.html"
        with patch("automated_gerber_compare.gui.filedialog.asksaveasfilename", return_value=str(target)):
            self.app._export()
        self.assertIn("data:image/png;base64,", target.read_text(encoding="utf-8"))

    def test_missing_layer_blocks_start_and_reselection_clears_result(self):
        b = make_zip(self.root / "b.zip", {"top.gtl": GERBER})
        self.choose("A", self.a)
        self.choose("B", b)
        self.app._validate()
        self.wait_for_worker()
        self.assertFalse(self.app.prepared.validation.can_compare)
        self.assertEqual(str(self.app.start_button["state"]), "disabled")
        self.choose("B", self.a)
        self.assertIsNone(self.app.prepared)
        self.assertEqual(str(self.app.start_button["state"]), "disabled")

    def test_settings_dialog_and_connection_controls(self):
        from automated_gerber_compare.gui import SettingsDialog
        self.assertEqual(str(self.app.session_button["state"]), "normal")
        self.app.deiconify()
        self.app.update()
        dialog = SettingsDialog(self.app)
        dialog.update_idletasks()
        self.assertIn("get_exe", dialog.vars)
        self.assertIn("env_file", dialog.vars)
        self.assertTrue(dialog.auto_start.get())
        self.assertTrue(dialog.save_button.winfo_ismapped())
        self.assertLessEqual(dialog.save_button.winfo_rooty() + dialog.save_button.winfo_height(),
                             dialog.winfo_rooty() + dialog.winfo_height())
        dialog.destroy()

    def test_compact_window_preserves_workspace(self):
        # Hosted Windows uses a small screen; lift its default WM sizing cap
        # to exercise both compact and full layouts in this one fixture.
        self.app.maxsize(2200, 1600)
        self.app.deiconify()
        self.app.geometry("1080x760")
        self.app.update()
        self.assertFalse(self.app.metrics_frame.winfo_ismapped())
        self.assertGreater(self.app.pair_tree.winfo_height(), 40)
        self.app.geometry("1280x940")
        self.app.update()
        self.assertTrue(self.app.metrics_frame.winfo_ismapped())

    def test_discovered_paths_update_ui_without_overwriting_inputs(self):
        self.choose("A", self.a)
        self.choose("B", self.a)
        from dataclasses import replace
        settings = replace(self.app.settings, auto_start_get=False)
        self.app.backend_busy = True
        self.app.events.put(("discovered", (settings, [], "")))
        self.app.after_cancel(self.app._poll_job)
        self.app._poll()
        self.assertFalse(self.app.backend_busy)
        self.assertEqual(self.app.path_a.get(), str(self.a))
        self.assertEqual(str(self.app.validate_button["state"]), "normal")


if __name__ == "__main__":
    unittest.main()
