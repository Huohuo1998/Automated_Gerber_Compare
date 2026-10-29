"""Strict parsing: a missing/ambiguous answer must never become a zero-difference result."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass


def nonnegative_integer(text: str, context: str) -> int:
    answer = text.strip()
    match = re.fullmatch(r"(?:COMANS\s*[=:]\s*)?(\d+)", answer, re.I)
    if not match:
        raise ValueError(f"{context} 未返回唯一的非负整数，原始输出：{answer!r}")
    return int(match[1])


@dataclass(frozen=True)
class Bounds:
    xmin: float
    ymin: float
    xmax: float
    ymax: float

    def union_with_margin(self, other: Bounds, margin: float) -> Bounds:
        return Bounds(min(self.xmin, other.xmin) - margin, min(self.ymin, other.ymin) - margin,
                      max(self.xmax, other.xmax) + margin, max(self.ymax, other.ymax) + margin)


def parse_bounds(text: str) -> Bounds:
    values = {}
    for key in ("xmin", "ymin", "xmax", "ymax"):
        matches = re.findall(r"\b" + key + r"\s*=\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)", text, re.I)
        if len(matches) != 1:
            raise ValueError(f"LIMITS 中 {key} 缺失或重复：{text!r}")
        values[key] = float(matches[0])
    bounds = Bounds(**values)
    if not all(math.isfinite(v) for v in values.values()) or bounds.xmax < bounds.xmin or bounds.ymax < bounds.ymin:
        raise ValueError(f"无效层边界（可能为空层或导入失败）：{text!r}")
    return bounds


def check_import_report(text: str, expected_layers: list[str]) -> None:
    def array(name: str) -> list[str]:
        matches = re.findall(r"\bset\s+" + name + r"\s*=\s*\((.*?)\)", text, re.S)
        if len(matches) != 1:
            raise ValueError(f"导入报告中 {name} 缺失或重复")
        return matches[0].split()

    statuses, layers = array("giTSTATUS"), array("giTLAYER")
    expected = [s.casefold() for s in expected_layers]
    if len(statuses) != len(expected) or len(layers) != len(expected):
        raise ValueError(f"导入报告的文件/状态数量不匹配：应为 {len(expected)}，层数 {len(layers)}，状态数 {len(statuses)}")
    if sorted(s.casefold() for s in layers) != sorted(expected):
        raise ValueError(f"导入报告的目标层与计划不一致：{layers}")
    failed = [f"{layer}: {status}" for layer, status in zip(layers, statuses) if status.casefold() != "ok"]
    if failed:
        raise ValueError("存在导入失败的文件：" + "; ".join(failed))


def decode_output(data: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeError:
            pass
    return data.decode("utf-8", errors="replace")
