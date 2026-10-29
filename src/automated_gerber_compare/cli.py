"""Headless validation/planning and explicit opt-in execution for automation."""

from __future__ import annotations

import argparse
import json
import sys
import threading
from pathlib import Path

from .models import jsonable
from .service import export_plan, run_comparison
from .settings import Settings, default_output_directory, load_settings
from .validation import prepare_comparison


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Valor NPI 自动 Gerber 对比；无子命令时启动桌面界面")
    sub = p.add_subparsers(dest="action")
    sub.add_parser("gui", help="启动桌面应用")
    for name, help_text in [("validate", "只校验两份 ZIP"), ("prepare", "校验并生成命令计划，不调用 Valor"), ("compare", "校验后调用本机 Valor 对比")]:
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("archive_a", type=Path)
        cmd.add_argument("archive_b", type=Path)
        cmd.add_argument("--output", type=Path, default=default_output_directory())
        if name != "validate":
            cmd.add_argument("--rcom", help="rcom.exe 路径")
            cmd.add_argument("--database", help="Valor 数据库名称")
            cmd.add_argument("--port", type=int)
            cmd.add_argument("--tolerance", type=float, help="图形比较容差，mil，0.2–10")
            cmd.add_argument("--grid", type=int, help="差异网格大小，mil，10–1000")
            cmd.add_argument("--timeout", type=float, help="单条命令超时，秒")
        if name == "compare":
            cmd.add_argument("--yes", action="store_true", help="确认已阅读校验信息，允许有警告时继续并调用 Valor")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.action in (None, "gui"):
        from .gui import main as gui_main
        gui_main()
        return 0
    try:
        prepared = prepare_comparison(args.archive_a, args.archive_b, args.output)
        result = prepared.validation
        summary = {"can_compare": result.can_compare, "pairs": len(result.pairs), "errors": len(result.errors),
                   "warnings": len(result.warnings), "directory": str(prepared.directory), "issues": jsonable(result.issues)}
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if not result.can_compare:
            return 2
        if args.action == "validate":
            return 0
        from dataclasses import asdict
        values = asdict(load_settings())
        for source, dest in [("rcom", "rcom_exe"), ("database", "database"), ("port", "port"),
                             ("tolerance", "tolerance_mils"), ("grid", "map_resolution_mils"), ("timeout", "command_timeout")]:
            value = getattr(args, source)
            if value is not None:
                values[dest] = value
        settings = Settings(**values)
        if args.action == "prepare":
            export_plan(prepared, settings)
            print("已生成计划（未调用 Valor）：" + str(prepared.directory / "command_plan.json"))
            return 0
        if not args.yes:
            print("尚未确认，不会调用 Valor。请先检查上述校验信息，确认后添加 --yes。", file=sys.stderr)
            return 2
        run = run_comparison(prepared, settings, threading.Event(),
                             lambda n, total, text: print(f"[{n}/{total}] {text}", file=sys.stderr))
        print("报告：" + str(prepared.directory / "report.html"))
        if run.error:
            print(run.error, file=sys.stderr)
        return {"consistent": 0, "different": 1, "failed": 3, "cancelled": 130}.get(run.status, 3)
    except (OSError, ValueError, TypeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 3
