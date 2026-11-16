#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import os
import shutil
import time
from pathlib import Path


def rotate_directory(
    directory: Path,
    *,
    max_bytes: int,
    keep: int,
) -> list[Path]:
    """Apply copytruncate-style rotation without requiring the logrotate binary.

    AX8860 board images do not always include logrotate. Model runtimes keep
    their file descriptors open, so rename-only rotation would continue writing
    to the old inode. Copy first, then truncate the active file.
    """

    directory.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    for active in sorted(directory.glob("*.log")):
        size = active.stat().st_size
        if size < max_bytes:
            continue

        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
        target = active.with_name(f"{active.name}.{stamp}-{time.time_ns()}.tmp")
        shutil.copyfile(active, target)
        try:
            with active.open("r+b") as handle:
                handle.truncate(0)
                handle.flush()
                os.fsync(handle.fileno())
            compressed = target.with_suffix(".gz")
            with target.open("rb") as source, gzip.open(compressed, "wb") as output:
                shutil.copyfileobj(source, output)
                output.flush()
                os.fsync(output.fileno())
            target.unlink()
            created.append(compressed)
        except Exception:
            target.unlink(missing_ok=True)
            compressed.unlink(missing_ok=True)  # type: ignore[possibly-undefined]
            raise

    if keep >= 0:
        archives = sorted(
            directory.glob("*.log.*.gz"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for archive in archives[keep:]:
            archive.unlink()
    return created


def main() -> int:
    parser = argparse.ArgumentParser(description="Rotate AXERA Model Manager job logs")
    parser.add_argument("directory", type=Path)
    parser.add_argument("--max-bytes", type=int, default=50 * 1024 * 1024)
    parser.add_argument("--keep", type=int, default=14)
    arguments = parser.parse_args()
    if arguments.max_bytes < 1:
        parser.error("--max-bytes must be positive")
    if arguments.keep < 0:
        parser.error("--keep cannot be negative")
    rotate_directory(
        arguments.directory,
        max_bytes=arguments.max_bytes,
        keep=arguments.keep,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
