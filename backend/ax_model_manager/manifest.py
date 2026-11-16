from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")
ARTIFACT_KINDS = {
    "binary": "file",
    "model": "file",
    "mmproj": "file",
    "chat_template": "file",
    "kernels": "directory",
}
PROFILE_KEYS = {
    "context", "batch", "ubatch", "cache_ram", "ctxcp", "mtp", "reasoning",
    "image_max_tokens", "port", "startup_timeout_seconds", "sampling",
}
SAMPLING_KEYS = {
    "temperature", "top_k", "top_p", "min_p", "repeat_last_n",
    "repeat_penalty", "presence_penalty", "frequency_penalty",
}
SAMPLING_RANGES = {
    "temperature": (0.0, 2.0),
    "top_k": (0, 1_000_000),
    "top_p": (0.0, 1.0),
    "min_p": (0.0, 1.0),
    "repeat_last_n": (0, 1_000_000),
    "repeat_penalty": (0.0, 10.0),
    "presence_penalty": (-2.0, 2.0),
    "frequency_penalty": (-2.0, 2.0),
}
CAPABILITIES = {"text", "image", "audio", "tts", "image_gen"}
ENGINES = {"llama-server", "llama-tts", "llama-mtmd-cli", "llama-cli"}


@dataclass
class ManifestResult:
    manifest: dict[str, Any]
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9_.-]+", "-", value.lower()).strip("-.")
    return slug[:128] or "model"


def is_safe_id(value: Any) -> bool:
    return isinstance(value, str) and bool(SAFE_ID.fullmatch(value))


def _resolved_within(model_dir: Path, relative: Any) -> tuple[Path | None, Path | None, str | None]:
    if not isinstance(relative, str) or not relative or relative in {".", "./"}:
        return None, None, "path must be a non-empty relative value"
    if relative.startswith("/") or relative.startswith("\\") or "\x00" in relative:
        return None, None, f"absolute or invalid path is not allowed: {relative!r}"
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None, None, f"path traversal is not allowed: {relative!r}"

    root = model_dir.resolve(strict=False)
    resolved = (root / candidate).resolve(strict=False)
    if resolved != root and root not in resolved.parents:
        return None, None, f"path escapes model directory: {relative!r}"
    return candidate, resolved, None


def check_artifact(
    model_dir: Path,
    key: str,
    value: Any,
    *,
    required: bool = False,
) -> dict[str, Any]:
    errors: list[str] = []
    relative: Path | None = None
    resolved: Path | None = None

    if value is None:
        if required:
            errors.append(f"artifact.{key} is required")
    else:
        relative, resolved, error = _resolved_within(model_dir, value)
        if error:
            errors.append(f"artifact.{key}: {error}")
        else:
            expected_kind = ARTIFACT_KINDS.get(key, "file")
            if expected_kind == "file" and not resolved.is_file():
                errors.append(f"artifact.{key} does not exist or is not a file: {value!r}")
            elif expected_kind == "directory" and not resolved.is_dir():
                errors.append(f"artifact.{key} does not exist or is not a directory: {value!r}")

    return {
        "key": key,
        "path": str(relative) if relative is not None else None,
        "kind": ARTIFACT_KINDS.get(key, "file"),
        "required": required,
        "exists": bool(resolved and (
            resolved.is_file() if ARTIFACT_KINDS.get(key, "file") == "file" else resolved.is_dir()
        )),
        "executable": bool(resolved and resolved.is_file() and os_access_cached(resolved)),
        "size_bytes": resolved.stat().st_size if resolved and resolved.is_file() else None,
        "errors": errors,
    }


def os_access_cached(path: Path) -> bool:
    # Kept as a tiny helper to make path permission checks easy to observe in tests.
    import os
    return os.access(path, os.X_OK)


