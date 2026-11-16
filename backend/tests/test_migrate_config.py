from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts" / "migrate_config.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location("ax_migrate_config", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_adds_defaults_and_preserves_token(tmp_path: Path) -> None:
    module = load_module()
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "model_root": "/mnt/models",
                "auth_token": "board-secret",
                "enable_control": True,
                "process_backend": "systemd",
            }
        ),
        encoding="utf-8",
    )

    result = module.migrate(config)

    assert result["model_root"] == "/mnt/models"
    assert result["auth_token"] == "board-secret"
    assert result["enable_control"] is True
    assert result["process_backend"] == "systemd"
    assert result["manager_port"] == 18090
    assert config.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob(".config.json.*.tmp"))


def test_migration_rejects_unknown_keys(tmp_path: Path) -> None:
    module = load_module()
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"legacy-key": 1}), encoding="utf-8")

    with pytest.raises(ValueError, match="unknown config keys"):
        module.migrate(config)


def test_migration_dry_run_does_not_create_config(tmp_path: Path) -> None:
    module = load_module()
    config = tmp_path / "missing.json"

    result = module.migrate(config, dry_run=True)

    assert result["model_root"] == "/mnt/models"
    assert not config.exists()


def test_cli_masks_auth_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = load_module()
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"auth_token": "board-secret"}), encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["migrate_config.py", str(config), "--dry-run"])

    assert module.main() == 0

    rendered = capsys.readouterr().out
    assert "board-secret" not in rendered
    assert json.loads(rendered)["auth_token"] == "***"
