#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def write_file(path: Path, content: str, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if mode is not None:
        os.chmod(path, mode)


def package_manifest(model: str = "fixture.gguf", model_id: str = "fixture-model") -> dict:
    return {
        "schema_version": 1,
        "id": model_id,
        "name": "Fixture Model",
        "version": "1",
        "engine": "llama-server",
        "artifacts": {
            "binary": "bin/llama-server",
            "model": model,
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
            },
            "benchmark_greedy": {
                "context": 32768,
                "batch": 4096,
                "ubatch": 4096,
                "cache_ram": 0,
                "ctxcp": 0,
                "mtp": False,
                "reasoning": "off",
                "sampling": {
                    "temperature": 0,
                    "top_k": 0,
                    "top_p": 1.0,
                    "min_p": 0,
                    "repeat_last_n": 0,
                    "repeat_penalty": 1.0,
                    "presence_penalty": 0,
                    "frequency_penalty": 0,
                },
            },
        },
    }


def create_package(directory: Path, *, with_manifest: bool = True) -> None:
    write_file(directory / "bin/llama-server", "#!/bin/sh\n", 0o755)
    (directory / "fixture.gguf").write_bytes(b"0" * 4096)
    (directory / "mmproj-fixture.gguf").write_bytes(b"0" * 2048)
    write_file(directory / "chat_template.jinja", "{{ messages }}\n")
    (directory / "kernels").mkdir(exist_ok=True)
    (directory / "kernels/kernel.bin").write_bytes(b"0" * 128)
    if with_manifest:
        write_file(
            directory / "model.json",
            json.dumps(package_manifest(), ensure_ascii=False, indent=2) + "\n",
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Create local Model Manager fixture models")
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)

    create_package(root / "fixture-model-AX8860-llama.cpp")
    create_package(root / "draft-model-AX8860-llama.cpp", with_manifest=False)

    broken = root / "broken-model-AX8860-llama.cpp"
    create_package(broken)
    manifest = package_manifest("missing.gguf", "broken-model")
    write_file(broken / "model.json", json.dumps(manifest, indent=2) + "\n")

    print(f"fixtures created: {root}")


if __name__ == "__main__":
    main()
