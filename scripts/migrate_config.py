#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path

from ax_model_manager.config import Settings, load_settings


FIELDS = [field for field in asdict(Settings()) if field != "config_path"]


def migrate(path: Path, *, dry_run: bool = False) -> dict:
    settings = load_settings(path)
    normalized = {field: asdict(settings)[field] for field in FIELDS}
    for field in ("model_root", "data_dir"):
        normalized[field] = str(Path(normalized[field]).expanduser())
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=str(path.parent),
            text=True,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(normalized, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary_name, 0o600)
            os.replace(temporary_name, path)
        except Exception:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
            raise
    return normalized


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize an AXERA Model Manager config")
    parser.add_argument("config", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args()
    value = migrate(arguments.config, dry_run=arguments.dry_run)
    value["auth_token"] = "***" if value.get("auth_token") else ""
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
