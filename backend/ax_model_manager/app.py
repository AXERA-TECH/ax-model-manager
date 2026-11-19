from __future__ import annotations

import json
import os
import re
import socket
import threading
import time
import urllib.request
from hmac import compare_digest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
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
    metrics: bool | None = None
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
    metrics: bool | None = None
    reasoning: str | None = Field(default=None, pattern="^(off|low|medium|high)$")
    image_max_tokens: int | None = Field(default=None, ge=0, le=100_000)
    port: int | None = Field(default=None, ge=1, le=65_535)
    startup_timeout_seconds: int | None = Field(default=None, ge=1, le=86_400)
    sampling: SamplingInput | None = None


class ProfileDuplicateRequest(BaseModel):
    target: str = Field(min_length=1, max_length=128)


RESOURCE_MAX_PATHS = {
    "cpu_percent": ("cpu_percent",),
    "cpu_load_1m": ("load", "1"),
    "os_used_kb": ("os_memory", "used_kb"),
    "cmm_used_kb": ("cmm", "used_kb"),
    "rx_mbps": ("net", "rx_mbps"),
    "tx_mbps": ("net", "tx_mbps"),
    "npu_temp_c": ("npu_max_temp_c",),
    "llama_rss_kb": ("llama", "rss_kb"),
}


def read_cpu_times(path: str = "/proc/stat") -> tuple[int, int] | None:
    """Read aggregate CPU busy/total jiffies from procfs."""

    try:
        with open(path, encoding="ascii") as handle:
            for line in handle:
                if not line.startswith("cpu "):
                    continue
                values = line.split()[1:]
                numbers = [int(value) for value in values[:8]]
                if len(numbers) < 4:
                    return None
                user, nice, system, idle = numbers[:4]
                iowait = numbers[4] if len(numbers) > 4 else 0
                irq = numbers[5] if len(numbers) > 5 else 0
                softirq = numbers[6] if len(numbers) > 6 else 0
                steal = numbers[7] if len(numbers) > 7 else 0
                busy = user + nice + system + irq + softirq + steal
                total = busy + idle + iowait
                return busy, total
    except (OSError, ValueError):
        return None
    return None


def cpu_percent_between(previous: tuple[int, int] | None, current: tuple[int, int] | None) -> float | None:
    if not previous or not current:
        return None
    busy_delta = current[0] - previous[0]
    total_delta = current[1] - previous[1]
    if busy_delta < 0 or total_delta <= 0:
        return None
    return round(min(100.0, busy_delta / total_delta * 100.0), 2)


