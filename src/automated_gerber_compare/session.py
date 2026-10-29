"""Reuse an RCOM endpoint or launch one owned get process and wait for readiness."""
from __future__ import annotations

import os
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .discovery import apply_discovery, discover_installations, process_environment
from .rcom import Cancelled, RcomClient, RcomError
from .settings import Settings


def port_is_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


@dataclass(frozen=True)
class SessionInfo:
    reused: bool
    pid: int | None
    port: int
    message: str
    get_exe: str = ""
    startup_log: str = ""


class ValorSession:
    """Retain owned process handles. Never terminate user or successfully started sessions."""
    def __init__(self):
        self.processes: dict[int, subprocess.Popen] = {}
        self.launch_paths: dict[int, tuple[str, str]] = {}

    def _info(self, reused: bool, settings: Settings, message: str) -> SessionInfo:
        process = self.processes.get(settings.port)
        executable, log = self.launch_paths.get(settings.port, ("", ""))
        return SessionInfo(reused, process.pid if process is not None else None, settings.port,
                           message, executable, log)

    def ensure_ready(
        self, settings: Settings, client: RcomClient, cancel: threading.Event,
        progress: Callable[[str], None] | None = None,
    ) -> SessionInfo:
        notify = progress or (lambda _: None)
        settings.validate()
        notify("正在连接 Valor…")
        try:
            client.execute("TST", timeout=min(settings.command_timeout, 5))
            return self._info(True, settings, "已连接现有 Valor 会话")
        except Cancelled:
            raise
        except RcomError as exc:
            if exc.uncertain or port_is_open(settings.port):
                raise RcomError(f"端口 {settings.port} 已占用或会话未正常响应，请检查对应 Valor 会话。\n{exc}", uncertain=exc.uncertain) from exc
            if not settings.auto_start_get:
                raise RcomError("Valor 尚未连接。请点击“启动 / 连接”，或在设置中启用自动启动。") from exc
        if cancel.is_set():
            raise Cancelled("已取消 Valor 启动等待")
        process = self.processes.get(settings.port)
        if process is not None and process.poll() is None:
            notify("正在等待已启动的 Valor 就绪…")
        else:
            if not settings.get_exe or not Path(settings.get_exe).is_file():
                # A configured rcom pins discovery to that installation; no cross-version pairing.
                settings = apply_discovery(settings, discover_installations(settings))
            if not settings.get_exe or not Path(settings.get_exe).is_file():
                raise RcomError("未找到与 rcom 对应的 get.exe。请在 Valor 设置中自动查找或手动选择。")
            executable = Path(settings.get_exe).resolve()
            args = [str(executable), f"-a{settings.port}"]
            notify("正在启动 Valor NPI…")
            log_path = client.directory / "get_startup.log"
            try:
                flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
                with log_path.open("ab") as stream:
                    stream.write(("\nStarting: " + subprocess.list2cmdline(args) + "\n").encode("utf-8"))
                    stream.flush()
                    process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=stream, stderr=stream,
                                               cwd=str(executable.parent), env=process_environment(settings),
                                               shell=False, creationflags=flags)
                self.processes[settings.port] = process
                self.launch_paths[settings.port] = (str(executable), str(log_path))
            except OSError as exc:
                raise RcomError(f"无法启动 get.exe：{exc}") from exc
        deadline = time.monotonic() + settings.startup_timeout
        last_error = ""
        while time.monotonic() < deadline:
            if cancel.is_set():
                raise Cancelled("已取消启动等待。Valor 进程将保留，可就绪后重新连接。")
            # A launcher can exit 0 while its GUI child is initializing; still test RCOM.
            code = process.poll()
            if code is not None and code != 0:
                raise RcomError(f"get.exe 启动退出，退出码 {code}。请查看 get_startup.log，并检查 Valor 环境和许可证。")
            try:
                remaining = deadline - time.monotonic()
                client.execute("TST", timeout=max(0.1, min(2, remaining)))
                return self._info(False, settings, "Valor 已启动并连接")
            except Cancelled:
                raise
            except RcomError as exc:
                last_error = str(exc)
            cancel.wait(min(0.5, max(0, deadline - time.monotonic())))
        raise RcomError(f"等待 Valor 启动超过 {settings.startup_timeout:g} 秒。进程将保留，请检查许可证或登录窗口后重新连接。\n{last_error}")
