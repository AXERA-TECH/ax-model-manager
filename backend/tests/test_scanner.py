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
