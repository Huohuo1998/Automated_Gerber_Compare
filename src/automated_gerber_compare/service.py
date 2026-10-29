"""Orchestrate validated inputs -> all comparisons -> all cleanup -> all captures."""

from __future__ import annotations

import threading
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Callable

from .commands import (bounds_command, build_plan, clean_commands, command, compare_command,
                       display_commands, setup_commands)
from .models import LayerResult, PreparedComparison, RunResult
from .parsers import check_import_report, decode_output, nonnegative_integer, parse_bounds
from .rcom import Cancelled, EndpointLock, RcomClient, RcomError
from .report import generate_report, image_mime
from .settings import Settings
from .validation import verify_snapshot, write_json

Progress = Callable[[int, int, str], None]


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _checked_plan(prepared: PreparedComparison, settings: Settings, job: str | None = None) -> dict:
    settings.validate(require_executable=False)
    verify_snapshot(prepared)
    job = job or "gc_" + datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]
    return build_plan(prepared, settings, job)


def export_plan(prepared: PreparedComparison, settings: Settings, job: str | None = None) -> dict:
    if (prepared.directory / "run.started").exists():
        raise FileExistsError("本工作快照已执行过，请重新校验以创建新任务")
    plan = _checked_plan(prepared, settings, job)
    write_json(prepared.directory / "command_plan.json", plan)
    return plan


