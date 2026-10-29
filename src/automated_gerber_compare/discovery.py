"""Bounded installation discovery. Never search whole drives or combine versions."""
from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping

from .parsers import decode_output
from .settings import Settings


def read_environment_file(path: Path, inherited: Mapping[str, str]) -> dict[str, str]:
    """Read installer NAME=value data without executing scripts or logging values."""
    values = dict(inherited)
    if not path.is_file():
        return values
    with path.open("rb") as stream:
        data = stream.read(1024 * 1024)
    raw = {}
    for line in decode_output(data).splitlines():
        if match := re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line):
            raw[match[1].upper()] = match[2].strip('"')
    # Resolve forward references; PATH's %PATH% expands against the inherited PATH.
    for _ in range(12):
        changed = False
        for key, original in raw.items():
            def expand(match):
                name = match[1].upper()
                return inherited.get(name, match[0]) if name == key else values.get(name, match[0])
            expanded = re.sub(r"%([A-Za-z_][A-Za-z0-9_]*)%", expand, original)
            if values.get(key) != expanded:
                values[key] = expanded
                changed = True
        if not changed:
            break
    return values


def edir_for(executable: Path) -> Path:
    for parent in executable.parents:
        if parent.name.casefold() == "edir":
            return parent
    return executable.parent.parent if executable.parent.name.casefold() in {"misc", "get", "bin"} else executable.parent


@dataclass(frozen=True)
class Installation:
    edir: Path
    get_exe: Path | None = None
    rcom_exe: Path | None = None
    source: str = "安装目录"

    @property
    def version(self) -> str:
        versions = re.findall(r"(?:vnpi[_ -]?|valor[_ -]?)(\d{4,})", str(self.edir), re.I)
        return versions[-1] if versions else "本机安装"

    @property
    def label(self) -> str:
        return f"Valor {self.version}  ·  {self.edir}"

    @property
    def complete(self) -> bool:
        return self.get_exe is not None and self.rcom_exe is not None


def _registry_roots() -> list[Path]:
    if os.name != "nt":
        return []
    import winreg
    roots = []
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", 0, winreg.KEY_READ | view) as key:
                    for index in range(winreg.QueryInfoKey(key)[0]):
                        try:
                            with winreg.OpenKey(key, winreg.EnumKey(key, index)) as entry:
                                display = str(winreg.QueryValueEx(entry, "DisplayName")[0])
                                if re.search(r"valor|vnpi", display, re.I):
                                    location = str(winreg.QueryValueEx(entry, "InstallLocation")[0])
                                    if location:
                                        roots.append(Path(location))
                        except OSError:
                            continue
            except OSError:
                continue
    return roots


def _common_roots(environ: Mapping[str, str]) -> list[Path]:
    if os.name != "nt":
        return []
    import ctypes
    roots = []
    kernel32 = ctypes.windll.kernel32
    kernel32.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
    kernel32.GetDriveTypeW.restype = ctypes.c_uint
    # Local fixed drives only. Network and removable disks are deliberately excluded.
    mask = kernel32.GetLogicalDrives()
    for index in range(26):
        drive = f"{chr(65 + index)}:/"
        if mask & (1 << index) and kernel32.GetDriveTypeW(drive) == 3:
            for folder in ("SiemensEDA", "MentorGraphics", "Valor", "Siemens"):
                roots.append(Path(drive) / folder)
    for key in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        if location := environ.get(key):
            roots.extend(Path(location) / folder for folder in ("SiemensEDA", "Siemens", "MentorGraphics", "Valor"))
    return roots


