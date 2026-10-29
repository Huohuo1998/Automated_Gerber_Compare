from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from automated_gerber_compare.cli import main as cli_main
from automated_gerber_compare.parsers import check_import_report, nonnegative_integer, parse_bounds
from automated_gerber_compare.rcom import Cancelled, EndpointLock, RcomClient, RcomError
from automated_gerber_compare.service import run_comparison
from automated_gerber_compare.settings import Settings
from automated_gerber_compare.validation import prepare_comparison
from test_inputs import GERBER, make_zip

# A valid, tiny PNG for simulated screenshots; it is not a Valor comparison image.
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j0WQAAAAASUVORK5CYII=")


class FakeProcess:
    def __init__(self, output=b"", returncode=0):
        self.output = output
        self.returncode = returncode

    def communicate(self, timeout=None):
        return self.output, b""

    def kill(self):
        self.returncode = -9


class FakeValor:
    """A deterministic protocol fixture for tests only; never available in the app UI."""
    def __init__(self, counts=(0, 65), fail="", bad_count=False, bad_import=False, no_image=False):
        self.commands = []
        self.inputs = []
        self.counts = iter(counts)
        self.fail = fail
        self.bad_count = bad_count
        self.bad_import = bad_import
        self.no_image = no_image
        self.argv = []

    def __call__(self, argv, **kwargs):
        self.argv.append(argv)
        assert kwargs["shell"] is False
        lmc = argv[-1]
        self.commands.append(lmc)
        if self.fail and lmc.startswith(self.fail):
            return FakeProcess(b"intentional failure", 5)
        if not lmc.startswith("COM "):
            return FakeProcess()
        parts = lmc[4:].split(",")
        name = parts[0]
        args = dict(p.split("=", 1) for p in parts[1:])
        output = ""
        if name == "input_manual_set":
            self.inputs.append(args)
        elif name == "input_manual":
            layers = " ".join(i["layer"].lower() for i in self.inputs)
            statuses = ["ok"] * len(self.inputs)
            if self.bad_import:
                statuses[0] = "fail"
            Path(args["script_path"]).write_text(f"set giTSTATUS = ({' '.join(statuses)})\nset giTLAYER = ({layers})", encoding="utf-8")
        elif name == "open_entity":
            output = "0"  # Provided real-world run record returns group 0.
        elif name == "compare_layers":
            output = "unexpected response" if self.bad_count else str(next(self.counts))
        elif name == "sel_multi_feat" and args["operation"] == "select":
            output = "3247"
        elif name == "info":
            # The viewport must use both layers, including B's larger positive extent.
            output_text = "LIMITS:xmin=-3,ymin=-2,xmax=1,ymax=1" if args["out_file"].endswith("_A.txt") else "LIMITS:xmin=0,ymin=0,xmax=8,ymax=5"
            Path(args["out_file"]).write_text(output_text)
        elif name == "zoom_pv_open":
            output = "7"
        elif name == "zoom_pv_print" and not self.no_image:
            assert args["popview"] == "7"
            Path(args["fname"]).write_bytes(PNG)
        return FakeProcess(output.encode())


