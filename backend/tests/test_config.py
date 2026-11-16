from pathlib import Path

import json
import pytest

from ax_model_manager.config import Settings, load_settings


def test_defaults_use_expected_ports() -> None:
    settings = Settings()
    assert settings.manager_port == 18090
    assert (settings.llama_port_min, settings.llama_port_max) == (18080, 18089)
    assert settings.read_only is True


def test_manager_port_cannot_overlap_llama_range() -> None:
    with pytest.raises(ValueError, match="overlap"):
        Settings(manager_port=18080)


def test_llama_port_range_must_be_ordered() -> None:
    with pytest.raises(ValueError, match="cannot be greater"):
        Settings(llama_port_min=18089, llama_port_max=18080)


def test_load_settings_rejects_unknown_keys(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"unknown_key": 1}', encoding="utf-8")
    with pytest.raises(ValueError, match="unknown config keys"):
        load_settings(path)


def test_load_settings_reads_model_root(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"model_root": str(tmp_path)}), encoding="utf-8")
    assert load_settings(path).model_root == tmp_path
