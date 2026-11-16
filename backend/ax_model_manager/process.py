from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ProcessSpec:
    argv: tuple[str, ...]
    cwd: Path
    log_path: Path
    env: dict[str, str] | None = None
    runtime_id: str = ""


@dataclass(frozen=True)
class RuntimeHandle:
    runtime_id: str
    pid: int | None = None


class FakeSubprocessManager:
    """Development process backend. Production systemd support is intentionally absent."""

    def start(self, spec: ProcessSpec) -> RuntimeHandle:
        spec.log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = spec.log_path.open("a", encoding="utf-8")
        env = os.environ.copy()
        if spec.env:
            env.update(spec.env)
        process = subprocess.Popen(
            spec.argv,
            cwd=str(spec.cwd),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
        return RuntimeHandle(runtime_id=spec.runtime_id or f"p-{process.pid}", pid=process.pid)

    def stop(self, handle: RuntimeHandle, timeout: float = 5.0) -> int | None:
        pid = handle.pid
        if pid is None:
            return None
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            return None
        try:
            deadline = time.monotonic() + timeout
            waited, status = os.waitpid(pid, os.WNOHANG)
            while waited == 0:
                waited, status = os.waitpid(pid, os.WNOHANG)
                if waited != 0:
                    break
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.05)
            if waited == 0:
                os.killpg(pid, signal.SIGKILL)
                waited, status = os.waitpid(pid, 0)
            if os.WIFEXITED(status):
                return os.WEXITSTATUS(status)
            if os.WIFSIGNALED(status):
                return -os.WTERMSIG(status)
            return None
        except ChildProcessError:
            return None

    def is_alive(self, handle: RuntimeHandle) -> bool:
        if handle.pid is None:
            return False
        try:
            waited, _ = os.waitpid(handle.pid, os.WNOHANG)
            return waited == 0
        except ChildProcessError:
            return False

    def list_runtimes(self) -> list[dict[str, Any]]:
        """Fake backend intentionally has no discoverable process list."""

        return []


class SystemdRunManager:
    """Production candidate backend using transient per-runtime systemd units."""

    def _systemctl(
        self,
        *args: str,
        check: bool = True,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["systemctl", *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=check,
            timeout=timeout,
        )

    def start(self, spec: ProcessSpec) -> RuntimeHandle:
        if not spec.runtime_id:
            raise ValueError("systemd backend requires runtime_id")
        spec.log_path.parent.mkdir(parents=True, exist_ok=True)
        spec.log_path.touch(exist_ok=True)
        command = [
            "systemd-run",
            "--quiet",
            f"--unit={spec.runtime_id}",
            "--collect",
            "--property=Type=exec",
            "--property=Restart=no",
            f"--property=WorkingDirectory={spec.cwd}",
            f"--property=StandardOutput=append:{spec.log_path}",
            f"--property=StandardError=append:{spec.log_path}",
        ]
        for key, value in (spec.env or {}).items():
            command.append(f"--setenv={key}={value}")
        command.extend(spec.argv)
        subprocess.run(command, check=True)
        completed = self._systemctl("show", spec.runtime_id, "--property=MainPID", "--value")
        pid = int(completed.stdout.strip() or "0")
        return RuntimeHandle(runtime_id=spec.runtime_id, pid=pid or None)

    def stop(self, handle: RuntimeHandle, timeout: float = 30.0) -> int | None:
        try:
            self._systemctl("stop", handle.runtime_id, timeout=timeout)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass
        try:
            completed = self._systemctl(
                "show",
                handle.runtime_id,
                "--property=ExecMainStatus",
                "--value",
                check=False,
            )
            value = completed.stdout.strip()
            return int(value) if value else None
        except (OSError, ValueError):
            return None

    def is_alive(self, handle: RuntimeHandle) -> bool:
        completed = self._systemctl("is-active", handle.runtime_id, check=False)
        return completed.stdout.strip() in {"active", "activating", "reloading"}

    def list_runtimes(self) -> list[dict[str, Any]]:
        """List Manager-created transient runtime units and their properties."""

        units = self._systemctl(
            "list-units",
            "ax-model-*.service",
            "--no-legend",
            "--plain",
            check=False,
        ).stdout.splitlines()
        result: list[dict[str, Any]] = []
        for line in units:
            parts = line.split()
            if not parts or not parts[0].startswith("ax-model-"):
                continue
            unit = parts[0]
            runtime_id = unit.removesuffix(".service")
            show = self._systemctl(
                "show",
                unit,
                "--property=MainPID,ActiveState,SubState,ExecStart",
                check=False,
            ).stdout
            properties: dict[str, Any] = {}
            for prop in show.splitlines():
                if "=" in prop:
                    key, value = prop.split("=", 1)
                    properties[key] = value
            try:
                pid = int(properties.get("MainPID") or 0) or None
            except ValueError:
                pid = None
            result.append({
                "runtime_id": runtime_id,
                "unit": unit,
                "pid": pid,
                "active_state": properties.get("ActiveState"),
                "sub_state": properties.get("SubState"),
                "command": properties.get("ExecStart"),
            })
        return result
