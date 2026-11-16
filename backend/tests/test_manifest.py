from pathlib import Path

from ax_model_manager.manifest import (
    check_artifact,
    infer_manifest,
    slugify,
    validate_manifest,
)

from conftest import write_model_package


def test_slugify_is_safe() -> None:
    assert slugify("Qwen3.6 35B A3B / AX8860") == "qwen3.6-35b-a3b-ax8860"


def test_artifact_rejects_traversal(tmp_path: Path) -> None:
    report = check_artifact(tmp_path, "model", "../outside.gguf", required=True)
    assert report["exists"] is False
    assert any("path traversal" in item for item in report["errors"])


def test_artifact_rejects_absolute_path(tmp_path: Path) -> None:
    report = check_artifact(tmp_path, "model", "/etc/passwd", required=True)
    assert any("absolute" in item for item in report["errors"])


def test_draft_manifest_infers_expected_artifacts(tmp_path: Path) -> None:
    directory = write_model_package(tmp_path / "fixture-model", manifest=False)
    manifest = infer_manifest(directory)
    assert manifest["id"] == "fixture-model"
    assert manifest["artifacts"]["binary"] == "bin/llama-server"
    assert manifest["artifacts"]["model"] == "fixture.gguf"
    assert manifest["artifacts"]["mmproj"] == "mmproj-fixture.gguf"
    assert "image" in manifest["capabilities"]


def test_validate_manifest_accepts_package_manifest(tmp_path: Path) -> None:
    directory = write_model_package(tmp_path / "fixture-model")
    manifest = directory / "model.json"
    import json
    result = validate_manifest(json.loads(manifest.read_text()), directory)
    assert result.valid


def test_validate_manifest_rejects_unknown_profile_fields(tmp_path: Path) -> None:
    directory = write_model_package(tmp_path / "fixture-model", manifest={
        "schema_version": 1,
        "id": "fixture-model",
        "engine": "llama-server",
        "artifacts": {
            "binary": "bin/llama-server",
            "model": "fixture.gguf",
        },
        "profiles": {"default": {"command": "rm -rf /"}},
    })
    import json
    result = validate_manifest(json.loads((directory / "model.json").read_text()), directory)
    assert not result.valid
    assert any("unknown keys" in item for item in result.errors)
