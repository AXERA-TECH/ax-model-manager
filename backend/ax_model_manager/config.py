from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_PATH = Path("/etc/axera/model-manager/config.json")


@dataclass(frozen=True)
class Settings:
    config_path: Path = DEFAULT_CONFIG_PATH
    model_root: Path = Path("/mnt/models")
    manager_port: int = 18090
    llama_port_min: int = 18080
    llama_port_max: int = 18089
    data_dir: Path = Path("/var/lib/axera-model-manager")
    read_only: bool = True
    enable_control: bool = False
    process_backend: str = "none"
    max_running_models: int = 1
    min_free_cmm_mb: int = 1024
    min_os_available_mb: int = 1024
    max_npu_temp_c: float = 85.0
    auth_token: str = ""
    allowed_origins: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("manager_port", "llama_port_min", "llama_port_max"):
            value = getattr(self, name)
            if not 1 <= value <= 65535:
                raise ValueError(f"{name} must be between 1 and 65535")
        if self.llama_port_min > self.llama_port_max:
            raise ValueError("llama_port_min cannot be greater than llama_port_max")
        if self.llama_port_min <= self.manager_port <= self.llama_port_max:
            raise ValueError("manager_port must not overlap the llama-server port range")
        allowed_backends = {"fake-subprocess", "systemd"}
        if self.enable_control and self.process_backend not in allowed_backends:
            raise ValueError(
                "enable_control requires process_backend=fake-subprocess or systemd"
            )
        if self.max_running_models < 1:
            raise ValueError("max_running_models must be at least 1")
        if self.min_free_cmm_mb < 0 or self.min_os_available_mb < 0:
            raise ValueError("memory thresholds cannot be negative")
        if not 40.0 <= self.max_npu_temp_c <= 120.0:
            raise ValueError("max_npu_temp_c must be between 40 and 120")

    def public_dict(self) -> dict[str, Any]:
        return {
            "model_root": str(self.model_root),
            "manager_port": self.manager_port,
            "llama_port_min": self.llama_port_min,
            "llama_port_max": self.llama_port_max,
            "data_dir": str(self.data_dir),
            "read_only": self.read_only,
            "enable_control": self.enable_control,
            "process_backend": self.process_backend,
            "max_running_models": self.max_running_models,
            "min_free_cmm_mb": self.min_free_cmm_mb,
            "min_os_available_mb": self.min_os_available_mb,
            "max_npu_temp_c": self.max_npu_temp_c,
            "authentication": bool(self.auth_token),
            "allowed_origins": list(self.allowed_origins),
        }


def load_settings(config_path: str | Path | None = None) -> Settings:
    """Load settings without introducing a second configuration system."""

    path = Path(config_path or os.environ.get("AX_MODEL_MANAGER_CONFIG", DEFAULT_CONFIG_PATH))
    values: dict[str, Any] = {}
    if path.is_file():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError(f"config must contain a JSON object: {path}")
        values = loaded

    # Environment overrides are intentionally useful for local development and tests.
    if os.environ.get("AX_MODEL_MANAGER_MODEL_ROOT"):
        values["model_root"] = os.environ["AX_MODEL_MANAGER_MODEL_ROOT"]
    if os.environ.get("AX_MODEL_MANAGER_TOKEN"):
        values["auth_token"] = os.environ["AX_MODEL_MANAGER_TOKEN"]

    known = Settings.__dataclass_fields__.keys()
    unknown = sorted(set(values) - set(known))
    if unknown:
        raise ValueError(f"unknown config keys: {', '.join(unknown)}")

    model_root = Path(values.get("model_root", Settings.model_root)).expanduser()
    data_dir = Path(values.get("data_dir", Settings.data_dir)).expanduser()
    return Settings(
        config_path=path,
        model_root=model_root,
        manager_port=int(values.get("manager_port", Settings.manager_port)),
        llama_port_min=int(values.get("llama_port_min", Settings.llama_port_min)),
        llama_port_max=int(values.get("llama_port_max", Settings.llama_port_max)),
        data_dir=data_dir,
        read_only=bool(values.get("read_only", True)),
        enable_control=bool(values.get("enable_control", False)),
        process_backend=str(values.get("process_backend", "none")),
        max_running_models=int(values.get("max_running_models", 1)),
        min_free_cmm_mb=int(values.get("min_free_cmm_mb", 1024)),
        min_os_available_mb=int(values.get("min_os_available_mb", 1024)),
        max_npu_temp_c=float(values.get("max_npu_temp_c", 85.0)),
        auth_token=str(values.get("auth_token", "")),
        allowed_origins=tuple(str(item) for item in values.get("allowed_origins", [])),
    )
