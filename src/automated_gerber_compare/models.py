"""Shared models; UI, input analysis and Valor execution remain independent."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Issue:
    severity: str
    code: str
    message: str
    side: str = ""
    file: str = ""
    layer_key: str = ""


@dataclass(frozen=True)
class ImportFormat:
    format: str
    units: str
    coordinates: str
    zeroes: str
    nf1: int
    nf2: int
    decimal: bool = False
    separator: str = "*"


@dataclass(frozen=True)
class FileRecord:
    side: str
    relative_path: str
    path: Path
    size: int
    sha256: str
    kind: str = "ignored"
    category: str = "非 Gerber 文件"
    match_key: str = ""
    import_format: ImportFormat | None = None


@dataclass(frozen=True)
class LayerPair:
    key: str
    label: str
    layer_base: str
    a: FileRecord
    b: FileRecord

    @property
    def layer_a(self) -> str:
        return self.layer_base + ".A"

    @property
    def layer_b(self) -> str:
        return self.layer_base + ".B"

    @property
    def layer_result(self) -> str:
        return self.layer_base + ".Result"


@dataclass
class ValidationResult:
    archive_a: str
    archive_b: str
    archive_hashes: dict[str, str] = field(default_factory=dict)
    files: list[FileRecord] = field(default_factory=list)
    pairs: list[LayerPair] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def can_compare(self) -> bool:
        return bool(self.pairs) and not self.errors


@dataclass(frozen=True)
class PreparedComparison:
    directory: Path
    validation: ValidationResult


@dataclass
class LayerResult:
    key: str
    label: str
    source_a: str
    source_b: str
    layer_a: str
    layer_b: str
    layer_result: str
    status: str = "not_run"
    difference_boxes: int | None = None
    image: str | None = None
    error: str = ""


@dataclass
class RunResult:
    job: str
    directory: Path
    started_at: str
    settings: dict[str, Any]
    layers: list[LayerResult]
    status: str = "running"
    finished_at: str = ""
    error: str = ""
    warnings: list[str] = field(default_factory=list)


def jsonable(value: Any) -> Any:
    """Convert dataclasses including pathlib paths into an auditable JSON value."""
    if hasattr(value, "__dataclass_fields__"):
        return jsonable(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value
