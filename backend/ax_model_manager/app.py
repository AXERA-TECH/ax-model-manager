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
from .process import (
    FakeSubprocessManager,
    ProcessSpec,
    RuntimeHandle,
    SystemdRunManager,
)
from .scanner import ModelRootError, scan_models, summary


class StartRequest(BaseModel):
    profile: str = "default"
    port: int | None = Field(default=None, ge=1, le=65535)
    force: bool = False
    debug_busy: bool = False
    context: int | None = Field(default=None, ge=1024, le=100_000_000)
    batch: int | None = Field(default=None, ge=1, le=1_000_000)
    ubatch: int | None = Field(default=None, ge=1, le=1_000_000)
    cache_ram: int | None = Field(default=None, ge=0, le=1_000_000)
    ctxcp: int | None = Field(default=None, ge=0, le=128)
    mtp: bool | None = None


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
    app.state.jobs = JobStore(Path(selected.data_dir))
    managers = {
        "fake-subprocess": FakeSubprocessManager(),
        "systemd": SystemdRunManager(),
    }
    app.state.process_manager = managers.get(selected.process_backend)

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
            records = scan_models(
                Path(app.state.settings.model_root),
                Path(app.state.settings.data_dir) / "models",
            )
        except ModelRootError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {
            "count": len(records),
            "valid_count": sum(1 for item in records if item["valid"]),
            "models": [summary(item) for item in records],
        }

    def find_model(model_id: str) -> dict[str, Any]:
        try:
            records = scan_models(
                Path(app.state.settings.model_root),
                Path(app.state.settings.data_dir) / "models",
            )
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

    @app.post(
        "/api/v1/models/{model_id}/manifest/confirm",
        dependencies=[Depends(require_auth), Depends(require_control)],
    )
    async def confirm_manifest(model_id: str, request: Request) -> dict[str, Any]:
        record = find_model(model_id)
        if record["manifest_source"] == "user":
            return {"model": model_id, "source": "user", "changed": False}
        if not record["manifest_valid"]:
            raise HTTPException(
                status_code=400,
                detail={"message": "manifest is invalid", "errors": record["errors"]},
            )
        override_dir = Path(app.state.settings.data_dir) / "models"
        override_dir.mkdir(parents=True, exist_ok=True)
        override_path = override_dir / f"{record['relative_directory']}.json"
        override_path.write_text(
            json.dumps(record["manifest"], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        audit(request, "manifest_confirmed", model=model_id, path=str(override_path))
        updated = find_model(model_id)
        return {
            "model": model_id,
            "source": updated["manifest_source"],
            "changed": True,
            "ready_to_start": updated["ready_to_start"],
            "path": str(override_path),
        }

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

    def artifact_path(record: dict[str, Any], key: str) -> Path | None:
        report = next((item for item in record["artifacts"] if item["key"] == key), None)
        if not report or not report.get("path"):
            return None
        return Path(record["directory"]) / str(report["path"])

    def build_process_spec(
        request: Request,
        job: Job,
        record: dict[str, Any],
        profile_name: str,
        overrides: dict[str, Any],
        debug_busy: bool,
    ) -> ProcessSpec:
        binary = artifact_path(record, "binary")
        model = artifact_path(record, "model")
        mmproj = artifact_path(record, "mmproj")
        template = artifact_path(record, "chat_template")
        kernels = artifact_path(record, "kernels")
        if binary is None or model is None:
            raise RuntimeError("binary or model artifact is missing")

        profile = dict(record["manifest"].get("profiles", {}).get(profile_name, {}))
        profile.update({key: value for key, value in overrides.items() if value is not None})
        sampling = profile.get("sampling", {})
        argv: list[str] = [
            str(binary),
            "-m", str(model),
            "-ngl", "99",
            "-b", str(profile.get("batch", 4096)),
            "-ub", str(profile.get("ubatch", 4096)),
            "--image-max-tokens", str(profile.get("image_max_tokens", 768)),
            "-c", str(profile.get("context", 32768)),
            "-np", "1",
            "-ctxcp", str(profile.get("ctxcp", 0)),
            "--cache-ram", str(profile.get("cache_ram", 0)),
            "--jinja",
        ]
        if mmproj is not None:
            argv.extend(["--mmproj", str(mmproj)])
        if template is not None:
            argv.extend(["--chat-template-file", str(template)])
        argv.extend(["--reasoning", profile.get("reasoning", "off")])
        if profile.get("mtp", False):
            argv.extend([
                "--spec-type", "draft-mtp",
                "--spec-draft-n-max", "3",
                "--spec-draft-sampling", "greedy",
            ])
        else:
            argv.extend(["--spec-type", "none"])

        if "temperature" in sampling:
            argv.extend(["--temp", str(sampling["temperature"])])
        if "top_k" in sampling:
            argv.extend(["--top-k", str(sampling["top_k"])])
        if "top_p" in sampling:
            argv.extend(["--top-p", str(sampling["top_p"])])
        if "min_p" in sampling:
            argv.extend(["--min-p", str(sampling["min_p"])])
        if "repeat_last_n" in sampling:
            argv.extend(["--repeat-last-n", str(sampling["repeat_last_n"])])
        if "repeat_penalty" in sampling:
            argv.extend(["--repeat-penalty", str(sampling["repeat_penalty"])])
        if "presence_penalty" in sampling:
            argv.extend(["--presence-penalty", str(sampling["presence_penalty"])])
        if "frequency_penalty" in sampling:
            argv.extend(["--frequency-penalty", str(sampling["frequency_penalty"])])
        argv.extend(["--host", "0.0.0.0", "--port", str(job.port)])

        if request.app.state.settings.process_backend == "fake-subprocess":
            argv = [
                str(binary), "--port", str(job.port),
                "--ready-delay", "0.05", *(("--busy",) if debug_busy else ()),
            ]

        safe_model = "".join(
            character.lower() if character.isalnum() else "-"
            for character in record["id"]
        ).strip("-")
        backend = request.app.state.settings.process_backend
        runtime_id = f"ax-model-{safe_model}-{job.id}" if backend == "systemd" else job.id
        env = {
            "LD_LIBRARY_PATH": "/opt/lib",
            "LLAMA_SPEC_ADAPTIVE": "0",
        }
        if kernels is not None:
            env["GGML_AXERA_KERNEL_DIR"] = str(kernels)
        return ProcessSpec(
            argv=tuple(argv),
            cwd=Path(record["directory"]),
            log_path=request.app.state.settings.data_dir / "logs" / f"{job.id}.log",
            env=env,
            runtime_id=runtime_id,
        )

    def monitor_start(
        request: Request,
        job: Job,
        record: dict[str, Any],
        debug_busy: bool = False,
        profile_name: str = "default",
        overrides: dict[str, Any] | None = None,
    ) -> None:
        jobs = request.app.state.jobs
        try:
            spec = build_process_spec(
                request,
                job,
                record,
                profile_name,
                overrides or {},
                debug_busy,
            )
            handle = request.app.state.process_manager.start(spec)
            jobs.update(
                job,
                pid=handle.pid,
                runtime_id=handle.runtime_id,
                log_path=str(spec.log_path),
                state="loading",
            )
            jobs.event(job, "process_started", pid=handle.pid, runtime_id=handle.runtime_id)
            timeout = float((overrides or {}).get("startup_timeout_seconds", 0) or 600)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if not request.app.state.process_manager.is_alive(handle):
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
            kwargs={
                "debug_busy": body.debug_busy,
                "profile_name": body.profile,
                "overrides": body.model_dump(exclude={"profile", "port", "force", "debug_busy"}),
            },
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
                RuntimeHandle(runtime_id=runtime.runtime_id or "", pid=runtime.pid),
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
