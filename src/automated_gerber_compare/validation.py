"""Prepare immutable input snapshots and enforce a bijection before any Valor calls."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path, PurePosixPath

from .analyzer import analyze_file
from .archives import ArchiveError, extract_archive, file_hash
from .models import FileRecord, Issue, LayerPair, PreparedComparison, ValidationResult, jsonable


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(jsonable(value), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def match_layers(result: ValidationResult) -> None:
    sides: dict[str, dict[str, list[FileRecord]]] = {"A": defaultdict(list), "B": defaultdict(list)}
    for record in result.files:
        if record.kind != "ignored":
            sides[record.side][record.match_key].append(record)
    for side in ("A", "B"):
        if not sides[side]:
            result.issues.append(Issue("error", "no_layers", "未找到可比较的 Gerber 或钻孔文件", side))
    for key in sorted(sides["A"].keys() | sides["B"].keys()):
        a, b = sides["A"].get(key, []), sides["B"].get(key, [])
        if len(a) != 1 or len(b) != 1:
            for side, records in (("A", a), ("B", b)):
                if not records:
                    result.issues.append(Issue("error", "unmatched_layer", f"缺少对应层：{key}", side))
                elif len(records) > 1:
                    result.issues.append(Issue("error", "ambiguous_layer", f"同一层用途存在多个文件，无法唯一配对：{key}；" + "、".join(r.relative_path for r in records), side))
            continue
        basename = PurePosixPath(a[0].relative_path).name
        base = re.sub(r"[^a-z0-9_.+-]", "_", basename.lower()).strip("._") or "layer"
        # Hash semantic keys to avoid collisions after sanitizing/truncation.
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:10]
        base = f"{base[:38]}_{digest}"
        result.pairs.append(LayerPair(key, a[0].category, base, a[0], b[0]))
        if a[0].import_format and b[0].import_format and a[0].import_format != b[0].import_format:
            result.issues.append(Issue("warning", "different_formats", f"{basename} 的两侧导入格式不同，将分别使用各自文件头参数；保持原坐标，不自动平移对齐"))
    for side in ("A", "B"):
        ignored = sum(r.kind == "ignored" for r in result.files if r.side == side)
        if ignored:
            result.issues.append(Issue("info", "ignored_files", f"{ignored} 个非 Gerber 文件列入清单，不参与配对或图形比较", side))


def prepare_comparison(archive_a: Path, archive_b: Path, output_root: Path) -> PreparedComparison:
    archive_a, archive_b, output_root = (p.expanduser().resolve() for p in (archive_a, archive_b, output_root))
    output_root.mkdir(parents=True, exist_ok=True)
    directory = output_root / (datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8])
    directory.mkdir()
    result = ValidationResult(str(archive_a), str(archive_b))
    for side, archive in (("A", archive_a), ("B", archive_b)):
        try:
            if not archive.is_file():
                raise ArchiveError(f"找不到压缩包：{archive}")
            before_hash = file_hash(archive)
            records = extract_archive(archive, directory / f"input_{side}", side)
            if before_hash != file_hash(archive):
                raise ArchiveError("校验期间压缩包发生变化，请重新上传校验")
            result.archive_hashes[side] = before_hash
            for record in records:
                analyzed, issues = analyze_file(record)
                result.files.append(analyzed)
                result.issues.extend(issues)
        except (ArchiveError, OSError, ValueError) as exc:
            result.issues.append(Issue("error", "archive_error", str(exc), side))
    match_layers(result)
    if result.archive_hashes.get("A") and result.archive_hashes.get("A") == result.archive_hashes.get("B"):
        result.issues.append(Issue("warning", "same_archive", "两份 ZIP 的 SHA-256 相同，请确认未重复选择同一版本"))
    write_json(directory / "validation.json", result)
    return PreparedComparison(directory, result)


def verify_snapshot(prepared: PreparedComparison) -> None:
    if not prepared.validation.can_compare:
        raise ValueError("校验存在错误或没有可比较层，请重新上传并校验")
    for pair in prepared.validation.pairs:
        for record in (pair.a, pair.b):
            if not record.import_format:
                raise ValueError(f"缺少导入参数：{record.relative_path}")
            if not record.path.is_file() or file_hash(record.path) != record.sha256:
                raise ValueError(f"校验后的文件快照发生变化，请重新校验：{record.relative_path}")
