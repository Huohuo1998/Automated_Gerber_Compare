"""Bounded ZIP extraction into controlled filenames, preserving original inventory."""

from __future__ import annotations

import hashlib
import re
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .models import FileRecord


class ArchiveError(ValueError):
    pass


@dataclass(frozen=True)
class ArchiveLimits:
    max_entries: int = 10000
    max_file_bytes: int = 256 * 1024 * 1024
    max_total_bytes: int = 2 * 1024 * 1024 * 1024


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _member_name(info: zipfile.ZipInfo) -> str:
    name = info.filename
    if not info.flag_bits & 0x800:
        # Windows ZIPs frequently store GBK filenames without the UTF-8 flag.
        try:
            name = name.encode("cp437").decode("gb18030")
        except UnicodeError:
            pass
    name = name.replace("\\", "/")
    parts = PurePosixPath(name).parts
    if (
        not parts or name.startswith("/") or ".." in parts
        or re.match(r"^[A-Za-z]:", name)
        or any(ord(c) < 32 for c in name)
        or any(":" in part for part in parts)
        or any(part.endswith((".", " ")) for part in parts)
    ):
        raise ArchiveError(f"压缩包包含不安全的文件路径：{name!r}")
    mode = info.external_attr >> 16
    file_type = stat.S_IFMT(mode)
    if file_type not in (0, stat.S_IFREG, stat.S_IFDIR):
        raise ArchiveError(f"压缩包包含符号链接或特殊文件：{name}")
    if info.flag_bits & 1:
        raise ArchiveError(f"不支持加密 ZIP，请先解密：{name}")
    return "/".join(parts)


def extract_archive(
    archive: Path, destination: Path, side: str, limits: ArchiveLimits = ArchiveLimits()
) -> list[FileRecord]:
    if not archive.is_file() or not zipfile.is_zipfile(archive):
        raise ArchiveError(f"请选择有效的 ZIP 压缩包：{archive}")
    destination.mkdir(parents=True, exist_ok=False)
    records: list[FileRecord] = []
    try:
        with zipfile.ZipFile(archive) as zf:
            infos = zf.infolist()
            if len(infos) > limits.max_entries:
                raise ArchiveError(f"ZIP 条目数超过上限 {limits.max_entries}")
            seen: set[str] = set()
            entries: list[tuple[zipfile.ZipInfo, str]] = []
            total = 0
            for info in infos:
                name = _member_name(info)
                key = name.casefold()
                if key in seen:
                    raise ArchiveError(f"ZIP 内存在重复或大小写冲突的路径：{name}")
                seen.add(key)
                if info.is_dir() or info.filename.endswith("\\"):
                    continue
                total += info.file_size
                if info.file_size > limits.max_file_bytes or total > limits.max_total_bytes:
                    raise ArchiveError("ZIP 解压数据超过大小上限（单文件 256 MiB / 总计 2 GiB）")
                entries.append((info, name))
            actual_total = 0
            for index, (info, name) in enumerate(entries, 1):
                suffix = PurePosixPath(name).suffix.lower()
                if not re.fullmatch(r"\.[a-z0-9]{1,8}", suffix):
                    suffix = ".dat"
                # The original path is metadata, never a filesystem destination or LMC argument.
                target = destination / f"{index:05d}{suffix}"
                digest = hashlib.sha256()
                size = 0
                with zf.open(info) as src, target.open("xb") as dst:
                    while chunk := src.read(1024 * 1024):
                        size += len(chunk)
                        actual_total += len(chunk)
                        if size > limits.max_file_bytes or actual_total > limits.max_total_bytes:
                            raise ArchiveError("ZIP 实际解压大小超出上限")
                        dst.write(chunk)
                        digest.update(chunk)
                records.append(FileRecord(side, name, target, size, digest.hexdigest()))
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError) as exc:
        raise ArchiveError(f"无法完整读取 ZIP：{exc}") from exc
    return records