def run_comparison(
    prepared: PreparedComparison,
    settings: Settings,
    cancel: threading.Event | None = None,
    progress: Progress | None = None,
) -> RunResult:
    settings.validate()
    # All static paths/parameters and input snapshots are checked BEFORE touching Valor.
    plan = _checked_plan(prepared, settings)
    directory = prepared.directory
    with (directory / "run.started").open("x", encoding="utf-8") as marker:
        marker.write(now())
    write_json(directory / "command_plan.json", plan)
    (directory / "images").mkdir()
    cancel = cancel or threading.Event()
    notify = progress or (lambda *_: None)
    client = RcomClient(settings, directory, cancel)
    job = plan["job"]
    pairs = prepared.validation.pairs
    result = RunResult(job, directory, now(), asdict(settings), [
        LayerResult(p.key, p.label, p.a.relative_path, p.b.relative_path, p.layer_a, p.layer_b, p.layer_result)
        for p in pairs
    ])
    job_created = editor_open = uncertain = False
    group: int | None = None
    popview: int | None = None
    active: LayerResult | None = None
    total = len(pairs) * 3 + 2
    completed = 0

    def update(message: str) -> None:
        nonlocal completed
        notify(completed, total, message)

    try:
        with EndpointLock(settings.port):
            try:
                update("测试 Valor RCOM 连接")
                client.execute("TST", timeout=min(settings.command_timeout, 15))
                for lmc in setup_commands(prepared, settings, job):
                    answer = client.execute(lmc)
                    if lmc.startswith("COM create_entity,job=,"):
                        job_created = True
                    if lmc.startswith("COM input_manual,"):
                        report_file = directory / "import.log"
                        if not report_file.is_file():
                            raise ValueError("Valor 没有生成导入报告，停止比较")
                        check_import_report(decode_output(report_file.read_bytes()), [l for p in pairs for l in (p.layer_a, p.layer_b)])
                    if lmc.startswith("COM open_entity,"):
                        # Even editor group 0 is a valid answer in the supplied run record.
                        editor_open = True
                        group = nonnegative_integer(answer, "open_entity")
                        client.execute(f"AUX set_group,group={group}")
                client.execute(command("units", type="inch"))
                completed += 1
                for pair, row in zip(pairs, result.layers):
                    active = row
                    update(f"图形比较：{pair.label} / {pair.a.relative_path}")
                    row.difference_boxes = nonnegative_integer(client.execute(compare_command(pair, settings)), "compare_layers")
                    row.status = "pending_image"
                    completed += 1
                client.execute(command("colors_set", mode="user"))
                for pair, row in zip(pairs, result.layers):
                    active = row
                    update(f"清理结果网格：{pair.label}")
                    commands = clean_commands(pair, settings)
                    selected = 0
                    for lmc in commands:
                        if lmc == "COM sel_delete" and selected == 0:
                            continue
                        answer = client.execute(lmc)
                        if lmc.startswith("COM sel_multi_feat,operation=select,"):
                            selected = nonnegative_integer(answer, "sel_multi_feat")
                    completed += 1
                for index, (pair, row) in enumerate(zip(pairs, result.layers), 1):
                    active = row
                    update(f"保存叠加截图：{pair.label}")
                    for lmc in display_commands(pair):
                        client.execute(lmc)
                    bounds = []
                    for side in ("A", "B"):
                        path = directory / f"bounds_{index:03d}_{side}.txt"
                        client.execute(bounds_command(pair, side, job, path))
                        if not path.is_file():
                            raise ValueError(f"Valor 没有生成 {side} 层的 LIMITS 文件")
                        bounds.append(parse_bounds(decode_output(path.read_bytes())))
                    viewport = bounds[0].union_with_margin(bounds[1], settings.margin_inch)
                    answer = client.execute(command("zoom_pv_open", x1=f"{viewport.xmin:.8f}", y1=f"{viewport.ymin:.8f}",
                                                    x2=f"{viewport.xmax:.8f}", y2=f"{viewport.ymax:.8f}",
                                                    w_win=settings.image_size, h_win=settings.image_size))
                    popview = nonnegative_integer(answer, "zoom_pv_open")
                    if popview == 0:
                        popview = None
                        raise ValueError("zoom_pv_open 未返回有效的正整数窗口 ID")
                    image = directory / "images" / f"{index:03d}.jpg"
                    client.execute(command("zoom_pv_print", popview=popview, fname=image))
                    if not image.is_file() or image.stat().st_size < 16:
                        raise ValueError("Valor 截图输出不存在或为空")
                    image_mime(image)
                    client.execute(command("zoom_pv_close", all=False, popview=popview))
                    popview = None
                    row.image = image.relative_to(directory).as_posix()
                    row.status = "consistent" if row.difference_boxes == 0 else "different"
                    completed += 1
                active = None
                client.execute(command("save_job", job=job, override=False))
                result.status = "different" if any(row.status == "different" for row in result.layers) else "consistent"
                completed += 1
            except Exception as exc:
                uncertain = isinstance(exc, RcomError) and exc.uncertain
                result.status = "cancelled" if isinstance(exc, Cancelled) else "failed"
                result.error = str(exc)
                if active:
                    active.status = "error"
                    active.error = str(exc)
                if uncertain:
                    result.warnings.append("RCOM 客户端已停止，但 Valor 端命令可能仍在执行；未继续发送清理命令。请在 Valor 中检查本次 Job 和窗口后再运行新任务。")
            finally:
                if not uncertain and job_created:
                    cleanup = []
                    if group is not None:
                        cleanup.append(f"AUX set_group,group={group}")
                    if popview is not None:
                        cleanup.append(command("zoom_pv_close", all=False, popview=popview))
                    if result.status not in ("consistent", "different"):
                        cleanup.append(command("save_job", job=job, override=False))
                    if editor_open and group is not None:
                        cleanup.append(command("editor_page_close"))
                    elif editor_open:
                        result.warnings.append("未解析到编辑器组号，未关闭编辑器，避免误关闭其他会话。")
                    cleanup.append(command("close_job", job=job))
                    for lmc in cleanup:
                        try:
                            client.execute(lmc, ignore_cancel=True, timeout=min(settings.command_timeout, 15))
                        except Exception as exc:
                            result.warnings.append("清理未完成：" + str(exc))
                            if isinstance(exc, RcomError) and exc.uncertain:
                                result.warnings.append("清理命令超时，已停止继续发送命令；请检查 Valor 会话。")
                                break
    except Exception as exc:
        result.status = "failed"
        result.error = str(exc)
    result.finished_at = now()
    update("生成 HTML 报告和证据压缩包")
    generate_report(prepared, result)
    notify(total, total, "任务结束：" + result.status)
    return result
