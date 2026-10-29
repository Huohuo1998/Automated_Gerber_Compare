from __future__ import annotations

import os
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

from automated_gerber_compare.discovery import (apply_discovery, discover_installations,
                                               process_environment, read_environment_file)
from automated_gerber_compare.rcom import Cancelled, RcomError
from automated_gerber_compare.session import ValorSession
from automated_gerber_compare.settings import Settings, load_settings


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def installation(self, version, *, get=True, rcom=True):
        edir = self.root / f"Valor{version}" / f"vNPI_{version}" / "edir"
        for relative, enabled in (("get/get.exe", get), ("misc/rcom.exe", rcom)):
            if enabled:
                path = edir / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
        return edir

    def test_discovers_matching_tools_and_prefers_latest_complete_install(self):
        self.installation("2504")
        latest = self.installation("2604")
        self.installation("2704", get=False)
        found = discover_installations(roots=[self.root], environ={"PATH": ""})
        self.assertEqual(len(found), 3)
        self.assertEqual(found[0].edir, latest)
        cfg = apply_discovery(Settings(), found)
        self.assertEqual(cfg.get_exe, str(latest / "get/get.exe"))
        self.assertEqual(cfg.rcom_exe, str(latest / "misc/rcom.exe"))

    def test_manual_rcom_pins_companion_to_same_installation(self):
        old = self.installation("2504")
        self.installation("2604")
        cfg = Settings(rcom_exe=str(old / "misc/rcom.exe"))
        found = discover_installations(cfg, roots=[self.root], environ={"PATH": ""})
        selected = apply_discovery(cfg, found)
        self.assertEqual(selected.get_exe, str(old / "get/get.exe"))
        self.assertEqual(selected.rcom_exe, cfg.rcom_exe)

    def test_never_combines_partial_installs(self):
        old = self.installation("2504", get=False)
        self.installation("2604", rcom=False)
        cfg = Settings(rcom_exe=str(old / "misc/rcom.exe"))
        selected = apply_discovery(cfg, discover_installations(cfg, roots=[self.root], environ={"PATH": ""}))
        self.assertEqual(selected.get_exe, "")
        self.assertEqual(selected.rcom_exe, cfg.rcom_exe)

    def test_valid_explicit_pair_is_preserved(self):
        edir = self.installation("2504")
        cfg = Settings(rcom_exe=str(edir / "misc/rcom.exe"), get_exe=str(edir / "get/get.exe"))
        self.assertEqual(apply_discovery(cfg, []), cfg)

    def test_environment_file_discovers_edir_and_expands_forward_references(self):
        edir = self.installation("2604")
        env_file = self.root / "valor.env"
        env_file.write_text(f'VALOR_EDIR=%BASE%/edir\nBASE={edir.parent.as_posix()}\nPATH=%VALOR_EDIR%/misc;%PATH%\n', encoding="utf-8")
        env = {"VALOR_ENV_FILE": str(env_file), "PATH": "previous-path"}
        found = discover_installations(roots=[], environ=env)
        self.assertTrue(found[0].complete)
        values = read_environment_file(env_file, env)
        self.assertEqual(values["VALOR_EDIR"], edir.as_posix())
        self.assertTrue(values["PATH"].endswith("previous-path"))

    def test_environment_and_shared_data_directory_are_preserved(self):
        edir = self.installation("2604")
        shared = self.root / "vNPI_DIR"
        file = self.root / "env"
        file.write_text(f"VALOR_DIR={shared}\nVALOR_EDIR={edir}\nSALT_LICENSE_SERVER=test-license\n", encoding="utf-8")
        cfg = Settings(get_exe=str(edir / "get/get.exe"), rcom_exe=str(edir / "misc/rcom.exe"), env_file=str(file))
        with patch.dict(os.environ, {"PATH": "old-path"}, clear=True):
            env = process_environment(cfg)
        self.assertEqual(env["VALOR_DIR"], str(shared))
        self.assertEqual(env["VALOR_ENV_FILE"], str(file))
        self.assertEqual(env["SALT_LICENSE_SERVER"], "test-license")
        self.assertIn(str(edir / "misc"), env["PATH"])

    def test_no_invented_valor_dir_when_installer_env_is_missing(self):
        edir = self.installation("2604")
        with patch.dict(os.environ, {"PATH": ""}, clear=True):
            env = process_environment(Settings(rcom_exe=str(edir / "misc/rcom.exe")))
        self.assertNotIn("VALOR_DIR", env)
        self.assertEqual(env["VALOR_EDIR"], str(edir))

    def test_old_settings_file_migrates_with_defaults(self):
        path = self.root / "old.json"
        path.write_text('{"rcom_exe":"existing-rcom.exe","database":"my_db","port":11001}', encoding="utf-8")
        cfg = load_settings(path)
        self.assertEqual(cfg.database, "my_db")
        self.assertTrue(cfg.auto_start_get)
        self.assertEqual(cfg.get_exe, "")
        self.assertEqual(cfg.startup_timeout, 120)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.get = self.root / "with spaces" / "get.exe"
        self.rcom = self.root / "with spaces" / "rcom.exe"
        self.get.parent.mkdir()
        self.get.touch()
        self.rcom.touch()
        self.settings = Settings(get_exe=str(self.get), rcom_exe=str(self.rcom), startup_timeout=1)
        self.client = MagicMock()
        self.client.directory = self.root
        self.client.execute.return_value = ""
        self.cancel = threading.Event()
        self.process = MagicMock()
        self.process.poll.return_value = None
        self.process.pid = 4321
        self.controller = ValorSession()

    def test_existing_connection_is_reused_without_starting_get(self):
        with patch("automated_gerber_compare.session.subprocess.Popen") as launch:
            info = self.controller.ensure_ready(self.settings, self.client, self.cancel)
        self.assertTrue(info.reused)
        launch.assert_not_called()

    def test_launch_get_then_wait_until_rcom_is_ready(self):
        self.client.execute.side_effect = [RcomError("not connected"), RcomError("starting"), ""]
        with patch("automated_gerber_compare.session.port_is_open", return_value=False):
            with patch("automated_gerber_compare.session.subprocess.Popen", return_value=self.process) as launch:
                info = self.controller.ensure_ready(self.settings, self.client, self.cancel)
        self.assertFalse(info.reused)
        self.assertEqual(info.pid, 4321)
        args, kwargs = launch.call_args
        self.assertEqual(args[0], [str(self.get.resolve()), "-a11000"])
        self.assertFalse(kwargs["shell"])
        self.assertEqual(kwargs["cwd"], str(self.get.parent.resolve()))
        self.assertTrue((self.root / "get_startup.log").exists())
        self.process.terminate.assert_not_called()

    def test_busy_or_wrong_port_does_not_start_duplicate(self):
        self.client.execute.side_effect = RcomError("bad response")
        with patch("automated_gerber_compare.session.port_is_open", return_value=True):
            with patch("automated_gerber_compare.session.subprocess.Popen") as launch:
                with self.assertRaisesRegex(RcomError, "已占用"):
                    self.controller.ensure_ready(self.settings, self.client, self.cancel)
        launch.assert_not_called()

    def test_uncertain_existing_session_does_not_launch(self):
        self.client.execute.side_effect = RcomError("timeout", uncertain=True)
        with patch("automated_gerber_compare.session.subprocess.Popen") as launch:
            with self.assertRaises(RcomError):
                self.controller.ensure_ready(self.settings, self.client, self.cancel)
        launch.assert_not_called()

    def test_disabled_auto_start_only_reports_connection_error(self):
        self.client.execute.side_effect = RcomError("not connected")
        with patch("automated_gerber_compare.session.port_is_open", return_value=False):
            with patch("automated_gerber_compare.session.subprocess.Popen") as launch:
                with self.assertRaisesRegex(RcomError, "自动启动"):
                    self.controller.ensure_ready(replace(self.settings, auto_start_get=False), self.client, self.cancel)
        launch.assert_not_called()

    def test_owned_starting_process_is_not_started_again(self):
        self.controller.processes[11000] = self.process
        self.client.execute.side_effect = [RcomError("not ready"), ""]
        with patch("automated_gerber_compare.session.port_is_open", return_value=False):
            with patch("automated_gerber_compare.session.subprocess.Popen") as launch:
                info = self.controller.ensure_ready(self.settings, self.client, self.cancel)
        self.assertEqual(info.pid, 4321)
        launch.assert_not_called()

    def test_get_nonzero_exit_is_reported(self):
        self.client.execute.side_effect = RcomError("not connected")
        self.process.poll.return_value = 7
        with patch("automated_gerber_compare.session.port_is_open", return_value=False):
            with patch("automated_gerber_compare.session.subprocess.Popen", return_value=self.process):
                with self.assertRaisesRegex(RcomError, "退出码 7"):
                    self.controller.ensure_ready(self.settings, self.client, self.cancel)

    def test_timeout_preserves_get_and_explains_next_step(self):
        self.client.execute.side_effect = RcomError("still initializing")
        with patch("automated_gerber_compare.session.port_is_open", return_value=False):
            with patch("automated_gerber_compare.session.subprocess.Popen", return_value=self.process):
                with self.assertRaisesRegex(RcomError, "进程将保留"):
                    self.controller.ensure_ready(self.settings, self.client, self.cancel)
        self.process.kill.assert_not_called()
        self.process.terminate.assert_not_called()

    def test_cancel_during_startup_preserves_get(self):
        calls = 0
        def probe(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls > 1:
                self.cancel.set()
            raise RcomError("not ready")
        self.client.execute.side_effect = probe
        with patch("automated_gerber_compare.session.port_is_open", return_value=False):
            with patch("automated_gerber_compare.session.subprocess.Popen", return_value=self.process):
                with self.assertRaises(Cancelled):
                    self.controller.ensure_ready(self.settings, self.client, self.cancel)
        self.process.kill.assert_not_called()

    def test_missing_get_has_actionable_message(self):
        self.client.execute.side_effect = RcomError("not connected")
        with patch("automated_gerber_compare.session.port_is_open", return_value=False):
            with patch("automated_gerber_compare.session.discover_installations", return_value=[]):
                with self.assertRaisesRegex(RcomError, "get.exe"):
                    self.controller.ensure_ready(replace(self.settings, get_exe=""), self.client, self.cancel)


if __name__ == "__main__":
    unittest.main()
