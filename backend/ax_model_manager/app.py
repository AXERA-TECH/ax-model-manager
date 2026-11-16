from __future__ import annotations

import json
import os
import socket
import threading
import time
import urllib.request
from hmac import compare_digest
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import __version__
from .config import Settings, load_settings
from .jobs import Job, JobStore
from .manifest import is_safe_id, validate_manifest
from .process import (
    FakeSubprocessManager,
    ProcessSpec,
    RuntimeHandle,
    SystemdRunManager,
)
from .scanner import ModelRootError, scan_models, summary


class SamplingInput(BaseModel):
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_k: int | None = Field(default=None, ge=0, le=1_000_000)
    top_p: float | None = Field(default=None, ge=0, le=1)
    min_p: float | None = Field(default=None, ge=0, le=1)
    repeat_last_n: int | None = Field(default=None, ge=0, le=1_000_000)
    repeat_penalty: float | None = Field(default=None, ge=0, le=10)
    presence_penalty: float | None = Field(default=None, ge=-2, le=2)
    frequency_penalty: float | None = Field(default=None, ge=-2, le=2)


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
    sampling: SamplingInput | None = None


class ConfigUpdateRequest(BaseModel):
    max_running_models: int = Field(ge=1, le=10)


class StopRequest(BaseModel):
    mode: str = "block"
    timeout_seconds: int = Field(default=5, ge=1, le=3600)
    force: bool = False
    reason: str = "user_stop"


class SwitchRequest(StartRequest):
    stop_timeout_seconds: int = Field(default=15, ge=1, le=600)


class RestartRequest(StartRequest):
    stop_timeout_seconds: int = Field(default=15, ge=1, le=600)


class ProfileInput(BaseModel):
    context: int | None = Field(default=None, ge=1024, le=100_000_000)
    batch: int | None = Field(default=None, ge=1, le=1_000_000)
    ubatch: int | None = Field(default=None, ge=1, le=1_000_000)
    cache_ram: int | None = Field(default=None, ge=0, le=1_000_000)
    ctxcp: int | None = Field(default=None, ge=0, le=128)
    mtp: bool | None = None
    reasoning: str | None = Field(default=None, pattern="^(off|low|medium|high)$")
    image_max_tokens: int | None = Field(default=None, ge=0, le=100_000)
    port: int | None = Field(default=None, ge=1, le=65_535)
    startup_timeout_seconds: int | None = Field(default=None, ge=1, le=86_400)
    sampling: SamplingInput | None = None


