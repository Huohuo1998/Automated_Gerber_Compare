"""Recognize Mentor/Xpedition layers and self-contained RS-274X / Excellon data.

The platform's gerber_analyzer.py informs naming conventions. Unlike a netlist
check, a graphic compare also includes mask, outline, drawing and other artwork.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import PurePosixPath

from .models import FileRecord, ImportFormat, Issue

GERBER_EXTENSIONS = {".gdo", ".gbr", ".ger", ".gerber", ".pho", ".art", ".gtl", ".gbl",
                     ".gts", ".gbs", ".gtp", ".gbp", ".gto", ".gbo", ".gko", ".gm1"}
DRILL_EXTENSIONS = {".ncd", ".drl", ".xln", ".exc"}
EXT_ROLES = {
    ".gtl": ("signal:top", "线路 / 顶层"), ".gbl": ("signal:bottom", "线路 / 底层"),
    ".gts": ("mask:top", "阻焊 / 顶层"), ".gbs": ("mask:bottom", "阻焊 / 底层"),
    ".gtp": ("paste:top", "钢网 / 顶层"), ".gbp": ("paste:bottom", "钢网 / 底层"),
    ".gto": ("silk:top", "丝印 / 顶层"), ".gbo": ("silk:bottom", "丝印 / 底层"),
    ".gko": ("outline", "板外形"),
}


def layer_role(name: str, kind: str) -> tuple[str, str, bool]:
    """Return a conservative semantic key; never resolve an ambiguous match by order."""
    lower = name.casefold()
    if kind == "drill":
        # NonPlated MUST precede Plated; the original platform regex overlapped.
        if re.fullmatch(r"thruhole.*nonplated\.ncd", lower):
            return "drill:through:npth", "钻孔 / 非电镀通孔", True
        if re.fullmatch(r"thruhole.*plated\.ncd", lower):
            return "drill:through:pth", "钻孔 / 电镀通孔", True
        if m := re.fullmatch(r"(\d+)-(\d+)(.*?)plated\.ncd", lower):
            return f"drill:{int(m[1])}-{int(m[2])}:{m[3]}", f"钻孔 / {m[1]}–{m[2]} 层 {m[3]}", True
        return f"drill:file:{lower}", "钻孔 / 按文件名匹配", False
    if lower == "gf_top_layer.gdo":
        return "signal:top", "线路 / 顶层", True
    if lower == "gf_bottom_layer.gdo":
        return "signal:bottom", "线路 / 底层", True
    if m := re.fullmatch(r"gf_inner_(\d+)\.gdo", lower):
        return f"signal:inner:{int(m[1])}", f"线路 / 内层 {int(m[1])}", True
    if lower == "gf_board_outline.gdo":
        return "outline", "板外形", True
    for pattern, key, label in [
        (r"gf_solder_mask_(top|bottom)\.gdo", "mask", "阻焊"),
        (r"gf_solder_paste_(top|bottom)\.gdo", "paste", "钢网"),
        (r"gf_(?:silkscreen|silk_screen)_(top|bottom)\.gdo", "silk", "丝印"),
    ]:
        if m := re.fullmatch(pattern, lower):
            return f"{key}:{m[1]}", f"{label} / {'顶层' if m[1] == 'top' else '底层'}", True
    if lower.startswith("gf_drill_drawing_") and lower.endswith(".gdo"):
        return f"drawing:{lower[17:-4]}", "钻孔图", True
    suffix = PurePosixPath(lower).suffix
    if suffix in EXT_ROLES:
        key, label = EXT_ROLES[suffix]
        return key, label, True
    if m := re.fullmatch(r".*\.g(\d+)", lower):
        return f"signal:internal_extension:{int(m[1])}", f"线路 / G{m[1]}", True
    return f"gerber:file:{lower}", "其他 Gerber / 按文件名匹配", False


def parse_gerber_format(text: str) -> ImportFormat:
    fs = re.search(r"%FS([LT])([AI])X([1-6])([1-6])Y([1-6])([1-6])\*%", text, re.I)
    mo = re.search(r"%MO(IN|MM)\*%", text, re.I)
    if not fs or not mo:
        raise ValueError("Gerber 缺少可解析的 %FS / %MO 文件头；请导出带光圈的 RS-274X 数据")
    if (fs[3], fs[4]) != (fs[5], fs[6]):
        raise ValueError("X/Y 坐标精度不一致，当前手动导入模板不支持")
    return ImportFormat("Gerber274x", "inch" if mo[1].upper() == "IN" else "mm",
                        "absolute" if fs[2].upper() == "A" else "incremental",
                        "leading" if fs[1].upper() == "L" else "trailing", int(fs[3]), int(fs[4]))


def parse_drill_format(text: str) -> ImportFormat:
    # Xpedition's explicit format comment is more specific than Excellon LZ/TZ dialects.
    mentor = re.search(r";\s*Format\s*:\s*([1-6])\.([1-6])\s*/\s*(Absolute|Incremental)"
                       r"\s*/\s*(Inch|mm|Metric)\s*/\s*(Leading|Trailing|None)", text, re.I)
    unit_match = re.search(r"^\s*(INCH|METRIC)(?:,([^\r\n]*))?", text, re.I | re.M)
    if mentor:
        units = "inch" if mentor[4].lower() == "inch" else "mm"
        if unit_match and units != ("inch" if unit_match[1].upper() == "INCH" else "mm"):
            raise ValueError("钻孔文件的单位声明与 Format 注释冲突")
        return ImportFormat("Excellon2", units, mentor[3].lower(), mentor[5].lower(),
                            int(mentor[1]), int(mentor[2]), separator="NL")
    if not unit_match:
        raise ValueError("钻孔文件缺少 INCH/METRIC 单位声明")
    units = "inch" if unit_match[1].upper() == "INCH" else "mm"
    coords = "incremental" if re.search(r"^\s*(G91|ICI,ON)\b", text, re.M | re.I) else "absolute"
    decimal = bool(re.search(r"^[XY][+-]?\d*\.\d+", text, re.M | re.I))
    if decimal:
        return ImportFormat("Excellon2", units, coords, "none", 2, 4, True, "NL")
    # Implicit precision and LZ/TZ meanings vary by exporter. Refuse to guess scale.
    raise ValueError("钻孔为隐式小数格式且缺少 Xpedition Format 注释；请导出显式小数或补充明确的格式声明")


def analyze_file(record: FileRecord) -> tuple[FileRecord, list[Issue]]:
    name = PurePosixPath(record.relative_path).name
    suffix = PurePosixPath(name).suffix.casefold()
    with record.path.open("rb") as stream:
        head = stream.read(256 * 1024)
    text = head.decode("ascii", errors="replace")
    is_drill = suffix in DRILL_EXTENSIONS or ("M48" in text and re.search(r"^\s*(INCH|METRIC)", text, re.M))
    is_gerber = suffix in GERBER_EXTENSIONS or bool(re.fullmatch(r"\.g\d+", suffix)) or (
        re.search(r"%FS[LT][AI]X", text) and re.search(r"%MO(?:IN|MM)\*%", text)
    )
    if not is_drill and not is_gerber:
        issues = []
        if suffix in {".zip", ".7z", ".rar"}:
            issues.append(Issue("warning", "nested_archive", "内嵌压缩包不自动解压；请将待比较文件直接放入上传 ZIP 的任意子目录", record.side, record.relative_path))
        return record, issues
    kind = "drill" if is_drill else "gerber"
    key, label, known = layer_role(name, kind)
    issues = []
    try:
        fmt = parse_drill_format(text) if is_drill else parse_gerber_format(text)
    except ValueError as exc:
        fmt = None
        issues.append(Issue("error", "unsupported_format", str(exc), record.side, record.relative_path))
    if not known:
        issues.append(Issue("warning", "filename_match", "层用途未识别，使用不区分大小写的完整文件名进行配对", record.side, record.relative_path))
    if record.size == 0:
        issues.append(Issue("error", "empty_file", "Gerber/钻孔文件为空", record.side, record.relative_path))
    return replace(record, kind=kind, category=label, match_key=key, import_format=fmt), issues
