"""Single-command RCOM transport and a per-endpoint, interprocess execution lock."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

from .parsers import decode_output
from .settings import Settings
from .discovery import process_environment


class RcomError(RuntimeError):
    def __init__(self, message: str, *, uncertain: bool = False):
        super().__init__(message)
        self.uncertain = uncertain


class Cancelled(RcomError):
    pass


class EndpointLock:
    """OS releases the lock on process exit; leave the lock inode in place."""

    def __init__(self, port: int):
        self.path = Path(tempfile.gettempdir()) / f"automated_gerber_compare_{port}.lock"
        self.stream = None

    def __enter__(self):
        stream = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            stream.close()
            raise RcomError("已有本程序的另一个任务占用此 RCOM 端口，请等待其结束") from exc
        self.stream = stream
        return self

    def __exit__(self, *_):
        if self.stream is not None:
            try:
                if os.name == "nt":
                    import msvcrt
                    self.stream.seek(0)
                    msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.stream, fcntl.LOCK_UN)
            finally:
                self.stream.close()


class RcomClient:
    def __init__(self, settings: Settings, directory: Path, cancel: threading.Event):
        self.settings = settings
        self.directory = directory
        self.cancel = cancel

    def execute(self, lmc: str, *, ignore_cancel: bool = False, timeout: float | None = None) -> str:
        if self.cancel.is_set() and not ignore_cancel:
            raise Cancelled("用户取消任务")
        args = [self.settings.rcom_exe, f"-a{self.settings.port}", lmc]
        limit = self.settings.command_timeout if timeout is None else timeout
        started = time.monotonic()
        stdout, stderr = b"", b""
        returncode = None
        problem = ""
        uncertain = False
        was_cancelled = False
        try:
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, shell=False, creationflags=flags,
                                       env=process_environment(self.settings))
            while True:
                was_cancelled = self.cancel.is_set() and not ignore_cancel
                remaining = limit - (time.monotonic() - started)
                if was_cancelled or remaining <= 0:
                    process.kill()
                    stdout, stderr = process.communicate()
                    returncode = process.returncode
                    uncertain = True
                    problem = "用户取消正在执行的命令" if was_cancelled else f"命令执行超过 {limit:g} 秒"
                    break
                try:
                    # Five seconds is the normal wait interval, not the overall deadline.
                    # Short readiness probes and the final interval use the time remaining.
                    stdout, stderr = process.communicate(timeout=min(5.0, remaining))
                    returncode = process.returncode
                    break
                except subprocess.TimeoutExpired:
                    continue
        except OSError as exc:
            problem = f"无法启动 rcom：{exc}"
        out, err = decode_output(stdout).strip(), decode_output(stderr).strip()
        if not problem and returncode != 0:
            problem = f"rcom 退出码 {returncode}：{err or out}"
        # Bare integer stdout is COMANS (e.g. 65 boxes), NOT a process status code.
        if not problem and re.search(r"(?im)^\s*(?:ERROR\b|FATAL\b|STATUS\s*[=:]\s*[1-9]\d*\b)", out + "\n" + err):
            problem = f"Valor 返回错误：{err or out}"
        record = {"timestamp": datetime.now().astimezone().isoformat(), "args": args, "command": lmc,
                  "returncode": returncode, "stdout": out, "stderr": err,
                  "elapsed_seconds": round(time.monotonic() - started, 3), "error": problem, "uncertain": uncertain}
        with (self.directory / "rcom.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps(record, ensure_ascii=False) + "\n")
        with (self.directory / "commands.txt").open("a", encoding="utf-8") as log:
            log.write(lmc + "\n")
        if problem:
            exception = Cancelled if was_cancelled else RcomError
            raise exception(problem + f"\n命令：{lmc}", uncertain=uncertain)
        return out