def discover_installations(
    settings: Settings | None = None,
    *, roots: list[Path] | None = None,
    environ: Mapping[str, str] | None = None,
) -> list[Installation]:
    settings = settings or Settings()
    env = dict(os.environ if environ is None else environ)
    env_path = settings.env_file or env.get("VALOR_ENV_FILE", "")
    if env_path:
        env = read_environment_file(Path(env_path), env)
    candidates: list[tuple[Path, str]] = []
    explicit: list[Path] = []
    for value in (settings.rcom_exe, settings.get_exe):
        if value and Path(value).is_file():
            path = Path(value).resolve()
            explicit.append(path)
            candidates.append((edir_for(path), "已有配置"))
    for key in ("VALOR_EDIR", "VALOR_DIR"):
        if value := env.get(key):
            if "%" not in value:
                candidates.append((Path(value), "Valor 环境"))
    for name in ("rcom.exe", "get.exe"):
        if found := shutil.which(name, path=env.get("PATH", "")):
            path = Path(found).resolve()
            explicit.append(path)
            candidates.append((edir_for(path), "PATH"))
    if roots is None:
        candidates.extend((p, "注册表") for p in _registry_roots())
        candidates.extend((p, "常见目录") for p in _common_roots(env))
    else:
        candidates.extend((Path(p), "指定目录") for p in roots)
    groups: dict[str, Installation] = {}

    def add(path: Path, source: str):
        if path.name.casefold() not in {"get.exe", "rcom.exe"} or not path.is_file():
            return
        path = path.resolve()
        edir = edir_for(path)
        key = str(edir).casefold()
        installation = groups.get(key, Installation(edir, source=source))
        field = "get_exe" if path.name.casefold() == "get.exe" else "rcom_exe"
        groups[key] = replace(installation, **{field: path})

    for path in explicit:
        add(path, "已有配置 / PATH")
    seen: set[str] = set()
    visited = 0
    # Inspect conventional companions first, even if a large vendor tree exhausts the budget.
    for root, source in candidates:
        for relative in ("get/get.exe", "misc/rcom.exe", "bin/get.exe", "bin/rcom.exe", "get.exe", "rcom.exe"):
            add(root / relative, source)
    for root, source in candidates:
        stack = [(root, 0)]
        root_visited = 0
        while stack and visited < 2200 and root_visited < 450:
            current, depth = stack.pop()
            identity = str(current).casefold()
            if identity in seen:
                continue
            seen.add(identity)
            visited += 1
            root_visited += 1
            try:
                with os.scandir(current) as entries:
                    children = []
                    for entry in entries:
                        if entry.is_file(follow_symlinks=False):
                            if entry.name.casefold() in {"get.exe", "rcom.exe"}:
                                add(Path(entry.path), source)
                        elif depth < 7 and entry.is_dir(follow_symlinks=False):
                            # Skip large data/dependency trees which cannot be installation roots.
                            if entry.name.casefold() not in {"node_modules", ".git", "jobs", "symbols", "docpkg", "all", "nv", "sys", "share"}:
                                children.append(Path(entry.path))
                    # Visit Valor branches first without letting another large EDA install
                    # consume the budget before subsequent local drives are examined.
                    children.sort(key=lambda p: (bool(re.search(r"valor|vnpi|^edir$|^get$|^misc$", p.name, re.I)), p.name))
                    stack.extend((path, depth + 1) for path in children)
            except OSError:
                continue
    priority = {"已有配置": 5, "已有配置 / PATH": 5, "Valor 环境": 4, "PATH": 3, "注册表": 2}
    return sorted(groups.values(), key=lambda i: (i.complete, priority.get(i.source, 0), int(i.version) if i.version.isdigit() else 0, str(i.edir)), reverse=True)


def apply_discovery(settings: Settings, installations: list[Installation]) -> Settings:
    """Keep valid manual paths; only derive a missing companion from the SAME install."""
    rcom_valid = bool(settings.rcom_exe and Path(settings.rcom_exe).is_file())
    get_valid = bool(settings.get_exe and Path(settings.get_exe).is_file())
    if rcom_valid and get_valid:
        return settings
    anchor = Path(settings.rcom_exe if rcom_valid else settings.get_exe) if rcom_valid or get_valid else None
    choices = installations
    if anchor:
        choices = [i for i in choices if str(i.edir).casefold() == str(edir_for(anchor.resolve())).casefold()]
    chosen = next((i for i in choices if i.complete), choices[0] if choices else None)
    if chosen is None:
        return settings
    return replace(settings,
                   rcom_exe=settings.rcom_exe if rcom_valid else str(chosen.rcom_exe or ""),
                   get_exe=settings.get_exe if get_valid else str(chosen.get_exe or ""))


def resolve_settings(settings: Settings) -> Settings:
    if settings.rcom_exe and Path(settings.rcom_exe).is_file():
        return settings
    return apply_discovery(settings, discover_installations(settings))


def process_environment(settings: Settings) -> dict[str, str]:
    env = dict(os.environ)
    env_file = settings.env_file or env.get("VALOR_ENV_FILE", "")
    if env_file and Path(env_file).is_file():
        env = read_environment_file(Path(env_file), env)
        env["VALOR_ENV_FILE"] = env_file
    executable = settings.get_exe or settings.rcom_exe
    if executable:
        edir = edir_for(Path(executable).resolve())
        # The vendor env file retains priority inside Valor. Never invent VALOR_DIR:
        # it is normally the shared vNPI_DIR, NOT the version's executable directory.
        env.setdefault("VALOR_EDIR", str(edir))
        paths = [edir / p for p in ("get", "misc", "nv/dlls", "nv/mgls/lib", "nv/bin")]
        prefix = [str(p) for p in paths if p.is_dir()]
        env["PATH"] = os.pathsep.join(prefix + [env.get("PATH", "")])
    return env