def value_at_path(value: Any, path: tuple[str, ...]) -> float | None:
    current = value
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    if isinstance(current, bool) or not isinstance(current, (int, float)):
        return None
    return float(current)


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
    app.state.resource_state = {
        "lock": threading.Lock(),
        "last_cpu_times": read_cpu_times(),
        "max": {name: None for name in RESOURCE_MAX_PATHS},
        "max_reset_at": None,
        "sample_count": 0,
    }

    def failure_code(message: str | None) -> str:
        text = str(message or "").lower()
        if "did not become ready" in text or "timeout" in text:
            return "startup_timeout"
        if "out of memory" in text or "oom" in text or "cannot allocate" in text:
            return "out_of_memory"
        if "no free llama-server port" in text or "address already in use" in text:
            return "port_conflict"
        if "binary or model artifact is missing" in text:
            return "artifact_missing"
        if "not executable" in text:
            return "binary_not_executable"
        if "exited before becoming ready" in text:
            return "runtime_exited"
        if "unauthorized" in text:
            return "unauthorized"
        return "runtime_error"

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
        record["recent_jobs"] = [
            job.public()
            for job in app.state.jobs.list()
            if job.model_id == model_id
        ][:10]
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
            # The config contains the bearer token and is replaced through a
            # sibling temporary file. Keep it private even on installations
            # that were originally created with a permissive umask.
            os.chmod(temporary, 0o600)
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

    def find_base_model(model_id: str) -> dict[str, Any]:
        try:
            records = scan_models(Path(app.state.settings.model_root), None)
        except ModelRootError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        for record in records:
            if record["id"] == model_id:
                return record
        raise HTTPException(status_code=404, detail=f"model not found: {model_id}")

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
            incoming_sampling = {
                key: value for key, value in clean["sampling"].items()
                if value is not None
            }
            clean["sampling"] = {
                **profiles.get(profile_id, {}).get("sampling", {}),
                **incoming_sampling,
            }
        profiles[profile_id] = {**profiles.get(profile_id, {}), **clean}
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

    @app.post(
        "/api/v1/models/{model_id}/profiles/{profile_id}/duplicate",
        dependencies=[Depends(require_auth), Depends(require_control)],
    )
    async def duplicate_profile(
        model_id: str,
        profile_id: str,
        body: ProfileDuplicateRequest,
        request: Request,
    ) -> dict[str, Any]:
        if not is_safe_id(body.target):
            raise HTTPException(status_code=400, detail="invalid target profile name")
        record = find_model(model_id)
        profiles = record["manifest"].get("profiles", {})
        if profile_id not in profiles:
            raise HTTPException(status_code=404, detail=f"profile not found: {profile_id}")
        if body.target in profiles:
            raise HTTPException(status_code=409, detail=f"profile already exists: {body.target}")
        manifest = json.loads(json.dumps(record["manifest"], ensure_ascii=False))
        manifest.setdefault("profiles", {})[body.target] = json.loads(json.dumps(
            profiles[profile_id],
            ensure_ascii=False,
        ))
        return write_user_manifest(record, manifest, request, "profile_duplicated")

    @app.post(
        "/api/v1/models/{model_id}/profiles/{profile_id}/reset",
        dependencies=[Depends(require_auth), Depends(require_control)],
    )
    async def reset_profile(model_id: str, profile_id: str, request: Request) -> dict[str, Any]:
        record = find_model(model_id)
        base = find_base_model(model_id)
        base_profiles = base.get("manifest", {}).get("profiles", {})
        source_profile = profile_id if profile_id in base_profiles else "default"
        if source_profile not in base_profiles:
            raise HTTPException(
                status_code=404,
                detail="base default profile not found",
            )
        manifest = json.loads(json.dumps(record["manifest"], ensure_ascii=False))
        manifest.setdefault("profiles", {})[profile_id] = json.loads(json.dumps(
            base_profiles[source_profile],
            ensure_ascii=False,
        ))
        return write_user_manifest(record, manifest, request, "profile_reset")

    @app.get(
        "/api/v1/models/{model_id}/profiles/{left}/diff/{right}",
        dependencies=[Depends(require_auth)],
    )
    async def profile_diff(model_id: str, left: str, right: str) -> dict[str, Any]:
        profiles = find_model(model_id)["manifest"].get("profiles", {})
        if left not in profiles or right not in profiles:
            missing = [name for name in (left, right) if name not in profiles]
            raise HTTPException(status_code=404, detail=f"profiles not found: {', '.join(missing)}")

        def flatten(value: dict[str, Any], prefix: str = "") -> dict[str, Any]:
            rows: dict[str, Any] = {}
            for key, item in value.items():
                name = f"{prefix}.{key}" if prefix else key
                if isinstance(item, dict):
                    rows.update(flatten(item, name))
                else:
                    rows[name] = item
            return rows

        left_rows = flatten(profiles[left])
        right_rows = flatten(profiles[right])
        changes: list[dict[str, Any]] = []
        for key in sorted(set(left_rows) | set(right_rows)):
            if key not in left_rows:
                changes.append({"field": key, "left": None, "right": right_rows[key], "change": "added"})
            elif key not in right_rows:
                changes.append({"field": key, "left": left_rows[key], "right": None, "change": "removed"})
            elif left_rows[key] != right_rows[key]:
                changes.append({"field": key, "left": left_rows[key], "right": right_rows[key], "change": "changed"})
        return {"model": model_id, "left": left, "right": right, "changes": changes}

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
        """Read the board's optional tempmon payload for the resource panel."""

        try:
            data = get_runtime_json(8080, "/api/stats", timeout=0.4)
            agent = data.get("agent") if isinstance(data.get("agent"), dict) else {}
            temperatures = [
                float(zone["c"]) for zone in data.get("zones", [])
                if isinstance(zone, dict) and isinstance(zone.get("c"), (int, float))
            ]
            cpu_frequencies = [
                float(value) for value in data.get("cpu_freqs", [])
                if isinstance(value, (int, float))
            ]
            return {
                "available": True,
                "sampled_at": data.get("time"),
                "agent": {
                    "version": agent.get("version"),
                    "bsp_version": agent.get("bsp_version"),
                    "chip_type": agent.get("chip_type"),
                },
                "load": data.get("load"),
                "cpu": {
                    "cores": data.get("cpu_cores"),
                    "frequency_mhz": cpu_frequencies,
                    "frequency_min_mhz": min(cpu_frequencies) if cpu_frequencies else None,
                    "frequency_max_mhz": max(cpu_frequencies) if cpu_frequencies else None,
                },
                "cmm": data.get("cmm"),
                "os_memory": data.get("mem"),
                "npu": data.get("npu"),
                "npu_max_temp_c": max(temperatures) if temperatures else None,
                "thermal_zones": data.get("zones", []),
                "disks": data.get("disks", []),
                "net": data.get("net"),
                "llama": data.get("llama"),
            }
        except Exception:
            return {
                "available": False,
                "sampled_at": None,
                "agent": None,
                "load": None,
                "cpu": None,
                "cmm": None,
                "os_memory": None,
                "npu": None,
                "npu_max_temp_c": None,
                "thermal_zones": [],
                "disks": [],
                "net": None,
                "llama": None,
            }
    app.state.read_board_resources = board_resources

    def sample_resources(request: Request) -> dict[str, Any]:
        """Sample board resources and update the process-local maxima."""

        current = request.app.state.read_board_resources()
        state = request.app.state.resource_state
        with state["lock"]:
            current_cpu_times = read_cpu_times()
            cpu_percent = cpu_percent_between(state["last_cpu_times"], current_cpu_times)
            state["last_cpu_times"] = current_cpu_times or state["last_cpu_times"]
            current["cpu_percent"] = cpu_percent
            if isinstance(current.get("cpu"), dict):
                current["cpu"]["percent"] = cpu_percent

            sampled_at = current.get("sampled_at") or time.time()
            maxima = state["max"]
            for name, path in RESOURCE_MAX_PATHS.items():
                value = value_at_path(current, path)
                if value is None:
                    continue
                previous = maxima.get(name)
                if previous is None or value > previous:
                    maxima[name] = value
            state["sample_count"] += 1
            return {
                "current": current,
                "max": dict(maxima),
                "sampled_at": sampled_at,
                "sample_count": state["sample_count"],
                "max_reset_at": state["max_reset_at"],
                "sampling": "on-request",
            }

    def resource_preflight(request: Request, force: bool = False) -> dict[str, Any]:
        resources = sample_resources(request)["current"]
        if not resources.get("available"):
            if request.app.state.settings.process_backend == "systemd":
                raise HTTPException(
                    status_code=503,
                    detail={
                        "error": "resource_monitor_unavailable",
                        "message": "tempmon is unavailable; cannot verify board memory",
                    },
                )
            return {"resources": resources, "ok": True, "findings": []}
        findings: list[dict[str, Any]] = []
        cmm = resources.get("cmm") or {}
        memory = resources.get("os_memory") or {}
        try:
            free_cmm_mb = (int(cmm.get("total_kb", 0)) - int(cmm.get("used_kb", 0))) / 1024
        except (TypeError, ValueError):
            free_cmm_mb = None
        try:
            os_available_mb = int(memory.get("avail_kb", 0)) / 1024
        except (TypeError, ValueError):
            os_available_mb = None
        temperature = resources.get("npu_max_temp_c")

        if free_cmm_mb is not None and free_cmm_mb < request.app.state.settings.min_free_cmm_mb:
            findings.append({
                "code": "cmm_low",
                "message": f"CMM free {free_cmm_mb:.0f}MB is below {request.app.state.settings.min_free_cmm_mb}MB",
                "value": round(free_cmm_mb, 1),
                "threshold": request.app.state.settings.min_free_cmm_mb,
            })
        if os_available_mb is not None and os_available_mb < request.app.state.settings.min_os_available_mb:
            findings.append({
                "code": "os_memory_low",
                "message": f"OS available {os_available_mb:.0f}MB is below {request.app.state.settings.min_os_available_mb}MB",
                "value": round(os_available_mb, 1),
                "threshold": request.app.state.settings.min_os_available_mb,
            })
        if isinstance(temperature, (int, float)) and temperature > request.app.state.settings.max_npu_temp_c:
            findings.append({
                "code": "npu_temperature_high",
                "message": f"NPU temperature {temperature:.1f}C is above {request.app.state.settings.max_npu_temp_c:.1f}C",
                "value": temperature,
                "threshold": request.app.state.settings.max_npu_temp_c,
            })

        result = {
            "resources": resources,
            "ok": not findings,
            "findings": findings,
        }
        if findings and not force:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "resource_check_failed",
                    "message": "; ".join(item["message"] for item in findings),
                    "findings": findings,
                },
            )
        return result

    def refresh_runtime_job(job: Job) -> None:
        if not job.runtime_id:
            app.state.jobs.update(
                job,
                state="failed",
                error="runtime_id is missing",
                failure_code="runtime_metadata_missing",
            )
            return
        handle = RuntimeHandle(runtime_id=job.runtime_id, pid=job.pid)
        if not app.state.process_manager.is_alive(handle):
            previous_state = job.state
            app.state.jobs.update(
                job,
                state="failed" if previous_state in {"starting", "loading", "running"} else "stopped",
                error=f"runtime disappeared while {previous_state}",
                failure_code="runtime_disappeared",
            )
            return
        if job.state == "stopping":
            return
        try:
            health = get_runtime_json(job.port, "/health", timeout=0.3) if job.port else None
        except Exception:
            health = None
        if health and health.get("status") == "ok":
            advertised_models: list[str] = []
            try:
                model_list = get_runtime_json(job.port, "/v1/models", timeout=0.3)
                advertised_models = [
                    str(item.get("id"))
                    for item in model_list.get("data", [])
                    if isinstance(item, dict) and item.get("id")
                ]
            except Exception:
                pass
            app.state.jobs.update(
                job,
                state="running",
                runtime_endpoint=f"http://127.0.0.1:{job.port}",
                runtime_models=advertised_models,
            )
        elif job.state not in {"starting", "loading"}:
            app.state.jobs.update(job, state="loading")

    def runtime_orphans() -> list[dict[str, Any]]:
        if not hasattr(app.state.process_manager, "list_runtimes"):
            return []
        active_runtime_ids = {
            job.runtime_id
            for job in app.state.jobs.active()
            if job.runtime_id
        }
        orphans: list[dict[str, Any]] = []
        for runtime in app.state.process_manager.list_runtimes():
            if runtime["runtime_id"] in active_runtime_ids:
                continue
            command = runtime.get("command") or ""
            port_match = re.search(r"--port[= ]([0-9]+)", command)
            original_job_match = re.search(r"job_[0-9]+_[0-9a-f]+", runtime["runtime_id"])
            original_log_path = None
            if original_job_match:
                candidate = (
                    request.app.state.settings.data_dir
                    / "logs"
                    / f"{original_job_match.group(0)}.log"
                )
                original_log_path = str(candidate) if candidate.is_file() else None
            runtime = {
                **runtime,
                "port": int(port_match.group(1)) if port_match else None,
                "health": None,
                "log_path": original_log_path,
            }
            if runtime["port"]:
                try:
                    runtime["health"] = get_runtime_json(runtime["port"], "/health", timeout=0.3)
                except Exception:
                    pass
            orphans.append(runtime)
        return orphans

    def reconcile_runtime_state() -> dict[str, Any]:
        for job in app.state.jobs.active():
            if job.action == "start":
                refresh_runtime_job(job)
        app.state.jobs.reconcile_composite_jobs()
        orphans = runtime_orphans()
        return {
            "reconciled_at": time.time(),
            "active_runtime_count": len([
                job for job in app.state.jobs.active()
                if job.action == "start" and job.state != "stopping"
            ]),
            "orphan_count": len(orphans),
            "orphans": orphans,
        }

    app.state.runtime_reconciliation = reconcile_runtime_state()

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
        acoustic = artifact_path(record, "acoustic")
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
        capabilities = set(record.get("capabilities", []))
        is_voxcpm2 = acoustic is not None
        is_asr_server = "audio" in capabilities and "tts" not in capabilities

        argv: list[str] = [
            str(binary),
            "-m", str(model),
            "-ngl", "99",
            "-np", "1",
        ]
        if "batch" in profile:
            argv.extend(["-b", str(profile["batch"])])
        if "ubatch" in profile:
            argv.extend(["-ub", str(profile["ubatch"])])
        if "context" in profile:
            argv.extend(["-c", str(profile["context"])])
        if "ctxcp" in profile:
            argv.extend(["-ctxcp", str(profile["ctxcp"])])
        if "cache_ram" in profile:
            argv.extend(["--cache-ram", str(profile["cache_ram"])])
        if "threads" in profile:
            argv.extend(["-t", str(profile["threads"])])
        if "http_threads" in profile:
            argv.extend(["--threads-http", str(profile["http_threads"])])
        if profile.get("metrics") is True:
            argv.append("--metrics")

        if is_voxcpm2:
            argv.extend(["--voxcpm2-acoustic", str(acoustic)])
        else:
            if "image_max_tokens" in profile:
                argv.extend(["--image-max-tokens", str(profile["image_max_tokens"])])
            if mmproj is not None:
                argv.extend(["--mmproj", str(mmproj)])
            if template is not None:
                argv.extend(["--chat-template-file", str(template), "--jinja"])

        if not is_voxcpm2 and not is_asr_server and "reasoning" in profile:
            argv.extend(["--reasoning", profile["reasoning"]])
        if not is_voxcpm2 and not is_asr_server and "mtp" in profile:
            if profile["mtp"]:
                argv.extend([
                    "--spec-type", "draft-mtp",
                    "--spec-draft-n-max", "3",
                    "--spec-draft-sampling", "greedy",
                ])
            else:
                argv.extend(["--spec-type", "none"])

        if not is_voxcpm2 and not is_asr_server:
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
        for key, value in profile.get("env", {}).items():
            env[str(key)] = str(value)
        if kernels is not None:
            env["GGML_AXERA_KERNEL_DIR"] = str(kernels)
        return ProcessSpec(
            argv=tuple(argv),
            cwd=Path(record["directory"]),
            log_path=request.app.state.settings.data_dir / "logs" / f"{job.id}.log",
            env=env,
            runtime_id=runtime_id,
        )

    @app.post("/api/v1/models/{model_id}/preview", dependencies=[Depends(require_auth)])
    async def preview_launch(model_id: str, body: StartRequest, request: Request) -> dict[str, Any]:
        record = find_model(model_id)
        if record.get("runtime_type") == "task":
            raise HTTPException(
                status_code=409,
                detail="one-shot task models do not expose a long-running server launch command",
            )
        profiles = record["manifest"].get("profiles", {})
        if body.profile not in profiles:
            raise HTTPException(status_code=400, detail=f"profile not found: {body.profile}")
        port = allocate_port(request, body.port)
        preview_job = SimpleNamespace(id="preview", port=port)
        spec = build_process_spec(
            request,
            preview_job,
            record,
            body.profile,
            body.model_dump(exclude={"profile", "port", "force", "debug_busy"}),
            body.debug_busy,
        )
        return {
            "model": model_id,
            "profile": body.profile,
            "port": port,
            "argv": list(spec.argv),
            "cwd": str(spec.cwd),
            "env": spec.env,
            "log_path": str(spec.log_path),
        }

    def monitor_start(
        request: Request,
        job: Job,
        record: dict[str, Any],
        debug_busy: bool = False,
        profile_name: str = "default",
        overrides: dict[str, Any] | None = None,
    ) -> None:
        jobs = request.app.state.jobs
        handle: RuntimeHandle | None = None
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
            base_profile = record["manifest"].get("profiles", {}).get(profile_name, {})
            timeout = float(
                (overrides or {}).get("startup_timeout_seconds", 0)
                or base_profile.get("startup_timeout_seconds", 0)
                or 600
            )
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
            raise TimeoutError(f"runtime did not become ready within {timeout:g} seconds")
        except Exception as exc:
            code = failure_code(str(exc))
            if handle is not None:
                try:
                    request.app.state.process_manager.stop(handle, timeout=5.0)
                except Exception as cleanup_error:
                    jobs.event(job, "cleanup_failed", error=str(cleanup_error))
            jobs.update(job, state="failed", error=str(exc), failure_code=code)
            jobs.event(job, "start_failed", error=str(exc), failure_code=code)
            audit(request, "start_failed", model=record["id"], job=job.id, error=str(exc), failure_code=code)

    def begin_start(
        request: Request,
        record: dict[str, Any],
        body: StartRequest,
        *,
        ignore_job_ids: set[str] | None = None,
    ) -> Job:
        model_id = str(record["id"])
        if record.get("runtime_type") == "task":
            raise HTTPException(
                status_code=400,
                detail="one-shot task models cannot be started as a long-running service",
            )
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
        resource_preflight(request, force=body.force)
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

    @app.get("/api/v1/runtimes", dependencies=[Depends(require_auth)])
    async def runtimes(request: Request) -> dict[str, Any]:
        reconciliation = reconcile_runtime_state()
        request.app.state.runtime_reconciliation = reconciliation
        return reconciliation

    @app.post(
        "/api/v1/runtimes/{runtime_id}/adopt",
        dependencies=[Depends(require_auth), Depends(require_control)],
    )
    async def adopt_runtime(runtime_id: str, request: Request) -> dict[str, Any]:
        orphan = next(
            (item for item in runtime_orphans() if item["runtime_id"] == runtime_id),
            None,
        )
        if not orphan:
            raise HTTPException(status_code=404, detail=f"orphan runtime not found: {runtime_id}")
        command = orphan.get("command") or ""
        records = scan_models(
            Path(request.app.state.settings.model_root),
            Path(request.app.state.settings.data_dir) / "models",
        )
        record = next((item for item in records if str(item["directory"]) in command), None)
        if not record:
            raise HTTPException(
                status_code=409,
                detail={"message": "cannot map orphan runtime to a model", "command": command},
            )
        port = orphan.get("port")
        if not port:
            raise HTTPException(status_code=409, detail="orphan runtime has no parseable --port")

        advertised_models: list[str] = []
        try:
            model_list = get_runtime_json(port, "/v1/models", timeout=0.3)
            advertised_models = [
                str(item.get("id"))
                for item in model_list.get("data", [])
                if isinstance(item, dict) and item.get("id")
            ]
        except Exception:
            pass
        job = request.app.state.jobs.create("start", str(record["id"]), "running")
        request.app.state.jobs.update(
            job,
            port=port,
            pid=orphan.get("pid"),
            runtime_id=runtime_id,
            runtime_endpoint=f"http://127.0.0.1:{port}",
            runtime_models=advertised_models,
            log_path=orphan.get("log_path"),
        )
        request.app.state.jobs.event(job, "orphan_runtime_adopted", runtime_id=runtime_id)
        audit(request, "runtime_adopted", model=record["id"], job=job.id, runtime_id=runtime_id, port=port)
        return {"job": job.public(), "runtime": orphan}

    @app.post(
        "/api/v1/runtimes/{runtime_id}/stop",
        dependencies=[Depends(require_auth), Depends(require_control)],
    )
    async def stop_runtime(runtime_id: str, request: Request) -> dict[str, Any]:
        orphan = next(
            (item for item in runtime_orphans() if item["runtime_id"] == runtime_id),
            None,
        )
        if not orphan:
            raise HTTPException(status_code=404, detail=f"orphan runtime not found: {runtime_id}")
        exit_code = request.app.state.process_manager.stop(
            RuntimeHandle(runtime_id=runtime_id, pid=orphan.get("pid")),
            timeout=20.0,
        )
        audit(request, "orphan_runtime_stopped", runtime_id=runtime_id, exit_code=exit_code)
        return {"runtime_id": runtime_id, "stopped": True, "exit_code": exit_code}

    @app.get("/api/v1/jobs", dependencies=[Depends(require_auth)])
    async def list_jobs(request: Request) -> dict[str, Any]:
        return {"jobs": [job.public() for job in request.app.state.jobs.list()]}

    @app.get("/api/v1/jobs/{job_id}", dependencies=[Depends(require_auth)])
    async def get_job(job_id: str, request: Request) -> dict[str, Any]:
        job = request.app.state.jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"job not found: {job_id}")
        return job.public()

    @app.get(
        "/api/v1/jobs/{job_id}/logs",
        dependencies=[Depends(require_auth)],
        response_model=None,
    )
    async def job_logs(
        job_id: str,
        request: Request,
        lines: int = Query(default=200, ge=1, le=5000),
        search: str = Query(default="", max_length=200),
        errors_only: bool = Query(default=False),
        output_format: str = Query(default="json", pattern="^(json|text)$"),
    ) -> dict[str, Any] | PlainTextResponse:
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
        if search:
            needle = search.lower()
            output = [line for line in output if needle in line.lower()]
        if errors_only:
            output = [
                line for line in output
                if re.search(r"error|failed|failure|exception|traceback|oom|out of memory", line, re.I)
            ]
        if output_format == "text":
            return PlainTextResponse(
                "\n".join(output) + ("\n" if output else ""),
                media_type="text/plain; charset=utf-8",
            )
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
        reconciliation = reconcile_runtime_state()
        request.app.state.runtime_reconciliation = reconciliation
        active_jobs = request.app.state.jobs.active()
        runtime_jobs = [job for job in active_jobs if job.action == "start"]
        resource_monitor = sample_resources(request)
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
            "resources": resource_monitor["current"],
            "resource_monitor": resource_monitor,
            "runtime_reconciliation": reconciliation,
        }

    @app.get("/api/v1/resources", dependencies=[Depends(require_auth)])
    async def resources(request: Request) -> dict[str, Any]:
        return sample_resources(request)

    @app.post("/api/v1/resources/max/reset", dependencies=[Depends(require_auth)])
    async def reset_resource_maxima(request: Request) -> dict[str, Any]:
        state = request.app.state.resource_state
        with state["lock"]:
            cleared = dict(state["max"])
            state["max"] = {name: None for name in RESOURCE_MAX_PATHS}
            state["max_reset_at"] = time.time()
            state["sample_count"] = 0
        return {
            "cleared_max": cleared,
            **sample_resources(request),
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
