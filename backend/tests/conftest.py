from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest


def write_model_package(
    directory: Path,
    *,
    manifest: dict | None = None,
    executable: bool = True,
) -> Path:
    binary = directory / "bin" / "llama-server"
    binary.parent.mkdir(parents=True, exist_ok=True)
    fake_server = Path(__file__).resolve().parents[2] / "scripts" / "fake_llama_server.py"
    shutil.copyfile(fake_server, binary)
    os.chmod(binary, 0o755 if executable else 0o644)

    model = directory / "fixture.gguf"
    model.write_bytes(b"0" * 128)
    mmproj = directory / "mmproj-fixture.gguf"
    mmproj.write_bytes(b"0" * 64)
    template = directory / "chat_template.jinja"
    template.write_text("{{ messages }}", encoding="utf-8")
    kernels = directory / "kernels"
    kernels.mkdir()
    (kernels / "kernel.bin").write_bytes(b"0" * 16)

    if manifest is None:
        manifest = {
            "schema_version": 1,
            "id": "fixture-model",
            "name": "Fixture Model",
            "version": "1",
            "engine": "llama-server",
            "artifacts": {
                "binary": "bin/llama-server",
                "model": "fixture.gguf",
                "mmproj": "mmproj-fixture.gguf",
                "chat_template": "chat_template.jinja",
                "kernels": "kernels",
            },
            "capabilities": ["text", "image"],
            "max_context": 32768,
            "estimated_cmm_mb": 4096,
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
    if manifest is not False:
        (directory / "model.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return directory


@pytest.fixture
def model_root(tmp_path: Path) -> Path:
    root = tmp_path / "models"
    root.mkdir()
    write_model_package(root / "fixture-model-AX8860-llama.cpp")
    return root
