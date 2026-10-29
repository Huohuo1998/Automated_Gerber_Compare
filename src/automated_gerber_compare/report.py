"""Portable HTML with embedded images plus machine-readable evidence and a ZIP."""

from __future__ import annotations

import base64
import html
import zipfile
from pathlib import Path

from . import __version__
from .models import PreparedComparison, RunResult
from .validation import write_json

STATUS_LABELS = {
    "consistent": "一致", "different": "存在差异", "failed": "执行失败", "cancelled": "已取消",
    "not_run": "未执行", "pending_image": "已对比，截图未完成", "error": "处理失败", "running": "执行中",
}


def image_mime(path: Path) -> str:
    with path.open("rb") as stream:
        head = stream.read(16)
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"BM"):
        return "image/bmp"
    raise ValueError(f"Valor 未生成有效的 JPEG/PNG/BMP 截图：{path}")


def generate_report(prepared: PreparedComparison, result: RunResult) -> Path:
    directory = prepared.directory
    write_json(directory / "result.json", result)
    esc = lambda value: html.escape(str(value), quote=True)
    rows = []
    sections = []
    for i, layer in enumerate(result.layers, 1):
        label = STATUS_LABELS.get(layer.status, layer.status)
        boxes = "—" if layer.difference_boxes is None else str(layer.difference_boxes)
        rows.append(f'<tr><td><a href="#layer-{i}">{i:02d}</a></td><td>{esc(layer.label)}</td>'
                    f'<td>{esc(layer.source_a)}</td><td>{esc(layer.source_b)}</td><td>{boxes}</td>'
                    f'<td class="{esc(layer.status)}">{esc(label)}</td></tr>')
        visual = '<p class="muted">此层尚无完整截图。</p>'
        if layer.image:
            path = directory / layer.image
            mime = image_mime(path)
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            visual = f'<img loading="lazy" src="data:{mime};base64,{encoded}" alt="第 {i} 层的 A/B/Result 叠加图">'
        error = f'<pre class="error">{esc(layer.error)}</pre>' if layer.error else ""
        sections.append(f'<section id="layer-{i}"><h2>{i:02d} · {esc(layer.label)} <span class="{esc(layer.status)}">{esc(label)}</span></h2>'
                        f'<p>A: {esc(layer.source_a)}<br>B: {esc(layer.source_b)}<br>差异网格数：{boxes}</p>{error}{visual}</section>')
    issues = "".join(f'<li>[{esc(i.severity)}] {esc(i.side)} {esc(i.file)} {esc(i.message)}</li>' for i in prepared.validation.issues)
    warnings = "".join(f"<li>{esc(w)}</li>" for w in result.warnings)
    error = f'<pre class="error">{esc(result.error)}</pre>' if result.error else ""
    consistent = sum(l.status == "consistent" for l in result.layers)
    different = sum(l.status == "different" for l in result.layers)
    incomplete = len(result.layers) - consistent - different
    settings_rows = "".join(f"<tr><td>{esc(k)}</td><td>{esc(v)}</td></tr>" for k, v in result.settings.items())
    archive_rows = "".join(f"<p>{side} 包 SHA-256：<code>{esc(value)}</code></p>" for side, value in prepared.validation.archive_hashes.items())
    content = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Gerber 对比报告 · {esc(result.job)}</title><style>
