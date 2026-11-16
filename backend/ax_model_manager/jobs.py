from __future__ import annotations

import threading
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


ACTIVE_STATES = {"starting", "loading", "running", "stopping"}


@dataclass
class Job:
    id: str
    action: str
    model_id: str
    state: str
    port: int | None = None
    pid: int | None = None
    runtime_id: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    error: str | None = None
    log_path: str | None = None
    runtime_endpoint: str | None = None
    exit_code: int | None = None
    events: list[dict[str, Any]] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        return {
            "job_id": self.id,
            "action": self.action,
            "model": self.model_id,
            "state": self.state,
            "port": self.port,
            "pid": self.pid,
            "runtime_id": self.runtime_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
            "log_path": self.log_path,
            "runtime_endpoint": self.runtime_endpoint,
            "exit_code": self.exit_code,
            "events": list(self.events),
        }


class JobStore:
    def __init__(self, state_dir: Path | None = None) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._state_dir = state_dir
        if state_dir is not None:
            self._load(state_dir)

    def _job_path(self, job_id: str) -> Path:
        if self._state_dir is None:
            raise RuntimeError("job persistence is disabled")
        return self._state_dir / "jobs" / f"{job_id}.json"

    def _persist_locked(self, job: Job) -> None:
        if self._state_dir is None:
            return
        path = self._job_path(job.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(asdict(job), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    def _load(self, state_dir: Path) -> None:
        directory = state_dir / "jobs"
        if not directory.is_dir():
            return
        for path in sorted(directory.glob("*.json")):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                job = Job(**value)
                self._jobs[job.id] = job
            except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
                continue

    def create(self, action: str, model_id: str, state: str) -> Job:
        job = Job(
            id=f"job_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}",
            action=action,
            model_id=model_id,
            state=state,
        )
        with self._lock:
            self._jobs[job.id] = job
            self._persist_locked(job)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda item: item.created_at, reverse=True)

    def active(self) -> list[Job]:
        return [job for job in self.list() if job.state in ACTIVE_STATES]

    def active_for_model(self, model_id: str) -> Job | None:
        for job in self.active():
            if job.model_id == model_id and job.state != "stopping":
                return job
        return None

    def update(self, job: Job, **values: Any) -> None:
        with self._lock:
            for key, value in values.items():
                setattr(job, key, value)
            job.updated_at = time.time()
            self._persist_locked(job)

    def event(self, job: Job, message: str, **values: Any) -> None:
        with self._lock:
            job.events.append({"at": time.time(), "message": message, **values})
            job.updated_at = time.time()
            self._persist_locked(job)
