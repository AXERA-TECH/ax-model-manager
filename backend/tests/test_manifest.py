from pathlib import Path
import json

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


def test_infers_minicpm_ocr_recommended_profile(tmp_path: Path) -> None:
    directory = write_model_package(
        tmp_path / "MiniCPM-OCR-1.0-AX8860-llama.cpp",
        manifest=False,
    )
    (directory / "run.sh").write_text(
        "# minicpm-ocr\n./bin/llama-server -c 4096 -b 256 -ub 256\n",
        encoding="utf-8",
    )

    manifest = infer_manifest(directory)

    assert manifest["runtime_type"] == "service"
    assert manifest["max_context"] == 4096
    assert manifest["profiles"]["default"] == {
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


def test_infers_qwen_asr_minimal_server_profile(tmp_path: Path) -> None:
    directory = write_model_package(
        tmp_path / "Qwen3-ASR-1.7B-AX8860-llama.cpp",
        manifest=False,
    )
    (directory / "run.sh").write_text(
        "./bin/llama-server -m model.gguf --mmproj mmproj.gguf -c 4096 -np 1\n",
        encoding="utf-8",
    )

    manifest = infer_manifest(directory)

    assert manifest["capabilities"] == ["audio"]
    assert manifest["runtime_type"] == "service"
    assert manifest["max_context"] == 4096
    assert manifest["profiles"]["default"] == {"context": 4096}


def test_infers_qwen_tts_as_one_shot_task(tmp_path: Path) -> None:
    directory = write_model_package(
        tmp_path / "Qwen3-TTS-0.6B-AX8860-llama.cpp",
        manifest=False,
    )
    binary = directory / "bin" / "llama-tts"
    (directory / "bin/llama-server").unlink()
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o755)
    (directory / "run.sh").write_text("./bin/llama-tts -c 4096\n", encoding="utf-8")

    manifest = infer_manifest(directory)

    assert manifest["engine"] == "llama-tts"
    assert manifest["capabilities"] == ["tts"]
    assert manifest["runtime_type"] == "task"
    assert manifest["profiles"]["default"] == {
        "context": 4096,
        "sampling": {"temperature": 0.9, "top_k": 50},
    }


def test_infers_voxcpm2_server_profile_and_acoustic_artifact(tmp_path: Path) -> None:
    directory = write_model_package(
        tmp_path / "VoxCPM2-AX8860-llama.cpp",
        manifest=False,
    )
    (directory / "gguf").mkdir()
    (directory / "gguf/VoxCPM2-BaseLM-F16.gguf").write_bytes(b"0" * 512)
    (directory / "gguf/VoxCPM2-Acoustic-F16.gguf").write_bytes(b"0" * 256)
    (directory / "run.sh").write_text(
        "./bin/llama-server --voxcpm2-acoustic gguf/VoxCPM2-Acoustic-F16.gguf\n",
        encoding="utf-8",
    )

    manifest = infer_manifest(directory)

    assert manifest["runtime_type"] == "service"
    assert manifest["capabilities"] == ["tts"]
    assert manifest["max_context"] == 8192
    assert manifest["estimated_cmm_mb"] == 3072
    assert manifest["artifacts"]["model"] == "gguf/VoxCPM2-BaseLM-F16.gguf"
    assert manifest["artifacts"]["acoustic"] == "gguf/VoxCPM2-Acoustic-F16.gguf"
    assert manifest["profiles"]["default"] == {
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


def test_validate_manifest_accepts_model_defaults_and_metrics_profile(tmp_path: Path) -> None:
    directory = write_model_package(tmp_path / "fixture-model", manifest={
        "schema_version": 1,
        "id": "fixture-model",
        "engine": "llama-server",
        "artifacts": {
            "binary": "bin/llama-server",
            "model": "fixture.gguf",
        },
        "defaults": {
            "system_prompt": "You are a concise assistant.",
            "max_output_tokens": 4096,
            "kv_cache_quantization": "E4M3",
            "attention_quantization": "MXFP4",
            "speculative": {
                "type": "draft-mtp",
                "enabled": False,
                "draft_n_max": 3,
            },
        },
        "profiles": {"default": {"context": 4096, "metrics": True}},
    })

    result = validate_manifest(
        json.loads((directory / "model.json").read_text(encoding="utf-8")),
        directory,
    )

    assert result.valid
    assert result.manifest["defaults"]["max_output_tokens"] == 4096
    assert result.manifest["defaults"]["speculative"]["draft_n_max"] == 3
    assert result.manifest["profiles"]["default"]["metrics"] is True


def test_validate_manifest_rejects_invalid_defaults(tmp_path: Path) -> None:
    directory = write_model_package(tmp_path / "fixture-model", manifest={
        "schema_version": 1,
        "id": "fixture-model",
        "engine": "llama-server",
        "artifacts": {
            "binary": "bin/llama-server",
            "model": "fixture.gguf",
        },
        "defaults": {
            "max_output_tokens": True,
            "kv_cache_quantization": "bad value",
            "speculative": {"enabled": "yes", "unknown": 1},
            "unknown": "value",
        },
        "profiles": {"default": {"context": 4096, "metrics": "yes"}},
    })

    result = validate_manifest(
        json.loads((directory / "model.json").read_text(encoding="utf-8")),
        directory,
    )

    assert not result.valid
    assert "defaults.max_output_tokens must be an integer between 1 and 1000000" in result.errors
    assert "defaults.kv_cache_quantization must match [A-Za-z0-9_.:+-] and contain 1-64 characters" in result.errors
    assert "defaults.speculative.enabled must be boolean" in result.errors
    assert "defaults.speculative has unknown keys: unknown" in result.errors
    assert "defaults has unknown keys: unknown" in result.errors
    assert "profiles.default.metrics must be boolean" in result.errors
