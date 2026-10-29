"""Preset LMC templates. A complete command is one argv element, never a shell string."""

from __future__ import annotations

import re
from pathlib import Path

from .models import FileRecord, LayerPair, PreparedComparison
from .settings import Settings


def command(name: str, /, **params: object) -> str:
    if not re.fullmatch(r"[a-z_]+", name):
        raise ValueError(f"非法 LMC 名称：{name}")
    values = []
    for key, value in params.items():
        if isinstance(value, Path):
            text = value.resolve().as_posix()
        elif isinstance(value, bool):
            text = "yes" if value else "no"
        else:
            text = str(value)
        if any(c in text for c in (",", "\r", "\n", "\x00")):
            raise ValueError(f"Valor 行命令参数 {key} 包含逗号或控制符，请更换输出目录或参数")
        values.append(f"{key}={text}")
    return "COM " + name + (("," + ",".join(values)) if values else "")


def import_command(record: FileRecord, layer: str, job: str) -> str:
    fmt = record.import_format
    if fmt is None:
        raise ValueError(f"缺少导入参数：{record.relative_path}")
    return command("input_manual_set", path=record.path, job=job, step="1", format=fmt.format,
                   data_type="ascii", units=fmt.units, coordinates=fmt.coordinates, zeroes=fmt.zeroes,
                   nf1=fmt.nf1, nf2=fmt.nf2, decimal=fmt.decimal, separator=fmt.separator,
                   tool_units=fmt.units, layer=layer, wheel="", wheel_template="", nf_comp=0,
                   multiplier=1, signed_coords=False, read_ipc356_comps=False)


def setup_commands(prepared: PreparedComparison, settings: Settings, job: str) -> list[str]:
    commands = [
        command("create_entity", job="", is_fw=False, type="job", name=job, db=settings.database, fw_type="form"),
        command("create_entity", job=job, is_fw=False, type="step", name="1", db=settings.database, fw_type="form"),
        command("input_manual_reset"),
    ]
    for pair in prepared.validation.pairs:
        commands.extend([import_command(pair.a, pair.layer_a, job), import_command(pair.b, pair.layer_b, job)])
    commands.extend([
        command("input_manual", script_path=prepared.directory / "import.log"),
        command("open_entity", job=job, type="step", name="1", iconic=False),
    ])
    return commands


def compare_command(pair: LayerPair, settings: Settings) -> str:
    return command("compare_layers", layer1=pair.layer_a, layer2=pair.layer_b,
                   tol=settings.tolerance_mils, map_layer=pair.layer_result, map_layer_res=settings.map_resolution_mils)


def clean_commands(pair: LayerPair, settings: Settings) -> list[str]:
    # Only Result is affected when deleting grid lines. Source A/B remain untouched.
    return [
        command("clear_layers"),
        command("affected_layer", mode="all", affected=False),
        command("display_layer", name=pair.layer_result, display=True, number=3),
        command("work_layer", name=pair.layer_result),
        command("affected_layer", name=pair.layer_result, mode="single", affected=True),
        command("sel_multi_feat", operation="unselect", feat_types="line;pad;surface;arc;text", resize_by=0, include_syms="*"),
        command("sel_multi_feat", operation="select", feat_types="line", resize_by=0, include_syms=f"s{settings.map_resolution_mils}"),
        command("sel_delete"),
    ]


def display_commands(pair: LayerPair) -> list[str]:
    return [command("clear_layers"), command("affected_layer", mode="all", affected=False),
            command("display_layer", name=pair.layer_result, display=True, number=3),
            command("display_layer", name=pair.layer_a, display=True, number=1),
            command("display_layer", name=pair.layer_b, display=True, number=2)]


def bounds_command(pair: LayerPair, side: str, job: str, output: Path) -> str:
    layer = pair.layer_a if side == "A" else pair.layer_b
    return command("info", args=f"-t layer -e {job}/1/{layer} -m display -d LIMITS -p xmax+xmin+ymax+ymin -u no",
                   out_file=output, write_mode="replace", units="inch")


def build_plan(prepared: PreparedComparison, settings: Settings, job: str) -> dict:
    """Static commands and explicitly marked runtime-dependent commands for review."""
    pairs = prepared.validation.pairs
    return {
        "version": 1, "job": job, "step": "1", "executable": settings.rcom_exe, "port": settings.port,
        "note": "${...} 来自实际命令返回/文件；本计划不能当作完整静态脚本直接执行。commands.txt 记录实际执行命令。",
        "runtime_values": {
            "viewport": "x1/y1 为两层 xmin/ymin 的较小值减去 margin；x2/y2 为 xmax/ymax 的较大值加上 margin；单位 inch",
            "sel_delete": "仅当紧邻的 sel_multi_feat 返回选中数量大于 0 时执行",
        },
        "stages": [
            {"name": "preflight", "commands": ["TST"]},
            {"name": "create_and_import", "commands": setup_commands(prepared, settings, job)},
            {"name": "select_editor", "commands": ["AUX set_group,group=${open_entity}", command("units", type="inch")]},
            {"name": "compare_all", "commands": [compare_command(p, settings) for p in pairs]},
            {"name": "clean_all_results", "commands": [command("colors_set", mode="user")] + [c for p in pairs for c in clean_commands(p, settings)]},
            {"name": "capture_all", "layers": [
                {"key": p.key, "commands": display_commands(p) + [
                    bounds_command(p, "A", job, prepared.directory / f"bounds_{i:03d}_A.txt"),
                    bounds_command(p, "B", job, prepared.directory / f"bounds_{i:03d}_B.txt"),
                    command("zoom_pv_open", x1="${viewport.x1}", y1="${viewport.y1}",
                            x2="${viewport.x2}", y2="${viewport.y2}", w_win=settings.image_size, h_win=settings.image_size),
                    command("zoom_pv_print", popview="${zoom_pv_open}", fname=prepared.directory / "images" / f"{i:03d}.jpg"),
                    command("zoom_pv_close", all=False, popview="${zoom_pv_open}"),
                ]} for i, p in enumerate(pairs, 1)]},
            {"name": "save_and_close", "commands": [command("save_job", job=job, override=False),
                                                        command("editor_page_close"), command("close_job", job=job)]},
        ],
    }