def _validate_profiles(profiles: Any) -> tuple[dict[str, Any], list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(profiles, dict) or not profiles:
        return {}, ["profiles must be a non-empty object"], warnings
    if "default" not in profiles:
        errors.append("profiles.default is required")

    normalized: dict[str, Any] = {}
    for name, profile in profiles.items():
        if not is_safe_id(str(name)):
            errors.append(f"invalid profile name: {name!r}")
            continue
        if not isinstance(profile, dict):
            errors.append(f"profiles.{name} must be an object")
            continue
        unknown = sorted(set(profile) - PROFILE_KEYS)
        if unknown:
            errors.append(f"profiles.{name} has unknown keys: {', '.join(unknown)}")

        integer_fields = {
            "context": (1024, 100_000_000),
            "batch": (1, 1_000_000),
            "ubatch": (1, 1_000_000),
            "cache_ram": (0, 1_000_000),
            "ctxcp": (0, 128),
            "image_max_tokens": (0, 100_000),
            "port": (1, 65_535),
            "startup_timeout_seconds": (1, 86_400),
        }
        clean: dict[str, Any] = {}
        for field_name, (minimum, maximum) in integer_fields.items():
            if field_name not in profile:
                continue
            value = profile[field_name]
            if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
                errors.append(f"profiles.{name}.{field_name} must be an integer between {minimum} and {maximum}")
            else:
                clean[field_name] = value

        if "mtp" in profile and not isinstance(profile["mtp"], bool):
            errors.append(f"profiles.{name}.mtp must be boolean")
        elif "mtp" in profile:
            clean["mtp"] = profile["mtp"]

        if "reasoning" in profile:
            if profile["reasoning"] not in {"off", "low", "medium", "high"}:
                errors.append(f"profiles.{name}.reasoning must be off, low, medium, or high")
            else:
                clean["reasoning"] = profile["reasoning"]

        if "sampling" in profile:
            sampling = profile["sampling"]
            if not isinstance(sampling, dict):
                errors.append(f"profiles.{name}.sampling must be an object")
            else:
                unknown_sampling = sorted(set(sampling) - SAMPLING_KEYS)
                if unknown_sampling:
                    errors.append(f"profiles.{name}.sampling has unknown keys: {', '.join(unknown_sampling)}")
                clean_sampling: dict[str, Any] = {}
                for sampling_key, (minimum, maximum) in SAMPLING_RANGES.items():
                    if sampling_key not in sampling:
                        continue
                    value = sampling[sampling_key]
                    is_integer = isinstance(value, int) and not isinstance(value, bool)
                    valid_number = isinstance(value, (int, float)) and not isinstance(value, bool)
                    expected_integer = sampling_key in {"top_k", "repeat_last_n"}
                    if not valid_number or (expected_integer and not is_integer) or not minimum <= value <= maximum:
                        errors.append(
                            f"profiles.{name}.sampling.{sampling_key} has an invalid value"
                        )
                    else:
                        clean_sampling[sampling_key] = value
                clean["sampling"] = clean_sampling

        normalized[str(name)] = clean

    if "default" in normalized and not any(v for k, v in normalized.items() if k != "default"):
        warnings.append("only the default profile is configured")
    return normalized, errors, warnings


def parse_manifest_file(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read manifest {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"manifest must contain a JSON object: {path}")
    return value


def validate_manifest(manifest: Any, model_dir: Path) -> ManifestResult:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(manifest, dict):
        return ManifestResult({}, ["manifest must be an object"], warnings)

    schema_version = manifest.get("schema_version")
    if isinstance(schema_version, bool) or schema_version != 1:
        errors.append("schema_version must be 1")

    model_id = manifest.get("id")
    if not is_safe_id(model_id):
        errors.append("id must match [a-z0-9][a-z0-9_.-] and cannot be empty")

    engine = manifest.get("engine")
    if engine not in ENGINES:
        errors.append(f"engine must be one of: {', '.join(sorted(ENGINES))}")

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        errors.append("artifacts must be an object")
        artifacts = {}
    else:
        unknown_artifacts = sorted(set(artifacts) - set(ARTIFACT_KINDS))
        if unknown_artifacts:
            errors.append(f"artifacts has unknown keys: {', '.join(unknown_artifacts)}")

    binary = artifacts.get("binary")
    model = artifacts.get("model")
    if binary is None:
        errors.append("artifacts.binary is required")
    if model is None:
        errors.append("artifacts.model is required")

    max_context = manifest.get("max_context", 32768)
    if isinstance(max_context, bool) or not isinstance(max_context, int) or not 1024 <= max_context <= 100_000_000:
        errors.append("max_context must be an integer between 1024 and 100000000")

    capabilities = manifest.get("capabilities", ["text"])
    if not isinstance(capabilities, list) or not capabilities or any(not isinstance(x, str) for x in capabilities):
        errors.append("capabilities must be a non-empty string array")
    else:
        unknown_capabilities = sorted(set(capabilities) - CAPABILITIES)
        if unknown_capabilities:
            errors.append(f"capabilities has unknown values: {', '.join(unknown_capabilities)}")

    profiles, profile_errors, profile_warnings = _validate_profiles(manifest.get("profiles"))
    errors.extend(profile_errors)
    warnings.extend(profile_warnings)

    normalized = {
        **manifest,
        "artifacts": artifacts,
        "capabilities": capabilities,
        "max_context": max_context,
        "profiles": profiles,
    }
    return ManifestResult(normalized, errors, warnings)


def infer_manifest(model_dir: Path) -> dict[str, Any]:
    """Create a conservative draft manifest without modifying the model directory."""

    def safe_relative(path: Path) -> str:
        return path.relative_to(model_dir).as_posix()

    binaries = sorted(
        item for item in (model_dir / "bin").glob("llama-*")
        if item.is_file()
    ) if (model_dir / "bin").is_dir() else []
    binary_preferences = [
        "llama-server",
        "llama-tts",
        "llama-mtmd-cli",
        "llama-cli",
    ]
    binary = next((item for name in binary_preferences for item in binaries if item.name == name), None)
    if binary is None and binaries:
        binary = binaries[0]
    if binary is None:
        binary = model_dir / "bin" / "llama-server"

    ggufs = [
        item for item in model_dir.rglob("*.gguf")
        if item.is_file()
        and ".staging" not in item.parts
        and not item.name.lower().startswith("mmproj-")
    ]
    model_file = max(ggufs, key=lambda item: item.stat().st_size, default=None)
    mmprojs = [
        item for item in model_dir.rglob("*.gguf")
        if item.is_file() and "mmproj" in item.name.lower()
    ]
    mmproj = mmprojs[0] if mmprojs else None
    templates = [item for item in model_dir.rglob("chat_template.jinja") if item.is_file()]
    template = templates[0] if templates else None
    kernels = model_dir / "kernels"

    folder_id = slugify(model_dir.name.removesuffix("-AX8860-llama.cpp"))
    artifacts: dict[str, str] = {"binary": safe_relative(binary)}
    if model_file:
        artifacts["model"] = safe_relative(model_file)
    if mmproj:
        artifacts["mmproj"] = safe_relative(mmproj)
    if template:
        artifacts["chat_template"] = safe_relative(template)
    if kernels.is_dir():
        artifacts["kernels"] = safe_relative(kernels)

    return {
        "schema_version": 1,
        "id": folder_id,
        "name": model_dir.name.removesuffix("-AX8860-llama.cpp"),
        "version": "",
        "engine": binary.name if binary.name in ENGINES else "llama-server",
        "binary": safe_relative(binary),
        "working_directory": ".",
        "artifacts": artifacts,
        "capabilities": (
            ["tts"] if binary.name == "llama-tts"
            else ["audio"] if "asr" in model_dir.name.lower()
            else ["text", "image"] if mmproj
            else ["text"]
        ),
        "max_context": 32768,
        "estimated_cmm_mb": None,
        "profiles": {
            "default": {
                "context": 32768,
                "batch": 4096,
                "ubatch": 4096,
                "cache_ram": 0,
                "ctxcp": 0,
                "mtp": False,
                "reasoning": "off",
            }
        },
    }
