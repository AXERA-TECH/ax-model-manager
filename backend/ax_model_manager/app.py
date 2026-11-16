from __future__ import annotations

import json
import socket
import threading
import time
import urllib.request
from hmac import compare_digest
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import __version__
from .config import Settings, load_settings
from .jobs import Job, JobStore
from .process import FakeSubprocessManager, ProcessSpec
from .scanner import ModelRootError, scan_models, summary


class StartRequest(BaseModel):
    profile: str = "default"
    port: int | None = Field(default=None, ge=1, le=65535)
    force: bool = False
    debug_busy: bool = False


class StopRequest(BaseModel):
    mode: str = "block"
    timeout_seconds: int = Field(default=5, ge=1, le=3600)
    force: bool = False
    reason: str = "user_stop"


def create_app(settings: Settings | None = None) -> FastAPI:
    selected = settings or load_settings()
    app = FastAPI(
        title="AXERA Model Manager",
        version=__version__,
        description="Read-only model discovery MVP. Control endpoints are intentionally not enabled yet.",
    )
    app.state.settings = selected
    app.state.jobs = JobStore()
    app.state.process_manager = (
        FakeSubprocessManager() if selected.process_backend == "fake-subprocess" else None
    )

    if selected.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(selected.allowed_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type"],
        )

    async def require_auth(request: Request) -> None:
        token = request.app.state.settings.auth_token
        if not token:
            return
        authorization = request.headers.get("Authorization", "")
        expected = f"Bearer {token}".encode("utf-8")
        if not compare_digest(authorization.encode("utf-8"), expected):
            raise HTTPException(status_code=401, detail="unauthorized")

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "mode": "read-only" if app.state.settings.read_only else "control-enabled",
        }

    @app.get("/api/v1/config", dependencies=[Depends(require_auth)])
    async def config() -> dict[str, Any]:
        return app.state.settings.public_dict()

    @app.get("/api/v1/models", dependencies=[Depends(require_auth)])
    async def models() -> dict[str, Any]:
        try:
            records = scan_models(Path(app.state.settings.model_root))
        except ModelRootError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {
            "count": len(records),
            "valid_count": sum(1 for item in records if item["valid"]),
            "models": [summary(item) for item in records],
        }

    def find_model(model_id: str) -> dict[str, Any]:
        try:
            records = scan_models(Path(app.state.settings.model_root))
        except ModelRootError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        for record in records:
            if record["id"] == model_id:
                return record
        raise HTTPException(status_code=404, detail=f"model not found: {model_id}")

    @app.get("/api/v1/models/{model_id}", dependencies=[Depends(require_auth)])
    async def model_detail(model_id: str) -> dict[str, Any]:
        return find_model(model_id)

    @app.get("/api/v1/models/{model_id}/manifest", dependencies=[Depends(require_auth)])
    async def model_manifest(model_id: str) -> dict[str, Any]:
        record = find_model(model_id)
        return {
            "id": record["id"],
            "source": record["manifest_source"],
            "valid": record["manifest_valid"],
            "review_required": record["manifest_review_required"],
            "errors": record["errors"],
            "warnings": record["warnings"],
            "manifest": record["manifest"],
        }

    @app.get("/api/v1/models/{model_id}/validate", dependencies=[Depends(require_auth)])
    async def validate_model(model_id: str) -> dict[str, Any]:
        record = find_model(model_id)
        return {
            "id": record["id"],
            "valid": record["valid"],
            "ready_to_start": record["ready_to_start"],
            "errors": record["errors"],
            "warnings": record["warnings"],
            "artifacts": record["artifacts"],
        }

    def require_control(request: Request) -> None:
        if not request.app.state.settings.enable_control:
            raise HTTPException(status_code=404, detail="control endpoints are disabled")

    def audit(request: Request, action: str, **values: Any) -> None:
        path = request.app.state.settings.data_dir / "audit.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"at": time.time(), "action": action, **values}
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    def allocate_port(request: Request, preferred: int | None = None) -> int:
        settings = request.app.state.settings
        candidates = [preferred] if preferred else list(
            range(settings.llama_port_min, settings.llama_port_max + 1)
        )
        busy_ports = {job.port for job in request.app.state.jobs.active() if job.port}
        for port in candidates:
            if not settings.llama_port_min <= port <= settings.llama_port_max:
                continue
            if port in busy_ports:
                continue
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
        raise HTTPException(status_code=409, detail="no free llama-server port")

    def get_runtime_json(port: int, path: str, timeout: float = 0.5) -> Any:
        url = f"http://127.0.0.1:{port}{path}"
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def monitor_start(
        request: Request,
        job: Job,
        record: dict[str, Any],
        debug_busy: bool = False,
    ) -> None:
        jobs = request.app.state.jobs
        binary_report = next(item for item in record["artifacts"] if item["key"] == "binary")
        binary = Path(record["directory"]) / str(binary_report["path"])
        log_path = request.app.state.settings.data_dir / "logs" / f"{job.id}.log"
        spec = ProcessSpec(
            argv=(
                str(binary),
                "--port",
                str(job.port),
                "--ready-delay",
                "0.05",
                *(("--busy",) if debug_busy else ()),
            ),
            cwd=Path(record["directory"]),
            log_path=log_path,
        )
        try:
            pid = request.app.state.process_manager.start(spec)
            jobs.update(job, pid=pid, log_path=str(log_path), state="loading")
            jobs.event(job, "process_started", pid=pid)
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if not request.app.state.process_manager.is_alive(pid):
                    raise RuntimeError("llama-server exited before becoming ready")
                try:
                    health = get_runtime_json(job.port, "/health", timeout=0.2)
                    if health.get("status") == "ok":
                        jobs.update(
                            job,
                            state="running",
                            runtime_endpoint=f"http://127.0.0.1:{job.port}",
                        )
                        jobs.event(job, "runtime_ready", port=job.port)
                        return
                except Exception:
                    time.sleep(0.05)
            raise TimeoutError("runtime did not become ready within 5 seconds")
        except Exception as exc:
            jobs.update(job, state="failed", error=str(exc))
            jobs.event(job, "start_failed", error=str(exc))
            audit(request, "start_failed", model=record["id"], job=job.id, error=str(exc))

    @app.post(
        "/api/v1/models/{model_id}/start",
        dependencies=[Depends(require_auth), Depends(require_control)],
    )
    async def start_model(model_id: str, body: StartRequest, request: Request) -> dict[str, Any]:
        record = find_model(model_id)
        if not record["ready_to_start"]:
            raise HTTPException(
                status_code=400,
                detail={"message": "model is not ready", "errors": record["errors"]},
            )
        profiles = record["manifest"].get("profiles", {})
        if body.profile not in profiles:
            raise HTTPException(status_code=400, detail=f"profile not found: {body.profile}")
        if body.debug_busy and request.app.state.settings.process_backend != "fake-subprocess":
            raise HTTPException(status_code=400, detail="debug_busy is only available with fake-subprocess")

        existing = request.app.state.jobs.active_for_model(model_id)
        if existing:
            return {"job": existing.public(), "existing": True}
        active = request.app.state.jobs.active()
        if len(active) >= request.app.state.settings.max_running_models:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "model limit reached",
                    "active_jobs": [item.public() for item in active],
                },
            )

        port = allocate_port(request, body.port)
        job = request.app.state.jobs.create("start", model_id, "starting")
        request.app.state.jobs.update(job, port=port)
        audit(
            request,
            "start_requested",
            model=model_id,
            job=job.id,
            profile=body.profile,
            port=port,
        )
        threading.Thread(
            target=monitor_start,
            args=(request, job, record),
            kwargs={"debug_busy": body.debug_busy},
            daemon=True,
        ).start()
        return {"job": job.public(), "existing": False}

    @app.get("/api/v1/jobs", dependencies=[Depends(require_auth)])
    async def list_jobs(request: Request) -> dict[str, Any]:
        return {"jobs": [job.public() for job in request.app.state.jobs.list()]}

    @app.get("/api/v1/jobs/{job_id}", dependencies=[Depends(require_auth)])
    async def get_job(job_id: str, request: Request) -> dict[str, Any]:
        job = request.app.state.jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"job not found: {job_id}")
        return job.public()

    @app.post(
        "/api/v1/models/{model_id}/stop",
        dependencies=[Depends(require_auth), Depends(require_control)],
    )
    async def stop_model(model_id: str, body: StopRequest, request: Request) -> dict[str, Any]:
        candidates = [
            job for job in request.app.state.jobs.active()
            if job.model_id == model_id and job.state in {"starting", "loading", "running"}
        ]
        if not candidates:
            raise HTTPException(status_code=409, detail=f"no running model: {model_id}")
        runtime = candidates[0]
        busy = False
        if runtime.state == "running" and runtime.port:
            try:
                slots = get_runtime_json(runtime.port, "/slots")
                busy = any(
                    slot.get("is_processing")
                    for slot in slots
                    if isinstance(slot, dict)
                )
            except Exception:
                busy = True
        if busy and not body.force:
            raise HTTPException(
                status_code=409,
                detail={"error": "model_in_use", "message": "slot is processing"},
            )

        stop_job = request.app.state.jobs.create("stop", model_id, "stopping")
        audit(
            request,
            "stop_requested",
            model=model_id,
            job=stop_job.id,
            runtime_job=runtime.id,
            force=body.force,
        )

        def stop_worker() -> None:
            request.app.state.jobs.update(runtime, state="stopping")
            exit_code = request.app.state.process_manager.stop(
                runtime.pid,
                timeout=float(body.timeout_seconds),
            )
            request.app.state.jobs.update(runtime, state="stopped", exit_code=exit_code)
            request.app.state.jobs.update(stop_job, state="stopped", exit_code=exit_code)
            audit(
                request,
                "stop_completed",
                model=model_id,
                job=stop_job.id,
                exit_code=exit_code,
            )

        threading.Thread(target=stop_worker, daemon=True).start()
        return {"job": stop_job.public(), "runtime_job": runtime.public()}

    return app


app = create_app()
