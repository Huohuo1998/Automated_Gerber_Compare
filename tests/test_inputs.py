from __future__ import annotations

import json
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path

from automated_gerber_compare.analyzer import layer_role, parse_drill_format, parse_gerber_format
from automated_gerber_compare.archives import ArchiveError, ArchiveLimits, extract_archive
from automated_gerber_compare.commands import build_plan, command, import_command
from automated_gerber_compare.settings import Settings, load_settings, save_settings
from automated_gerber_compare.validation import prepare_comparison, verify_snapshot, warnings_for_pair

GERBER = "%FSLAX26Y26*%\n%MOIN*%\n%ADD10C,0.01*%\nD10*\nX00000000Y00000000D03*\nM02*\n"
DRILL = "; Format : 2.4 / Absolute / Inch / Trailing\nM48\nINCH,LZ\nFMAT,2\nT01C0.01\n%\nT01\nX0012Y0023\nM30\n"


def make_zip(path: Path, files: dict[str, str | bytes]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return path


class InputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def prepare(self, a, b):
        return prepare_comparison(make_zip(self.root / "a.zip", a), make_zip(self.root / "b.zip", b), self.root / "out")

    def test_recursive_inventory_all_artwork_and_drills(self):
        names = ["GF_Top_Layer.gdo", "GF_Bottom_Layer.gdo", "GF_Inner_1.gdo", "GF_Inner_2.gdo", "GF_Board_Outline.gdo", "GF_Drill_Drawing_Through.gdo", "GF_Solder_Mask_Top.gdo", "GF_Solder_Mask_Bottom.gdo"]
        a = {f"板卡A/任意层级/Gerber/{n}": GERBER for n in names}
        b = {f"different/root/{n.lower()}": GERBER for n in names}
        for n in ["ThruHoleNonPlated.ncd", "ThruHolePlated.ncd"]:
            a[f"a/drills/{n}"] = b[f"b/NCDrill/{n}"] = DRILL
        a["a.ipc"] = "netlist"
        b["doc.pdf"] = b"%PDF-1.5"
        b["extra.dwg"] = b"drawing"
        prepared = self.prepare(a, b)
        v = prepared.validation
        self.assertTrue(v.can_compare, v.issues)
        self.assertEqual(len(v.pairs), 10)
        self.assertEqual(len(v.files), 23)
        self.assertEqual(len({p.layer_base for p in v.pairs}), 10)
        self.assertTrue(all(len(p.layer_result) < 64 for p in v.pairs))
        self.assertEqual(v.archive_hashes.keys(), {"A", "B"})
        verify_snapshot(prepared)

    def test_missing_layers_block(self):
        v = self.prepare({"GF_Top_Layer.gdo": GERBER}, {"GF_Top_Layer.gdo": GERBER, "GF_Inner_3.gdo": GERBER}).validation
        self.assertFalse(v.can_compare)
        self.assertEqual([i.code for i in v.errors], ["unmatched_layer"])

    def test_ambiguous_semantic_role_blocks(self):
        v = self.prepare({"x/GF_Top_Layer.gdo": GERBER, "y/GF_Top_Layer.gdo": GERBER}, {"GF_Top_Layer.gdo": GERBER}).validation
        self.assertFalse(v.can_compare)
        self.assertIn("ambiguous_layer", [i.code for i in v.errors])

    def test_generic_same_names_are_warning_and_permitted(self):
        v = self.prepare({"x/custom.gbr": GERBER}, {"y/CUSTOM.GBR": GERBER}).validation
        self.assertTrue(v.can_compare)
        self.assertIn("filename_match", [i.code for i in v.warnings])

    def test_generic_different_names_are_not_guessed(self):
        v = self.prepare({"revA.gbr": GERBER}, {"revB.gbr": GERBER}).validation
        self.assertFalse(v.can_compare)
        self.assertEqual(len(v.errors), 2)

    def test_extension_alias_maps_same_known_layer(self):
        v = self.prepare({"GF_Top_Layer.gdo": GERBER}, {"board.gtl": GERBER}).validation
        self.assertTrue(v.can_compare)
        self.assertEqual(v.pairs[0].key, "signal:top")

    def test_only_mask_pair_does_not_require_netlist(self):
        v = self.prepare({"GF_Solder_Mask_Top.gdo": GERBER}, {"GF_Solder_Mask_Top.gdo": GERBER}).validation
        self.assertTrue(v.can_compare)

    def test_no_artwork_blocks(self):
        v = self.prepare({"test.ipc": "ipc"}, {"readme.txt": "hello"}).validation
        self.assertFalse(v.can_compare)
        self.assertEqual(len(v.errors), 2)

    def test_unsupported_or_empty_artwork_blocks(self):
        for content in ("", "G01*\nX1Y1D01*\nM02*"):
            with self.subTest(content=content):
                v = self.prepare({"GF_Top_Layer.gdo": content}, {"GF_Top_Layer.gdo": GERBER}).validation
                self.assertFalse(v.can_compare)
                self.assertIn("unsupported_format", [i.code for i in v.errors])

    def test_content_identification_in_unknown_extension(self):
        v = self.prepare({"film.txt": GERBER}, {"film.txt": GERBER}).validation
        self.assertTrue(v.can_compare)
        self.assertEqual(v.files[0].kind, "gerber")

    def test_each_side_keeps_own_precision(self):
        prepared = self.prepare({"GF_Top_Layer.gdo": GERBER.replace("26Y26", "24Y24")}, {"GF_Top_Layer.gdo": GERBER})
        v = prepared.validation
        self.assertTrue(v.can_compare)
        self.assertIn("different_formats", [i.code for i in v.warnings])
        pair = v.pairs[0]
        self.assertIn("nf2=4", import_command(pair.a, pair.layer_a, "test"))
        self.assertIn("nf2=6", import_command(pair.b, pair.layer_b, "test"))

    def test_warnings_are_associated_with_the_correct_layer(self):
        prepared = self.prepare({"GF_Top_Layer.gdo": GERBER, "GF_Solder_Mask_Top.gdo": GERBER},
                                {"GF_Top_Layer.gdo": GERBER.replace("26Y26", "24Y24"), "GF_Solder_Mask_Top.gdo": GERBER})
        pairs = {p.key:p for p in prepared.validation.pairs}
        top = warnings_for_pair(prepared.validation, pairs["signal:top"])
        self.assertEqual([i.code for i in top], ["different_formats"])
        self.assertEqual(warnings_for_pair(prepared.validation, pairs["mask:top"]), [])

    def test_filename_warning_is_associated_by_file_identity(self):
        prepared = self.prepare({"unknown.gbr": GERBER}, {"UNKNOWN.GBR": GERBER})
        notices = warnings_for_pair(prepared.validation, prepared.validation.pairs[0])
        self.assertEqual({i.side for i in notices}, {"A", "B"})

    def test_staged_snapshot_change_is_detected(self):
        prepared = self.prepare({"top.gtl": GERBER}, {"top.gtl": GERBER})
        prepared.validation.pairs[0].a.path.write_text("changed")
        with self.assertRaisesRegex(ValueError, "快照发生变化"):
            verify_snapshot(prepared)

    def test_original_zip_change_does_not_change_validated_snapshot(self):
        prepared = self.prepare({"top.gtl": GERBER}, {"top.gtl": GERBER})
        (self.root / "a.zip").write_bytes(b"changed")
        verify_snapshot(prepared)

    def test_identical_archives_warn_but_allow(self):
        archive = make_zip(self.root / "same.zip", {"top.gtl": GERBER})
        v = prepare_comparison(archive, archive, self.root / "out").validation
        self.assertTrue(v.can_compare)
        self.assertIn("same_archive", [i.code for i in v.warnings])

    def test_both_invalid_archives_report_errors(self):
        bad = self.root / "bad.zip"
        bad.write_text("not zip")
        v = prepare_comparison(bad, self.root / "missing.zip", self.root / "out").validation
        self.assertFalse(v.can_compare)
        self.assertEqual(sum(i.code == "archive_error" for i in v.errors), 2)

    def test_windows_zip_gbk_name(self):
        # Craft an unflagged GBK directory name, as produced by common Windows ZIP tools.
        utf8_name = "中文/top.gtl"
        raw_name = utf8_name.encode("gbk")
        dummy_name = "x" * len(raw_name)
        archive = make_zip(self.root / "gbk.zip", {dummy_name: GERBER})
        data = archive.read_bytes().replace(dummy_name.encode("ascii"), raw_name)
        archive.write_bytes(data)
        records = extract_archive(archive, self.root / "gbk_out", "A")
        self.assertEqual(records[0].relative_path, utf8_name)

    def test_zip_path_traversal_or_special_files_rejected(self):
        for n, name in enumerate(["../escape.gdo", "/absolute.gdo", "C:/evil.gdo", "a/../../evil.gdo", "a\\..\\evil.gdo"]):
            with self.subTest(name=name):
                archive = make_zip(self.root / f"unsafe{n}.zip", {name: GERBER})
                with self.assertRaises(ArchiveError):
                    extract_archive(archive, self.root / f"unsafe{n}", "A")
        archive = self.root / "symlink.zip"
        with zipfile.ZipFile(archive, "w") as z:
            info = zipfile.ZipInfo("link")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(info, "../outside")
        with self.assertRaises(ArchiveError):
            extract_archive(archive, self.root / "link_out", "A")

    def test_zip_case_collision_and_limits_rejected(self):
        archive = make_zip(self.root / "collision.zip", {"Top.gdo": GERBER, "top.gdo": GERBER})
        with self.assertRaises(ArchiveError):
            extract_archive(archive, self.root / "case_out", "A")
        archive = make_zip(self.root / "large.zip", {"top.gtl": GERBER})
        with self.assertRaises(ArchiveError):
            extract_archive(archive, self.root / "limited", "A", ArchiveLimits(max_file_bytes=10))

    def test_plan_uses_suffixes_and_runtime_placeholders(self):
        prepared = self.prepare({"a/top.gtl": GERBER}, {"b/top.gtl": GERBER})
        plan = build_plan(prepared, Settings(), "test")
        text = json.dumps(plan)
        self.assertIn(".A", text)
        self.assertIn(".B", text)
        self.assertIn(".Result", text)
        self.assertIn("${zoom_pv_open}", text)
        self.assertIn("${open_entity}", text)

    def test_lmc_injection_delimiters_rejected(self):
        with self.assertRaises(ValueError):
            command("info", out_file=self.root / "a,b.txt")
        with self.assertRaises(ValueError):
            command("info", args="foo\nCOM delete_entity")


class FormatAndSettingsTests(unittest.TestCase):
    def test_gerber_header_mm_trailing_incremental(self):
        fmt = parse_gerber_format("%FSTIX35Y35*%\n%MOMM*%")
        self.assertEqual((fmt.units, fmt.zeroes, fmt.coordinates, fmt.nf1, fmt.nf2), ("mm", "trailing", "incremental", 3, 5))

    def test_mismatched_xy_precision_rejected(self):
        with self.assertRaises(ValueError):
            parse_gerber_format("%FSLAX24Y26*%\n%MOIN*%")

    def test_drill_formats_and_nonplated_precedence(self):
        fmt = parse_drill_format(DRILL)
        self.assertEqual((fmt.format, fmt.nf1, fmt.nf2, fmt.zeroes), ("Excellon2", 2, 4, "trailing"))
        self.assertEqual(layer_role("ThruHoleNonPlated.ncd", "drill")[0], "drill:through:npth")
        self.assertEqual(layer_role("ThruHolePlated.ncd", "drill")[0], "drill:through:pth")

    def test_drill_decimal_and_unknown_implicit(self):
        self.assertTrue(parse_drill_format("M48\nMETRIC,TZ\n%\nX12.3Y3.2\n").decimal)
        with self.assertRaises(ValueError):
            parse_drill_format("M48\nMETRIC,TZ\n%\nX123Y32\n")
        with self.assertRaises(ValueError):
            parse_drill_format(DRILL.replace("INCH,LZ", "METRIC,TZ"))

    def test_settings_roundtrip_and_invalid_ranges(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            exe = Path(tmp) / "rcom.exe"
            exe.touch()
            settings = Settings(rcom_exe=str(exe), tolerance_mils=0.2)
            save_settings(settings, path)
            self.assertEqual(load_settings(path), settings)
            for key, value in [("port", 0), ("map_resolution_mils", 9), ("image_size", 2001), ("tolerance_mils", float("nan")), ("margin_inch", -1), ("database", "db,job=x")]:
                with self.subTest(key=key):
                    with self.assertRaises(ValueError):
                        Settings(**{key: value}).validate(require_executable=False)


if __name__ == "__main__":
    unittest.main()
