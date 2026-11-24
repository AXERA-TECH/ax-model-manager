import json
from pathlib import Path

from ax_model_manager.scanner import ModelRootError, scan_models

from conftest import write_model_package


def test_scan_finds_valid_package_and_draft_models(model_root: Path) -> None:
    write_model_package(model_root / "draft-model", manifest=False)
    records = scan_models(model_root)
    by_id = {item["id"]: item for item in records}

    assert by_id["fixture-model"]["valid"] is True
    assert by_id["fixture-model"]["manifest_source"] == "package"
    assert by_id["draft-model"]["manifest_source"] == "draft"
    assert by_id["draft-model"]["manifest_review_required"] is True
    assert by_id["draft-model"]["ready_to_start"] is False
    assert by_id["draft-model"]["size_bytes"] > 0


def test_scan_reports_missing_required_artifacts(model_root: Path) -> None:
    (model_root / "fixture-model-AX8860-llama.cpp" / "fixture.gguf").unlink()
    records = scan_models(model_root)
    record = records[0]
    assert record["valid"] is False
    assert record["ready_to_start"] is False
    assert any("artifact.model" in error for error in record["errors"])


def test_scan_reports_non_executable_binary(model_root: Path) -> None:
    package = model_root / "fixture-model-AX8860-llama.cpp"
    binary = package / "bin" / "llama-server"
    binary.chmod(0o644)
    record = scan_models(model_root)[0]
    assert record["valid"] is False
    assert "artifact.binary is not executable" in record["errors"]


def test_scan_finds_axdiffusion_package(model_root: Path) -> None:
    package = model_root / "MiniMax-H3-Image-AX8860"
    (package / "bin").mkdir(parents=True)
    binary = package / "bin" / "axdiffusion-server"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    (package / "axdiffusion.json").write_text("{}", encoding="utf-8")
    (package / "model.json").write_text(json.dumps({
        "schema_version": 1,
        "id": "minimax-h3-image",
        "engine": "axdiffusion-server",
        "artifacts": {
            "binary": "bin/axdiffusion-server",
            "model": "axdiffusion.json",
        },
        "capabilities": ["image_gen"],
        "runtime_type": "service",
        "max_context": 20480,
        "estimated_cmm_mb": 34816,
        "profiles": {"default": {"startup_timeout_seconds": 900}},
        "launch": {
            "argv": ["--model", ".", "--port", "{port}", "--out-dir", "output"],
            "health_path": "/health",
            "out_dir": "output",
        },
    }), encoding="utf-8")

    record = scan_models(model_root)[0]

    assert record["id"] == "minimax-h3-image"
    assert record["valid"] is True
    assert record["ready_to_start"] is True
    assert record["capabilities"] == ["image_gen"]
    assert record["manifest"]["launch"]["argv"][0] == "--model"


def test_scan_reports_traversal_manifest(model_root: Path) -> None:
    package = model_root / "evil-model"
    package.mkdir()
    binary = package / "bin" / "llama-server"
    binary.parent.mkdir()
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    (package / "real.gguf").write_bytes(b"0")
    (package / "model.json").write_text(json.dumps({
        "schema_version": 1,
        "id": "evil-model",
        "engine": "llama-server",
        "artifacts": {"binary": "bin/llama-server", "model": "../outside.gguf"},
        "profiles": {"default": {}},
    }), encoding="utf-8")
    record = scan_models(model_root)[0]
    assert record["valid"] is False
    assert any("path traversal" in error for error in record["errors"])


def test_scan_rejects_model_directory_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "models"
    root.mkdir()
    outside = tmp_path / "outside-model"
    outside.mkdir()
    (root / "escaped").symlink_to(outside, target_is_directory=True)
    record = scan_models(root)[0]
    assert record["valid"] is False
    assert "model directory symlink escapes model root" in record["errors"]