def create_app(settings: Settings | None = None) -> FastAPI:
    selected = settings or load_settings()
    app = FastAPI(
        title="AXERA Model Manager",
        version=__version__,
        description="AXERA board model discovery and lifecycle manager.",
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
            allow_methods=["GET", "POST", "PUT", "DELETE"],
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
        rows = []
        for item in records:
            row = summary(item)
            active = app.state.jobs.active_for_model(str(item["id"]))
            if active:
                row["state"] = active.state
                row["active_job"] = active.public()
            else:
                row["active_job"] = None
            rows.append(row)
        return {
            "count": len(records),
            "valid_count": sum(1 for item in records if item["valid"]),
            "models": rows,
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
        record = find_model(model_id)
        active = app.state.jobs.active_for_model(model_id)
        if active:
            record["state"] = active.state
            record["active_job"] = active.public()
        else:
            record["active_job"] = None
        return record

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

    @app.put(
        "/api/v1/config",
        dependencies=[Depends(require_auth), Depends(require_control)],
    )
    async def update_config(body: ConfigUpdateRequest, request: Request) -> dict[str, Any]:
        active_runtimes = [
            job for job in request.app.state.jobs.active()
            if job.action == "start"
        ]
        if body.max_running_models < len(active_runtimes):
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "cannot set limit below active runtime count",
                    "active_runtime_count": len(active_runtimes),
                },
            )

        config_path = Path(app.state.settings.config_path)
        try:
            values = {}
            if config_path.is_file():
                values = json.loads(config_path.read_text(encoding="utf-8"))
            values["max_running_models"] = body.max_running_models
            config_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = config_path.with_suffix(config_path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(values, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, config_path)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=503, detail=f"cannot save config: {exc}") from exc

        app.state.settings = replace(
            app.state.settings,
            max_running_models=body.max_running_models,
        )
        audit(
            request,
            "config_updated",
            max_running_models=body.max_running_models,
            config_path=str(config_path),
        )
        return app.state.settings.public_dict()

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

    def write_user_manifest(record: dict[str, Any], manifest: dict[str, Any], request: Request, action: str) -> dict[str, Any]:
        model_dir = Path(record["directory"])
        result = validate_manifest(manifest, model_dir)
        if not result.valid:
            raise HTTPException(
                status_code=400,
                detail={"message": "invalid manifest", "errors": result.errors},
            )
        override_dir = Path(app.state.settings.data_dir) / "models"
        override_dir.mkdir(parents=True, exist_ok=True)
        override_path = override_dir / f"{record['relative_directory']}.json"
        override_path.write_text(
            json.dumps(result.manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        updated = find_model(str(record["id"]))
        audit(request, action, model=record["id"], profile_path=str(override_path))
        return {
            "model": updated["id"],
            "manifest_source": updated["manifest_source"],
            "ready_to_start": updated["ready_to_start"],
            "profiles": list(updated["manifest"].get("profiles", {}).keys()),
            "path": str(override_path),
        }

    @app.put(
        "/api/v1/models/{model_id}/profiles/{profile_id}",
        dependencies=[Depends(require_auth), Depends(require_control)],
    )
    async def save_profile(
        model_id: str,
        profile_id: str,
        body: ProfileInput,
        request: Request,
    ) -> dict[str, Any]:
        if not is_safe_id(profile_id):
            raise HTTPException(status_code=400, detail="invalid profile name")
        record = find_model(model_id)
        manifest = json.loads(json.dumps(record["manifest"], ensure_ascii=False))
        profiles = manifest.setdefault("profiles", {})
        clean = body.model_dump(exclude_none=True)
        if "sampling" in clean and clean["sampling"] is not None:
            clean["sampling"] = {
                key: value for key, value in clean["sampling"].items()
                if value is not None
            }
        profiles[profile_id] = clean
        return write_user_manifest(record, manifest, request, "profile_saved")

    @app.delete(
        "/api/v1/models/{model_id}/profiles/{profile_id}",
        dependencies=[Depends(require_auth), Depends(require_control)],
    )
    async def delete_profile(
        model_id: str,
        profile_id: str,
        request: Request,
    ) -> dict[str, Any]:
        if profile_id == "default":
            raise HTTPException(status_code=400, detail="default profile cannot be deleted")
        record = find_model(model_id)
        manifest = json.loads(json.dumps(record["manifest"], ensure_ascii=False))
        profiles = manifest.setdefault("profiles", {})
        if profile_id not in profiles:
            raise HTTPException(status_code=404, detail=f"profile not found: {profile_id}")
        del profiles[profile_id]
        return write_user_manifest(record, manifest, request, "profile_deleted")

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
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                        sock.bind(("127.0.0.1", port))
                    return port
                except OSError:
                    time.sleep(0.02)
        raise HTTPException(status_code=409, detail="no free llama-server port")

    def get_runtime_json(port: int, path: str, timeout: float = 0.5) -> Any:
        url = f"http://127.0.0.1:{port}{path}"
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def board_resources() -> dict[str, Any]:
        """Read optional tempmon data for the risk confirmation UI."""

        try:
            data = get_runtime_json(8080, "/api/stats", timeout=0.4)
            temperatures = [
                float(zone["c"]) for zone in data.get("zones", [])
                if isinstance(zone, dict) and isinstance(zone.get("c"), (int, float))
            ]
            return {
                "available": True,
                "cmm": data.get("cmm"),
                "os_memory": data.get("mem"),
                "npu": data.get("npu"),
                "npu_max_temp_c": max(temperatures) if temperatures else None,
            }
        except Exception:
            return {
                "available": False,
                "cmm": None,
                "os_memory": None,
                "npu": None,
                "npu_max_temp_c": None,
            }

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
        profile.update({
            key: value
            for key, value in overrides.items()
            if value is not None and key != "sampling"
        })
        if overrides.get("sampling") is not None:
            profile["sampling"] = {
                **profile.get("sampling", {}),
                **{
                    key: value
                    for key, value in overrides["sampling"].items()
                    if value is not None
                },
            }
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
                        try:
                            model_list = get_runtime_json(job.port, "/v1/models")
                            advertised_models = [
                                str(item.get("id"))
                                for item in model_list.get("data", [])
                                if isinstance(item, dict) and item.get("id")
                            ]
                        except Exception:
                            advertised_models = []
                        jobs.update(
                            job,
                            state="running",
                            runtime_endpoint=f"http://127.0.0.1:{job.port}",
                            runtime_models=advertised_models,
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

    def begin_start(
        request: Request,
        record: dict[str, Any],
        body: StartRequest,
        *,
        ignore_job_ids: set[str] | None = None,
    ) -> Job:
        model_id = str(record["id"])
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
            return existing
        ignore = ignore_job_ids or set()
        active = [
            job for job in request.app.state.jobs.active()
            if job.id not in ignore and job.action == "start"
        ]
        if len(active) >= request.app.state.settings.max_running_models and not body.force:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "model limit reached",
                    "limit": request.app.state.settings.max_running_models,
                    "override_available": True,
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
            **({"force": True} if len(active) >= request.app.state.settings.max_running_models else {}),
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
        return job

    @app.post(
        "/api/v1/models/{model_id}/start",
        dependencies=[Depends(require_auth), Depends(require_control)],
    )
    async def start_model(model_id: str, body: StartRequest, request: Request) -> dict[str, Any]:
        record = find_model(model_id)
        existing = request.app.state.jobs.active_for_model(model_id)
        if existing:
            return {"job": existing.public(), "existing": True}
        job = begin_start(request, record, body)
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

    @app.get("/api/v1/jobs/{job_id}/logs", dependencies=[Depends(require_auth)])
    async def job_logs(
        job_id: str,
        request: Request,
        lines: int = Query(default=200, ge=1, le=5000),
    ) -> dict[str, Any]:
        job = request.app.state.jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"job not found: {job_id}")
        path = Path(job.log_path) if job.log_path else None
        output: list[str] = []
        truncated = False
        if path and path.is_file():
            size = path.stat().st_size
            with path.open("rb") as handle:
                if size > 1024 * 1024:
                    handle.seek(-1024 * 1024, 2)
                    truncated = True
                data = handle.read().decode("utf-8", errors="replace")
            output = data.splitlines()[-lines:]
        return {
            "job_id": job.id,
            "path": str(path) if path else None,
            "truncated": truncated,
            "lines": output,
        }

    @app.get("/api/v1/audit", dependencies=[Depends(require_auth)])
    async def audit_records(request: Request, limit: int = Query(default=100, ge=1, le=1000)) -> dict[str, Any]:
        path = request.app.state.settings.data_dir / "audit.jsonl"
        rows: list[dict[str, Any]] = []
        if path.is_file():
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    try:
                        value = json.loads(line)
                        if isinstance(value, dict):
                            rows.append(value)
                    except json.JSONDecodeError:
                        continue
        return {"records": rows[-limit:]}

    @app.get("/api/v1/dashboard", dependencies=[Depends(require_auth)])
    async def dashboard(request: Request) -> dict[str, Any]:
        try:
            records = scan_models(
                Path(request.app.state.settings.model_root),
                Path(request.app.state.settings.data_dir) / "models",
            )
            model_root_ok = True
            model_root_error = None
        except ModelRootError as exc:
            records = []
            model_root_ok = False
            model_root_error = str(exc)
        active_jobs = request.app.state.jobs.active()
        runtime_jobs = [job for job in active_jobs if job.action == "start"]
        request.app.state.jobs.reconcile_composite_jobs()
        active_jobs = request.app.state.jobs.active()
        runtime_jobs = [job for job in active_jobs if job.action == "start"]
        return {
            "version": __version__,
            "config": request.app.state.settings.public_dict(),
            "model_root_ok": model_root_ok,
            "model_root_error": model_root_error,
            "model_count": len(records),
            "valid_model_count": sum(1 for item in records if item["valid"]),
            "active_job_count": len(runtime_jobs),
            "configured_model_limit": request.app.state.settings.max_running_models,
            "active_jobs": [job.public() for job in active_jobs],
            "resources": board_resources(),
        }

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
            request.app.state.jobs.reconcile_composite_jobs()
            audit(
                request,
                "stop_completed",
                model=model_id,
                job=stop_job.id,
                exit_code=exit_code,
            )

        threading.Thread(target=stop_worker, daemon=True).start()
        return {"job": stop_job.public(), "runtime_job": runtime.public()}

    @app.post(
        "/api/v1/models/{model_id}/restart",
        dependencies=[Depends(require_auth), Depends(require_control)],
    )
    async def restart_model(model_id: str, body: RestartRequest, request: Request) -> dict[str, Any]:
        record = find_model(model_id)
        profiles = record["manifest"].get("profiles", {})
        if not record["ready_to_start"]:
            raise HTTPException(
                status_code=400,
                detail={"message": "model is not ready", "errors": record["errors"]},
            )
        if body.profile not in profiles:
            raise HTTPException(status_code=400, detail=f"profile not found: {body.profile}")

        runtime = request.app.state.jobs.active_for_model(model_id)
        if not runtime:
            raise HTTPException(status_code=409, detail=f"no running model: {model_id}")

        busy = False
        if runtime.state == "running" and runtime.port:
            try:
                slots = get_runtime_json(runtime.port, "/slots")
                busy = any(slot.get("is_processing") for slot in slots if isinstance(slot, dict))
            except Exception:
                busy = True
        if busy and not body.force:
            raise HTTPException(
                status_code=409,
                detail={"error": "model_in_use", "message": "slot is processing"},
            )

        restart_job = request.app.state.jobs.create("restart", model_id, "stopping")
        audit(
            request,
            "restart_requested",
            model=model_id,
            job=restart_job.id,
            runtime_job=runtime.id,
            force=body.force,
        )

        def restart_worker() -> None:
            jobs = request.app.state.jobs
            try:
                jobs.update(runtime, state="stopping")
                exit_code = request.app.state.process_manager.stop(
                    RuntimeHandle(runtime_id=runtime.runtime_id or "", pid=runtime.pid),
                    timeout=float(body.stop_timeout_seconds),
                )
                jobs.update(runtime, state="stopped", exit_code=exit_code)
                jobs.update(restart_job, state="starting")

                start_job = begin_start(
                    request,
                    record,
                    body,
                    ignore_job_ids={restart_job.id},
                )
                deadline = time.monotonic() + 900.0
                while time.monotonic() < deadline:
                    current = jobs.get(start_job.id)
                    if not current:
                        raise RuntimeError("start job disappeared")
                    if current.state == "running":
                        jobs.update(
                            restart_job,
                            state="running",
                            port=current.port,
                            runtime_id=current.runtime_id,
                        )
                        audit(
                            request,
                            "restart_completed",
                            model=model_id,
                            job=restart_job.id,
                            start_job=start_job.id,
                        )
                        return
                    if current.state == "failed":
                        jobs.update(restart_job, state="failed", error=current.error)
                        audit(
                            request,
                            "restart_failed",
                            model=model_id,
                            job=restart_job.id,
                            error=current.error,
                        )
                        return
                    time.sleep(0.1)
                jobs.update(restart_job, state="failed", error="restart timeout")
            except Exception as exc:
                jobs.update(restart_job, state="failed", error=str(exc))
                audit(request, "restart_failed", model=model_id, job=restart_job.id, error=str(exc))

        threading.Thread(target=restart_worker, daemon=True).start()
        return {"job": restart_job.public(), "runtime_job": runtime.public()}

    @app.post(
        "/api/v1/models/{model_id}/switch-to",
        dependencies=[Depends(require_auth), Depends(require_control)],
    )
    async def switch_model(model_id: str, body: SwitchRequest, request: Request) -> dict[str, Any]:
        # Validate the complete target before touching the currently healthy runtime.
        target = find_model(model_id)
        profiles = target["manifest"].get("profiles", {})
        if not target["ready_to_start"]:
            raise HTTPException(
                status_code=400,
                detail={"message": "target model is not ready", "errors": target["errors"]},
            )
        if body.profile not in profiles:
            raise HTTPException(status_code=400, detail=f"profile not found: {body.profile}")

        runtimes = [
            job for job in request.app.state.jobs.active()
            if job.action == "start" and job.state in {"starting", "loading", "running"}
        ]
        if any(job.model_id == model_id for job in runtimes):
            existing = request.app.state.jobs.active_for_model(model_id)
            return {"job": existing.public() if existing else None, "existing": True}

        busy_runtime = None
        for runtime in runtimes:
            if runtime.state != "running" or not runtime.port:
                continue
            try:
                slots = get_runtime_json(runtime.port, "/slots")
                if any(slot.get("is_processing") for slot in slots if isinstance(slot, dict)):
                    busy_runtime = runtime
                    break
            except Exception:
                busy_runtime = runtime
                break
        if busy_runtime and not body.force:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "model_in_use",
                    "message": f"current model {busy_runtime.model_id} has a processing slot",
                },
            )

        switch_job = request.app.state.jobs.create("switch", model_id, "stopping")
        audit(
            request,
            "switch_requested",
            model=model_id,
            job=switch_job.id,
            force=body.force,
            current_models=[runtime.model_id for runtime in runtimes],
        )

        def switch_worker() -> None:
            jobs = request.app.state.jobs
            try:
                for runtime in runtimes:
                    jobs.update(runtime, state="stopping")
                    exit_code = request.app.state.process_manager.stop(
                        RuntimeHandle(runtime_id=runtime.runtime_id or "", pid=runtime.pid),
                        timeout=float(body.stop_timeout_seconds),
                    )
                    jobs.update(runtime, state="stopped", exit_code=exit_code)

                jobs.update(switch_job, state="starting")
                start_body = body.model_copy(update={"port": None})
                start_job = begin_start(
                    request,
                    target,
                    start_body,
                    ignore_job_ids={switch_job.id},
                )
                deadline = time.monotonic() + 900.0
                while time.monotonic() < deadline:
                    current = jobs.get(start_job.id)
                    if not current:
                        raise RuntimeError("start job disappeared")
                    if current.state == "running":
                        jobs.update(switch_job, state="running", port=current.port, runtime_id=current.runtime_id)
                        audit(request, "switch_completed", model=model_id, job=switch_job.id, start_job=start_job.id)
                        return
                    if current.state == "failed":
                        jobs.update(switch_job, state="failed", error=current.error)
                        audit(
                            request,
                            "switch_failed",
                            model=model_id,
                            job=switch_job.id,
                            error=current.error,
                        )
                        return
                    time.sleep(0.1)
                jobs.update(switch_job, state="failed", error="switch timeout")
            except Exception as exc:
                jobs.update(switch_job, state="failed", error=str(exc))
                audit(request, "switch_failed", model=model_id, job=switch_job.id, error=str(exc))

        threading.Thread(target=switch_worker, daemon=True).start()
        return {
            "job": switch_job.public(),
            "stopping_jobs": [runtime.public() for runtime in runtimes],
        }

    static_dir = Path(__file__).with_name("static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/ui/{filename}", include_in_schema=False)
    async def static_file(filename: str) -> FileResponse:
        # Keep static serving explicit and bounded instead of mounting a broad directory.
        allowed = {"styles.css", "app.js"}
        if filename not in allowed:
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(static_dir / filename)

    return app


app = create_app()