:root {{color-scheme:light}} * {{box-sizing:border-box}} body {{margin:0;background:#f2f5f8;color:#16283b;font:15px/1.7 "Microsoft YaHei",system-ui,sans-serif}}
main {{max-width:1200px;margin:40px auto;padding:0 24px}} header,section {{background:white;border:1px solid #dce4ed;border-radius:12px;padding:28px;margin-bottom:22px}}
.eyebrow {{font-size:12px;letter-spacing:2px;color:#536d89}} h1 {{margin:8px 0;font-size:30px}} h2 {{font-size:20px;margin:0 0 14px}} h2 span {{font-size:14px;margin-left:12px}}
.muted {{color:#60768d}} .consistent {{color:#127347}} .different {{color:#b35c00}} .failed,.error,.cancelled {{color:#b42318}}
.metrics {{display:flex;gap:18px;flex-wrap:wrap;margin:24px 0}} .metric {{background:#f5f8fb;padding:15px 25px;border-radius:8px}} .metric b {{font-size:28px;margin-right:10px}}
.table-scroll {{overflow:auto}} table {{border-collapse:collapse;width:100%;font-size:13px}} th,td {{border-bottom:1px solid #e3eaf0;text-align:left;padding:12px 9px;vertical-align:top;overflow-wrap:anywhere}}
th {{background:#f5f8fb}} td:first-child {{white-space:nowrap}} img {{display:block;max-width:100%;height:auto;margin:auto;border:1px solid #e3eaf0}} pre {{white-space:pre-wrap;overflow-wrap:anywhere;background:#fff4f3;padding:14px}} code {{overflow-wrap:anywhere}}
a {{color:#225ab4}} details {{margin:18px 0}} footer {{text-align:center;font-size:12px;color:#60768d;padding:20px}} @media print {{body {{background:white}} main {{margin:0;max-width:none}} section {{break-inside:avoid;border-radius:0}} .table-scroll {{overflow:visible}} details {{display:block}}}}
</style></head><body><main><header><div class="eyebrow">AUTOMATED GERBER COMPARE</div><h1>Gerber 图形对比报告</h1>
<p>总体结果：<strong class="{esc(result.status)}">{esc(STATUS_LABELS.get(result.status, result.status))}</strong> · Job <code>{esc(result.job)}</code> / Step 1</p>
<p class="muted">开始：{esc(result.started_at)}<br>结束：{esc(result.finished_at)}</p>
<div class="metrics"><div class="metric"><b>{len(result.layers)}</b>比较层</div><div class="metric consistent"><b>{consistent}</b>一致</div><div class="metric different"><b>{different}</b>存在差异</div><div class="metric"><b>{incomplete}</b>未完整完成</div></div>{error}
<p>A：{esc(prepared.validation.archive_a)}<br>B：{esc(prepared.validation.archive_b)}</p>
<p>判定依据：Valor NPI <code>compare_layers</code> 在 {esc(result.settings['tolerance_mils'])} mil 容差下返回的差异网格数；0 表示该容差下图形一致，正数表示存在差异。网格数不等于独立缺陷数。比较保持源文件坐标。</p>
<p class="muted">叠加视图使用 Valor User 颜色模式：显示槽 1 = A，2 = B，3 = Result；具体颜色取决于本机 Valor 的 User 配色。所有截图均嵌入本文件，可离线查看。</p></header>
<section><h2>逐层记录</h2><div class="table-scroll"><table><thead><tr><th>序号</th><th>分类</th><th>A 文件</th><th>B 文件</th><th>差异网格</th><th>判定</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></section>
<section><h2>校验与执行信息</h2><ul>{issues}{warnings}</ul><details><summary>运行参数与输入校验值</summary><table>{settings_rows}</table>{archive_rows}</details>
<p class="muted">证据文件：result.json、validation.json、command_plan.json、commands.txt、rcom.jsonl、import.log 及边界文件随报告包提供。失败或取消的任务不作整体一致判定。</p></section>
{''.join(sections)}<footer>Automated Gerber Compare {__version__}</footer></main></body></html>'''
    report = directory / "report.html"
    report.write_text(content, encoding="utf-8")
    with zipfile.ZipFile(directory / "report_bundle.zip", "w", compression=zipfile.ZIP_DEFLATED) as zf:
        files = [report, directory / "result.json", directory / "validation.json", directory / "command_plan.json",
                 directory / "commands.txt", directory / "rcom.jsonl", directory / "import.log",
                 directory / "get_startup.log", directory / "valor_session.json"]
        files.extend(sorted(directory.glob("bounds_*.txt")))
        files.extend(sorted((directory / "images").glob("*")))
        for path in files:
            if path.is_file():
                zf.write(path, path.relative_to(directory).as_posix())
    return report
