from __future__ import annotations

import gzip
import importlib.util
import os
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "rotate_logs.py"


def load_module():
    spec = importlib.util.spec_from_file_location("ax_rotate_logs", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_rotation_copies_truncates_and_compresses(tmp_path: Path) -> None:
    module = load_module()
    active = tmp_path / "job.log"
    active.write_text("abcdefghij", encoding="utf-8")

    created = module.rotate_directory(tmp_path, max_bytes=10, keep=2)

    assert active.read_text(encoding="utf-8") == ""
    assert len(created) == 1
    with gzip.open(created[0], "rt", encoding="utf-8") as handle:
        assert handle.read() == "abcdefghij"


def test_rotation_keeps_only_newest_archives(tmp_path: Path) -> None:
    module = load_module()
    archives = []
    for index in range(3):
        path = tmp_path / f"job.log.{index}.gz"
        path.write_bytes(b"old")
        stamp = 1_000_000 + index
        os.utime(path, (stamp, stamp))
        archives.append(path)
    (tmp_path / "job.log").write_text("small", encoding="utf-8")

    module.rotate_directory(tmp_path, max_bytes=100, keep=2)

    assert archives[0].exists() is False
    assert archives[1].exists() is True
    assert archives[2].exists() is True
