from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .manifest import (
    ManifestResult,
    check_artifact,
    infer_manifest,
    parse_manifest_file,
    validate_manifest,
)


def _looks_like_model(directory: Path) -> bool:
    """Ignore report/work directories that contain neither binaries nor model weights."""

    if (directory / "model.json").is_file():
        return True
    bin_dir = directory / "bin"
    if bin_dir.is_dir() and any(item.is_file() for item in bin_dir.glob("llama-*")):
        return True
    return any(
        item.is_file() and not item.name.lower().startswith("mmproj-")
        for item in directory.rglob("*.gguf")
    )


class ModelRootError(RuntimeError):
    pass


def _directory_stats(model_dir: Path) -> tuple[int, int]:
    total = 0
    count = 0
    root = model_dir.resolve(strict=False)
    for current, directories, files in os.walk(model_dir, followlinks=False):
        current_path = Path(current)
        # Do not descend into hidden/staging directories or symlinked directories.
        directories[:] = sorted(
            item for item in directories
            if not item.startswith(".") and item != ".staging"
            and not (current_path / item).is_symlink()
        )
        for filename in sorted(files):
            path = current_path / filename
            if filename.startswith("."):
                continue
            try:
                resolved = path.resolve(strict=False)
                if resolved != root and root not in resolved.parents:
                    continue
                total += resolved.stat().st_size
                count += 1
            except OSError:
                continue
    return total, count


def _artifact_reports(model_dir: Path, artifacts: dict[str, Any]) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for key in ("binary", "model", "mmproj", "acoustic", "chat_template", "kernels"):
        reports.append(check_artifact(
            model_dir,
            key,
            artifacts.get(key),
            required=key in {"binary", "model"},
        ))
    return reports


def inspect_model_directory(
    model_root: Path,
    directory: Path,
    override_manifest: Path | None = None,
) -> dict[str, Any]:
    root = model_root.expanduser().resolve(strict=False)
    model_dir = directory.expanduser().resolve(strict=False)
    if model_dir != root and root not in model_dir.parents:
        raise ModelRootError(f"model directory escapes model root: {directory}")

    raw_relative_dir = directory.name
    errors: list[str] = []
    warnings: list[str] = []
    source = "draft"
    manifest: dict[str, Any] = {}

    package_manifest = override_manifest or directory / "model.json"
    if package_manifest.is_file():
        try:
            manifest = parse_manifest_file(package_manifest)
            source = "user" if override_manifest else "package"
        except ValueError as exc:
            errors.append(str(exc))
            source = "invalid"
            manifest = infer_manifest(model_dir)
    else:
        manifest = infer_manifest(model_dir)
        warnings.append("model.json is missing; a draft manifest was inferred")

    result: ManifestResult = validate_manifest(manifest, model_dir)
    errors.extend(result.errors)
    warnings.extend(result.warnings)
    artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), dict) else {}
    artifact_reports = _artifact_reports(model_dir, artifacts)
    for report in artifact_reports:
        errors.extend(report["errors"])

    binary_report = next((item for item in artifact_reports if item["key"] == "binary"), None)
    if binary_report and binary_report["exists"] and not binary_report["executable"]:
        errors.append("artifact.binary is not executable")

    size_bytes, file_count = _directory_stats(model_dir)
    model_id = manifest.get("id") or raw_relative_dir
    valid = not errors
    runtime_type = str(manifest.get("runtime_type", "service"))
    ready_to_start = valid and source != "draft" and runtime_type != "task"

    return {
        "id": model_id,
        "name": manifest.get("name") or raw_relative_dir,
        "version": manifest.get("version") or "",
        "state": "discovered",
        "directory": str(directory),
        "relative_directory": raw_relative_dir,
        "manifest_source": source,
        "manifest_valid": result.valid,
        "manifest_review_required": source not in {"package", "user"},
        "ready_to_start": ready_to_start,
        "valid": valid,
        "errors": sorted(set(errors)),
        "warnings": sorted(set(warnings)),
        "capabilities": manifest.get("capabilities", ["text"]),
        "runtime_type": runtime_type,
        "max_context": manifest.get("max_context"),
        "estimated_cmm_mb": manifest.get("estimated_cmm_mb"),
        "profiles": list(manifest.get("profiles", {}).keys()),
        "size_bytes": size_bytes,
        "file_count": file_count,
        "artifacts": artifact_reports,
        "manifest": manifest,
    }


def scan_models(
    model_root: Path,
    overrides_dir: Path | None = None,
) -> list[dict[str, Any]]:
    root = model_root.expanduser()
    if not root.is_dir():
        raise ModelRootError(f"model root does not exist or is not a directory: {root}")

    resolved_root = root.resolve(strict=False)
    models: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if entry.name.startswith(".") or entry.name == ".staging":
            continue
        if not entry.is_dir() and not entry.is_symlink():
            continue
        try:
            resolved_entry = entry.resolve(strict=False)
            if resolved_entry != resolved_root and resolved_root not in resolved_entry.parents:
                record = {
                    "id": entry.name,
                    "name": entry.name,
                    "version": "",
                    "state": "invalid",
                    "directory": str(entry),
                    "relative_directory": entry.name,
                    "manifest_source": "invalid",
                    "manifest_valid": False,
                    "manifest_review_required": True,
                    "ready_to_start": False,
                    "valid": False,
                    "errors": ["model directory symlink escapes model root"],
                    "warnings": [],
                    "capabilities": [],
                    "runtime_type": "service",
                    "max_context": None,
                    "estimated_cmm_mb": None,
                    "profiles": [],
                    "size_bytes": 0,
                    "file_count": 0,
                    "artifacts": [],
                    "manifest": {},
                }
                models.append(record)
                continue
            if not entry.is_dir():
                continue
            if not _looks_like_model(entry):
                continue

            override_path = (
                overrides_dir / f"{entry.name}.json"
                if overrides_dir is not None
                else None
            )
            record = inspect_model_directory(
                root,
                entry,
                override_path if override_path and override_path.is_file() else None,
            )
            if record["id"] in seen_ids:
                record["valid"] = False
                record["ready_to_start"] = False
                record["errors"] = sorted(set(record["errors"] + [f"duplicate model id: {record['id']}"]))
            seen_ids.add(record["id"])
            models.append(record)
        except (OSError, ModelRootError) as exc:
            models.append({
                "id": entry.name,
                "name": entry.name,
                "version": "",
                "state": "invalid",
                "directory": str(entry),
                "relative_directory": entry.name,
                "manifest_source": "invalid",
                "manifest_valid": False,
                "manifest_review_required": True,
                "ready_to_start": False,
                "valid": False,
                "errors": [str(exc)],
                "warnings": [],
                "capabilities": [],
                "runtime_type": "service",
                "max_context": None,
                "estimated_cmm_mb": None,
                "profiles": [],
                "size_bytes": 0,
                "file_count": 0,
                "artifacts": [],
                "manifest": {},
            })
    return models


def summary(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key not in {"manifest", "artifacts"}}
