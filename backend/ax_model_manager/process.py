from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProcessSpec:
    argv: tuple[str, ...]
    cwd: Path
    log_path: Path
    env: dict[str, str] | None = None


class FakeSubprocessManager:
    """Development process backend. Production systemd support is intentionally absent."""

    def start(self, spec: ProcessSpec) -> int:
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
        return process.pid

    def stop(self, pid: int, timeout: float = 5.0) -> int | None:
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

    def is_alive(self, pid: int | None) -> bool:
        if pid is None:
            return False
        try:
            waited, _ = os.waitpid(pid, os.WNOHANG)
            return waited == 0
        except ChildProcessError:
            return False
