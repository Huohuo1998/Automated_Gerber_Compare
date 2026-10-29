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
            self.app = App()
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


if __name__ == "__main__":
    unittest.main()