def test_scan_requires_existing_model_root(tmp_path: Path) -> None:
    try:
        scan_models(tmp_path / "missing")
    except ModelRootError as exc:
        assert "model root" in str(exc)
    else:
        raise AssertionError("ModelRootError not raised")


def test_scan_ignores_directories_without_model_artifacts(model_root: Path) -> None:
    reports = model_root / "benchmark-reports"
    reports.mkdir()
    (reports / "result.md").write_text("report", encoding="utf-8")
    records = scan_models(model_root)
    assert all(record["relative_directory"] != "benchmark-reports" for record in records)


def test_scan_infers_tts_engine(model_root: Path) -> None:
    tts = model_root / "Qwen3-TTS-0.6B-AX8860-llama.cpp"
    tts.mkdir()
    binary = tts / "bin" / "llama-tts"
    binary.parent.mkdir()
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    (tts / "qwen3-tts.gguf").write_bytes(b"0" * 128)
    (tts / "mmproj-qwen3-tts.gguf").write_bytes(b"0" * 64)
    record = next(item for item in scan_models(model_root) if item["id"] == "qwen3-tts-0.6b")
    assert record["manifest"]["engine"] == "llama-tts"
    assert record["manifest"]["artifacts"]["binary"] == "bin/llama-tts"
    assert "tts" in record["capabilities"]


def test_one_shot_task_manifest_is_not_service_startable(model_root: Path) -> None:
    package = model_root / "one-shot-task"
    write_model_package(package, manifest={
        "schema_version": 1,
        "id": "one-shot-task",
        "engine": "llama-tts",
        "artifacts": {
            "binary": "bin/llama-server",
            "model": "fixture.gguf",
        },
        "capabilities": ["tts"],
        "runtime_type": "task",
        "max_context": 4096,
        "profiles": {"default": {"context": 4096}},
    })

    record = next(item for item in scan_models(model_root) if item["id"] == "one-shot-task")

    assert record["valid"] is True
    assert record["runtime_type"] == "task"
    assert record["ready_to_start"] is False


def test_model_summary_exposes_model_defaults(model_root: Path) -> None:
    write_model_package(model_root / "defaults-model", manifest={
        "schema_version": 1,
        "id": "defaults-model",
        "engine": "llama-server",
        "artifacts": {
            "binary": "bin/llama-server",
            "model": "fixture.gguf",
        },
        "defaults": {
            "system_prompt": "Answer concisely.",
            "max_output_tokens": 2048,
            "kv_cache_quantization": "E4M3",
            "attention_quantization": "MXFP4",
            "speculative": {"type": "draft-mtp", "enabled": False, "draft_n_max": 3},
        },
        "profiles": {"default": {"context": 4096}},
    })

    record = next(item for item in scan_models(model_root) if item["id"] == "defaults-model")

    assert record["defaults"]["system_prompt"] == "Answer concisely."
    assert record["defaults"]["speculative"]["draft_n_max"] == 3


def test_user_override_manifest_makes_draft_ready(model_root: Path, tmp_path: Path) -> None:
    overrides = tmp_path / "overrides"
    overrides.mkdir()
    record = scan_models(model_root)[0]
    (overrides / "fixture-model-AX8860-llama.cpp.json").write_text(
        json.dumps(record["manifest"]),
        encoding="utf-8",
    )
    overridden = scan_models(model_root, overrides)[0]
    assert overridden["manifest_source"] == "user"
    assert overridden["manifest_review_required"] is False
    assert overridden["ready_to_start"] is True


def test_scan_rejects_artifact_symlink_escape(model_root: Path, tmp_path: Path) -> None:
    package = model_root / "fixture-model-AX8860-llama.cpp"
    outside = tmp_path / "outside.gguf"
    outside.write_bytes(b"outside")
    target = package / "linked.gguf"
    target.symlink_to(outside)
    manifest_path = package / "model.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"]["model"] = "linked.gguf"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    record = scan_models(model_root)[0]
    assert record["valid"] is False
    assert any("escapes model directory" in error for error in record["errors"])
