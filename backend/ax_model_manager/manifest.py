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
    "acoustic": "file",
    "chat_template": "file",
    "kernels": "directory",
}
PROFILE_KEYS = {
    "context", "batch", "ubatch", "cache_ram", "ctxcp", "mtp", "reasoning",
    "image_max_tokens", "port", "startup_timeout_seconds", "sampling", "metrics",
    "threads", "http_threads", "env",
}
SUPPORTED_PROFILE_ENV = {
    "MALLOC_ARENA_MAX",
    "MALLOC_MMAP_THRESHOLD_",
    "MALLOC_TRIM_THRESHOLD_",
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
ENGINES = {"llama-server", "llama-tts", "llama-mtmd-cli", "llama-cli", "axdiffusion-server"}
LAUNCH_KEYS = {"argv", "health_path", "out_dir", "default_port"}
LAUNCH_PLACEHOLDERS = {"{port}", "{model_dir}", "{out_dir}"}
API_KEYS = {"type", "base_path", "endpoints", "web_ui", "notes"}
DEFAULT_KEYS = {
    "system_prompt",
    "max_output_tokens",
    "kv_cache_quantization",
    "attention_quantization",
    "speculative",
}
SPECULATIVE_KEYS = {"type", "enabled", "draft_n_max"}
QUANTIZATION_RE = re.compile(r"^[A-Za-z0-9_.:+-]{1,64}$")


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
            "threads": (1, 1024),
            "http_threads": (1, 1024),
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

        if "metrics" in profile and not isinstance(profile["metrics"], bool):
            errors.append(f"profiles.{name}.metrics must be boolean")
        elif "metrics" in profile:
            clean["metrics"] = profile["metrics"]

        if "reasoning" in profile:
            if profile["reasoning"] not in {"off", "low", "medium", "high"}:
                errors.append(f"profiles.{name}.reasoning must be off, low, medium, or high")
            else:
                clean["reasoning"] = profile["reasoning"]

        if "env" in profile:
            environment = profile["env"]
            if not isinstance(environment, dict):
                errors.append(f"profiles.{name}.env must be an object")
            else:
                unknown_environment = sorted(set(environment) - SUPPORTED_PROFILE_ENV)
                if unknown_environment:
                    errors.append(
                        f"profiles.{name}.env has unknown keys: {', '.join(unknown_environment)}"
                    )
                clean_environment: dict[str, str] = {}
                for key, value in environment.items():
                    if key not in SUPPORTED_PROFILE_ENV:
                        continue
                    if isinstance(value, bool) or not isinstance(value, (int, str)):
                        errors.append(f"profiles.{name}.env.{key} must be an integer or string")
                    else:
                        clean_environment[key] = str(value)
                clean["env"] = clean_environment

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


def _validate_defaults(defaults: Any) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    if defaults is None:
        return {}, errors
    if not isinstance(defaults, dict):
        return {}, ["defaults must be an object"]

    unknown = sorted(set(defaults) - DEFAULT_KEYS)
    if unknown:
        errors.append(f"defaults has unknown keys: {', '.join(unknown)}")

    clean: dict[str, Any] = {}
    system_prompt = defaults.get("system_prompt")
    if system_prompt is not None:
        if not isinstance(system_prompt, str) or len(system_prompt) > 16_384:
            errors.append("defaults.system_prompt must be a string of at most 16384 characters")
        else:
            clean["system_prompt"] = system_prompt

    max_output_tokens = defaults.get("max_output_tokens")
    if max_output_tokens is not None:
        if isinstance(max_output_tokens, bool) or not isinstance(max_output_tokens, int) or not 1 <= max_output_tokens <= 1_000_000:
            errors.append("defaults.max_output_tokens must be an integer between 1 and 1000000")
        else:
            clean["max_output_tokens"] = max_output_tokens

    for key in ("kv_cache_quantization", "attention_quantization"):
        value = defaults.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or not QUANTIZATION_RE.fullmatch(value):
            errors.append(f"defaults.{key} must match [A-Za-z0-9_.:+-] and contain 1-64 characters")
        else:
            clean[key] = value

    speculative = defaults.get("speculative")
    if speculative is not None:
        if not isinstance(speculative, dict):
            errors.append("defaults.speculative must be an object")
        else:
            unknown_speculative = sorted(set(speculative) - SPECULATIVE_KEYS)
            if unknown_speculative:
                errors.append(
                    f"defaults.speculative has unknown keys: {', '.join(unknown_speculative)}"
                )
            clean_speculative: dict[str, Any] = {}
            speculative_type = speculative.get("type")
            if speculative_type is not None:
                if not isinstance(speculative_type, str) or not QUANTIZATION_RE.fullmatch(speculative_type):
                    errors.append("defaults.speculative.type must contain 1-64 safe identifier characters")
                else:
                    clean_speculative["type"] = speculative_type
            enabled = speculative.get("enabled")
            if enabled is not None:
                if not isinstance(enabled, bool):
                    errors.append("defaults.speculative.enabled must be boolean")
                else:
                    clean_speculative["enabled"] = enabled
            draft_n_max = speculative.get("draft_n_max")
            if draft_n_max is not None:
                if isinstance(draft_n_max, bool) or not isinstance(draft_n_max, int) or not 1 <= draft_n_max <= 64:
                    errors.append("defaults.speculative.draft_n_max must be an integer between 1 and 64")
                else:
                    clean_speculative["draft_n_max"] = draft_n_max
            if clean_speculative:
                clean["speculative"] = clean_speculative
    return clean, errors


def _validate_launch(launch: Any) -> tuple[dict[str, Any], list[str]]:
    """Validate a non-llama launch template (e.g. axdiffusion-server).

    The template is applied verbatim except for a small set of placeholders
    that the manager substitutes at start time. No shell is involved.
    """

    errors: list[str] = []
    if launch is None:
        return {}, errors
    if not isinstance(launch, dict):
        return {}, ["launch must be an object"]
    if not launch:
        return {}, errors

    unknown = sorted(set(launch) - LAUNCH_KEYS)
    if unknown:
        errors.append(f"launch has unknown keys: {', '.join(unknown)}")

    argv = launch.get("argv")
    if not isinstance(argv, list) or not argv:
        errors.append("launch.argv must be a non-empty string array")
        argv = []
    elif len(argv) > 128:
        errors.append("launch.argv has too many entries (max 128)")
    else:
        for index, item in enumerate(argv):
            if not isinstance(item, str) or not item or len(item) > 4096 or "\x00" in item:
                errors.append(f"launch.argv[{index}] must be a non-empty string")
        if not any("{port}" in item for item in argv if isinstance(item, str)):
            errors.append("launch.argv must reference {port}")

    health_path = launch.get("health_path", "/health")
    if not isinstance(health_path, str) or not health_path.startswith("/") or any(ch.isspace() for ch in health_path):
        errors.append("launch.health_path must be an absolute URL path")
    else:
        health_path = health_path

    out_dir = launch.get("out_dir")
    if out_dir is not None and (not isinstance(out_dir, str) or not out_dir or out_dir.startswith("/") or ".." in Path(out_dir).parts):
        errors.append("launch.out_dir must be a relative path inside the model directory")
        out_dir = None
    default_port = launch.get("default_port")
    if default_port is not None and (
        isinstance(default_port, bool) or not isinstance(default_port, int) or not 1024 <= default_port <= 65535
    ):
        errors.append("launch.default_port must be an integer between 1024 and 65535")
        default_port = None

    clean: dict[str, Any] = {}
    if isinstance(argv, list) and argv and not any(
        not isinstance(item, str) or not item for item in argv
    ):
        clean["argv"] = list(argv)
    clean["health_path"] = health_path if isinstance(health_path, str) else "/health"
    if out_dir:
        clean["out_dir"] = out_dir
    if default_port is not None:
        clean["default_port"] = default_port
    return clean, errors


def _validate_api(api: Any) -> tuple[dict[str, Any], list[str]]:
    """Validate the public API description advertised by a model package."""

    errors: list[str] = []
    if api is None:
        return {}, errors
    if not isinstance(api, dict):
        return {}, ["api must be an object"]
    if not api:
        return {}, errors

    unknown = sorted(set(api) - API_KEYS)
    if unknown:
        errors.append(f"api has unknown keys: {', '.join(unknown)}")

    clean: dict[str, Any] = {}
    api_type = api.get("type")
    if not isinstance(api_type, str) or not QUANTIZATION_RE.fullmatch(api_type):
        errors.append("api.type must be a safe identifier such as openai-images")
    else:
        clean["type"] = api_type

    base_path = api.get("base_path", "/")
    if not isinstance(base_path, str) or not base_path.startswith("/") or any(ch.isspace() for ch in base_path):
        errors.append("api.base_path must be an absolute URL path")
    else:
        clean["base_path"] = base_path

    endpoints = api.get("endpoints")
    if endpoints is not None:
        if (
            not isinstance(endpoints, list)
            or not endpoints
            or any(
                not isinstance(item, str)
                or not item.startswith("/")
                or any(ch.isspace() for ch in item)
                for item in endpoints
            )
        ):
            errors.append("api.endpoints must be a non-empty array of absolute URL paths")
        else:
            clean["endpoints"] = list(endpoints)

    web_ui = api.get("web_ui")
    if web_ui is not None:
        if not isinstance(web_ui, str) or not web_ui.startswith("/") or any(ch.isspace() for ch in web_ui):
            errors.append("api.web_ui must be an absolute URL path")
        else:
            clean["web_ui"] = web_ui

    notes = api.get("notes")
    if notes is not None:
        if not isinstance(notes, str) or len(notes) > 4096:
            errors.append("api.notes must be a string of at most 4096 characters")
        else:
            clean["notes"] = notes
    return clean, errors


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

    runtime_type = manifest.get("runtime_type", "service")
    if runtime_type not in {"service", "task"}:
        errors.append("runtime_type must be service or task")

    profiles, profile_errors, profile_warnings = _validate_profiles(manifest.get("profiles"))
    errors.extend(profile_errors)
    warnings.extend(profile_warnings)
    defaults, default_errors = _validate_defaults(manifest.get("defaults"))
    errors.extend(default_errors)
    launch, launch_errors = _validate_launch(manifest.get("launch"))
    errors.extend(launch_errors)
    api, api_errors = _validate_api(manifest.get("api"))
    errors.extend(api_errors)

    if engine == "axdiffusion-server" and not launch:
        errors.append("axdiffusion-server requires a launch template")

    normalized = {
        **manifest,
        "artifacts": artifacts,
        "capabilities": capabilities,
        "runtime_type": runtime_type,
        "max_context": max_context,
        "defaults": defaults,
        "profiles": profiles,
        "launch": launch,
        "api": api,
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

    folder_name = model_dir.name.lower()
    run_script = model_dir / "run.sh"
    try:
        package_text = run_script.read_text(encoding="utf-8").lower()
    except OSError:
        package_text = ""

    capabilities = (
        ["tts"] if binary.name == "llama-tts"
        else ["audio"] if "asr" in folder_name
        else ["text", "image"] if mmproj
        else ["text"]
    )
    max_context = 32768
    estimated_cmm_mb: int | None = None
    runtime_type = "task" if binary.name == "llama-tts" else "service"
    default_profile: dict[str, Any] = {
        "context": 32768,
        "batch": 4096,
        "ubatch": 4096,
        "cache_ram": 0,
        "ctxcp": 0,
        "mtp": False,
        "reasoning": "off",
    }

    # OCR / ASR / TTS packages are intentionally treated as stable packages.
    # Their launch semantics differ from general LLM packages, so infer their
    # recommended server/task defaults from the package's own run.sh instead
    # of falling back to a generic 32K/4096 LLM profile.
    if "voxcpm2" in folder_name or "--voxcpm2-acoustic" in package_text:
        acoustic = next(
            (item for item in model_dir.rglob("*.gguf") if "acoustic" in item.name.lower()),
            None,
        )
        base_lm = next(
            (item for item in model_dir.rglob("*.gguf") if "baselm" in item.name.lower()),
            model_file,
        )
        if base_lm is not None:
            artifacts["model"] = safe_relative(base_lm)
        if acoustic is not None:
            artifacts["acoustic"] = safe_relative(acoustic)
        capabilities = ["tts"]
        max_context = 8192
        estimated_cmm_mb = 3072
        runtime_type = "service"
        default_profile = {
            "context": 8192,
            "batch": 1024,
            "threads": 8,
            "http_threads": 1,
            "startup_timeout_seconds": 180,
            "env": {
                "MALLOC_ARENA_MAX": "2",
                "MALLOC_MMAP_THRESHOLD_": "131072",
                "MALLOC_TRIM_THRESHOLD_": "131072",
            },
        }
    elif "minicpm-ocr" in folder_name or "minicpm-ocr" in package_text:
        capabilities = ["image", "text"]
        max_context = 4096
        default_profile = {
            "context": 4096,
            "batch": 256,
            "ubatch": 256,
            "cache_ram": 4096,
            "ctxcp": 8,
            "mtp": False,
            "reasoning": "off",
            "image_max_tokens": 4096,
            "sampling": {"temperature": 0.0, "top_k": 1},
        }
    elif "qwen3-asr" in folder_name or "qwen3-asr" in package_text:
        capabilities = ["audio"]
        max_context = 4096
        default_profile = {"context": 4096}
    elif "hy-mt2" in folder_name or "hy-mt2" in package_text:
        # Follow the model README's run.sh -s defaults: 16K context,
        # prompt cache in host RAM, and CTXCP=8. The generic LLM fallback
        # uses 32K/0/0, which does not match the package recommendation.
        capabilities = ["text"]
        max_context = 16384
        default_profile = {
            "context": 16384,
            "batch": 4096,
            "ubatch": 4096,
            "cache_ram": 4096,
            "ctxcp": 8,
            "mtp": False,
            "reasoning": "off",
        }
    elif binary.name == "llama-tts":
        capabilities = ["tts"]
        max_context = 4096
        runtime_type = "task"
        default_profile = {
            "context": 4096,
            "sampling": {"temperature": 0.9, "top_k": 50},
        }

    return {
        "schema_version": 1,
        "id": folder_id,
        "name": model_dir.name.removesuffix("-AX8860-llama.cpp"),
        "version": "",
        "engine": binary.name if binary.name in ENGINES else "llama-server",
        "binary": safe_relative(binary),
        "working_directory": ".",
        "artifacts": artifacts,
        "capabilities": capabilities,
        "runtime_type": runtime_type,
        "max_context": max_context,
        "estimated_cmm_mb": estimated_cmm_mb,
        "profiles": {"default": default_profile},
    }