class ParserTests(unittest.TestCase):
    def test_count_is_strict_and_not_a_status(self):
        self.assertEqual(nonnegative_integer("65\n", "compare"), 65)
        self.assertEqual(nonnegative_integer("COMANS=0", "compare"), 0)
        for text in ("", "-1", "0\n65", "error", "65.0"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    nonnegative_integer(text, "compare")

    def test_limits_union_and_margin(self):
        a = parse_bounds("LIMITS:xmin=-0.7308,ymin=-2.4991,xmax=6.4001,ymax=2.0334")
        b = parse_bounds("LIMITS:xmin=-1,ymin=-2,xmax=7,ymax=3")
        merged = a.union_with_margin(b, 0.2)
        self.assertAlmostEqual(merged.xmin, -1.2)
        self.assertAlmostEqual(merged.ymin, -2.6991)
        self.assertAlmostEqual(merged.xmax, 7.2)
        self.assertAlmostEqual(merged.ymax, 3.2)
        for text in ("", "xmin=0,ymin=0,xmax=-1,ymax=1", "xmin=nan,ymin=0,xmax=1,ymax=1", "xmin=1e999,ymin=0,xmax=1e999,ymax=1"):
            with self.assertRaises(ValueError):
                parse_bounds(text)

    def test_import_report_requires_all_layers_and_ok(self):
        check_import_report("set giTSTATUS = (ok ok)\nset giTLAYER = (x.a x.b)", ["X.A", "X.B"])
        for text in ("", "set giTSTATUS = (ok fail)\nset giTLAYER = (x.a x.b)", "set giTSTATUS = (ok)\nset giTLAYER = (x.a)", "set giTSTATUS = (ok ok)\nset giTLAYER = (x.a x.a)"):
            with self.assertRaises(ValueError):
                check_import_report(text, ["x.A", "x.B"])


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.exe = self.root / "path with spaces" / "rcom.exe"
        self.exe.parent.mkdir()
        self.exe.touch()
        self.settings = Settings(rcom_exe=str(self.exe), port=41069)
        files = {"nested/GF_Top_Layer.gdo": GERBER, "deeper/GF_Solder_Mask_Top.gdo": GERBER}
        self.a = make_zip(self.root / "a.zip", files)
        self.b = make_zip(self.root / "b.zip", files)
        self.prepared = prepare_comparison(self.a, self.b, self.root / "outputs with spaces")

    def run_fake(self, fake, **kwargs):
        with patch("automated_gerber_compare.rcom.subprocess.Popen", side_effect=fake):
            return run_comparison(self.prepared, self.settings, **kwargs)

    def test_full_pipeline_order_dynamic_values_and_portable_report(self):
        fake = FakeValor()
        run = self.run_fake(fake)
        self.assertEqual(run.status, "different", run.error)
        self.assertEqual([l.status for l in run.layers], ["consistent", "different"])
        self.assertEqual([l.difference_boxes for l in run.layers], [0, 65])
        c = fake.commands
        compares = [i for i, s in enumerate(c) if s.startswith("COM compare_layers,")]
        cleans = [i for i, s in enumerate(c) if s == "COM sel_delete"]
        captures = [i for i, s in enumerate(c) if s.startswith("COM zoom_pv_open,")]
        self.assertLess(max(compares), min(cleans))
        self.assertLess(max(cleans), min(captures))
        self.assertIn("AUX set_group,group=0", c)
        self.assertIn("x1=-3.20000000,y1=-2.20000000,x2=8.20000000,y2=5.20000000", c[captures[0]])
        self.assertTrue(all(argv[0] == str(self.exe) and argv[1] == "-a41069" for argv in fake.argv))
        report = (run.directory / "report.html").read_text(encoding="utf-8")
        self.assertEqual(report.count("data:image/png;base64,"), 2)
        self.assertIn("网格数不等于独立缺陷数", report)
        with zipfile.ZipFile(run.directory / "report_bundle.zip") as z:
            self.assertIn("report.html", z.namelist())
            self.assertIn("rcom.jsonl", z.namelist())
            self.assertIn("images/001.jpg", z.namelist())
            self.assertFalse(any(n.startswith("input_") for n in z.namelist()))
        saved = json.loads((run.directory / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["status"], "different")

    def test_zero_differences_are_consistent(self):
        run = self.run_fake(FakeValor(counts=(0, 0)))
        self.assertEqual(run.status, "consistent", run.error)

    def test_import_failure_stops_before_compare(self):
        fake = FakeValor(bad_import=True)
        run = self.run_fake(fake)
        self.assertEqual(run.status, "failed")
        self.assertFalse(any(s.startswith("COM compare_layers") for s in fake.commands))
        self.assertTrue((run.directory / "report.html").exists())

    def test_invalid_compare_answer_never_becomes_zero(self):
        run = self.run_fake(FakeValor(bad_count=True))
        self.assertEqual(run.status, "failed")
        self.assertIsNone(run.layers[0].difference_boxes)
        self.assertEqual(run.layers[0].status, "error")

    def test_rcom_nonzero_exit_stops_and_preserves_error_evidence(self):
        fake = FakeValor(fail="COM compare_layers")
        run = self.run_fake(fake)
        self.assertEqual(run.status, "failed")
        self.assertIn("退出码 5", run.error)
        log = [json.loads(s) for s in (run.directory / "rcom.jsonl").read_text().splitlines()]
        self.assertTrue(any(r["returncode"] == 5 and r["error"] for r in log))
        self.assertFalse(any(s.startswith("COM zoom_pv_open") for s in fake.commands))

    def test_missing_screenshot_is_not_success(self):
        run = self.run_fake(FakeValor(no_image=True))
        self.assertEqual(run.status, "failed")
        self.assertIn("截图", run.error)
        self.assertEqual(run.layers[0].status, "error")
        self.assertEqual(run.layers[1].status, "pending_image")

    def test_cancel_before_first_command_creates_cancelled_report(self):
        event = threading.Event()
        event.set()
        fake = FakeValor()
        run = self.run_fake(fake, cancel=event)
        self.assertEqual(run.status, "cancelled")
        self.assertEqual(fake.commands, [])

    def test_unsafe_static_path_rejected_before_valor(self):
        prepared = prepare_comparison(self.a, self.b, self.root / "comma,dir")
        with patch("automated_gerber_compare.rcom.subprocess.Popen") as process:
            with self.assertRaises(ValueError):
                run_comparison(prepared, self.settings)
            process.assert_not_called()

    def test_invalid_validation_blocks_before_valor(self):
        bad = make_zip(self.root / "mismatch.zip", {"other.gbr": GERBER})
        prepared = prepare_comparison(self.a, bad, self.root / "invalid")
        with patch("automated_gerber_compare.rcom.subprocess.Popen") as process:
            with self.assertRaises(ValueError):
                run_comparison(prepared, self.settings)
            process.assert_not_called()

    def test_changed_snapshot_blocks_before_valor(self):
        self.prepared.validation.pairs[0].a.path.write_text("tampered")
        with patch("automated_gerber_compare.rcom.subprocess.Popen") as process:
            with self.assertRaises(ValueError):
                run_comparison(self.prepared, self.settings)
            process.assert_not_called()

    def test_same_prepared_session_cannot_run_twice(self):
        self.run_fake(FakeValor(counts=(0, 0)))
        original_plan = (self.prepared.directory / "command_plan.json").read_bytes()
        with self.assertRaises(FileExistsError):
            self.run_fake(FakeValor(counts=(0, 0)))
        self.assertEqual((self.prepared.directory / "command_plan.json").read_bytes(), original_plan)

    def test_cli_compare_requires_explicit_confirmation(self):
        with patch("automated_gerber_compare.cli.load_settings", return_value=self.settings):
            with patch("automated_gerber_compare.cli.run_comparison") as run:
                with patch("builtins.print"):
                    code = cli_main(["compare", str(self.a), str(self.b), "--output", str(self.root / "cli")])
                self.assertEqual(code, 2)
                run.assert_not_called()

    def test_report_escapes_untrusted_filenames(self):
        # POSIX permits these names, and ZIP metadata is portable to Windows staging.
        a = make_zip(self.root / "html_a.zip", {"<script>alert(1)</script>.gbr": GERBER})
        prepared = prepare_comparison(a, a, self.root / "escaped")
        with patch("automated_gerber_compare.rcom.subprocess.Popen", side_effect=FakeValor(counts=(0,))):
            run = run_comparison(prepared, self.settings)
        text = (run.directory / "report.html").read_text(encoding="utf-8")
        self.assertNotIn("<script>", text)
        self.assertIn("&lt;script&gt;", text)


class TransportTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "POSIX executable fixture; Windows GUI path has separate tests")
    def test_real_subprocess_keeps_whole_command_as_one_argument(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "fake rcom"
            script.write_text(f"#!{sys.executable}\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n", encoding="utf-8")
            script.chmod(0o700)
            client = RcomClient(Settings(rcom_exe=str(script), port=12345), root, threading.Event())
            lmc = "COM info,args=-t layer -e demo/1/top.A,out_file=/path with spaces/test.txt"
            output = client.execute(lmc)
            self.assertEqual(json.loads(output), ["-a12345", lmc])

    def test_endpoint_lock_prevents_overlap(self):
        with EndpointLock(41070):
            with self.assertRaises(RcomError):
                with EndpointLock(41070):
                    self.fail("second lock unexpectedly acquired")
        with EndpointLock(41070):
            pass

    def test_timeout_kills_client_and_marks_server_state_uncertain(self):
        with tempfile.TemporaryDirectory() as tmp:
            process = FakeProcess()
            def communicate(timeout=None):
                if process.returncode == -9:
                    return b"", b""
                raise subprocess.TimeoutExpired("rcom", timeout)
            process.communicate = communicate
            client = RcomClient(Settings(rcom_exe="rcom.exe", command_timeout=1), Path(tmp), threading.Event())
            with patch("automated_gerber_compare.rcom.subprocess.Popen", return_value=process):
                with patch("automated_gerber_compare.rcom.time.monotonic", side_effect=[0, 2, 3]):
                    with self.assertRaises(RcomError) as raised:
                        client.execute("TST")
            self.assertTrue(raised.exception.uncertain)
            self.assertEqual(process.returncode, -9)

    def test_cancel_during_command_marks_uncertain(self):
        with tempfile.TemporaryDirectory() as tmp:
            event = threading.Event()
            process = FakeProcess()
            def communicate(timeout=None):
                if process.returncode == -9:
                    return b"", b""
                event.set()
                raise subprocess.TimeoutExpired("rcom", timeout)
            process.communicate = communicate
            client = RcomClient(Settings(rcom_exe="rcom.exe"), Path(tmp), event)
            with patch("automated_gerber_compare.rcom.subprocess.Popen", return_value=process):
                with self.assertRaises(Cancelled) as raised:
                    client.execute("TST")
            self.assertTrue(raised.exception.uncertain)

    def test_uncertain_server_failure_does_not_send_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = make_zip(root / "x.zip", {"top.gtl": GERBER})
            prepared = prepare_comparison(archive, archive, root / "out")
            fake = FakeValor(counts=(0,))
            calls = []
            def execute(client, lmc, **kwargs):
                calls.append(lmc)
                if lmc.startswith("COM compare_layers"):
                    raise RcomError("simulated timeout", uncertain=True)
                process = fake(["rcom", lmc], shell=False)
                return process.communicate()[0].decode()
            with patch.object(RcomClient, "execute", execute):
                run = run_comparison(prepared, Settings(rcom_exe=str(archive), port=41071))
            self.assertEqual(run.status, "failed")
            self.assertTrue(calls[-1].startswith("COM compare_layers"))
            self.assertIn("可能仍在执行", run.warnings[0])


if __name__ == "__main__":
    unittest.main()
