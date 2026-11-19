from typing import Any

from fastapi.testclient import TestClient

import ax_model_manager.app as app_module
from ax_model_manager.app import create_app
from ax_model_manager.config import Settings


def resource_payload(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "available": True,
        "sampled_at": 1_000,
        "agent": {"version": "test"},
        "load": {"1": 0.5, "5": 0.4, "15": 0.3},
        "cpu": {"cores": 16, "frequency_mhz": [1600]},
        "cmm": {"total_kb": 1000, "used_kb": 200},
        "os_memory": {"total_kb": 1000, "avail_kb": 500, "used_kb": 500},
        "npu": {"clock_mhz": [800]},
        "npu_max_temp_c": 40.0,
        "thermal_zones": [],
        "disks": [],
        "net": {"rx_mbps": 1.0, "tx_mbps": 2.0},
        "llama": {"rss_kb": 300, "process_count": 1},
    }
    values.update(overrides)
    return values


def test_resources_api_reports_cpu_percent_and_maxima(
    model_root, tmp_path, monkeypatch
) -> None:
    samples = [resource_payload(), resource_payload(
        cmm={"total_kb": 1000, "used_kb": 300},
        os_memory={"total_kb": 1000, "avail_kb": 300, "used_kb": 700},
        net={"rx_mbps": 3.0, "tx_mbps": 4.0},
        npu_max_temp_c=45.0,
        llama={"rss_kb": 500, "process_count": 1},
    )]
    with TestClient(create_app(Settings(
        model_root=model_root,
        data_dir=tmp_path / "data",
    ))) as client:
        client.app.state.read_board_resources = lambda: samples.pop(0)
        monkeypatch.setattr(app_module, "read_cpu_times", lambda: (150, 300))
        client.app.state.resource_state["last_cpu_times"] = (100, 200)

        first = client.get("/api/v1/resources").json()
        assert first["current"]["cpu_percent"] == 50.0
        assert first["current"]["cpu"]["percent"] == 50.0
        assert first["max"] == {
            "cpu_percent": 50.0,
            "cpu_load_1m": 0.5,
            "os_used_kb": 500.0,
            "cmm_used_kb": 200.0,
            "rx_mbps": 1.0,
            "tx_mbps": 2.0,
            "npu_temp_c": 40.0,
            "llama_rss_kb": 300.0,
        }
        assert first["sample_count"] == 1

        second = client.get("/api/v1/resources").json()
        assert second["current"]["cmm"]["used_kb"] == 300
        assert second["max"]["cmm_used_kb"] == 300.0
        assert second["max"]["os_used_kb"] == 700.0
        assert second["max"]["rx_mbps"] == 3.0
        assert second["max"]["tx_mbps"] == 4.0
        assert second["max"]["npu_temp_c"] == 45.0
        assert second["max"]["llama_rss_kb"] == 500.0
        assert second["sample_count"] == 2


def test_resource_max_reset_clears_maxima_without_changing_current(
    model_root, tmp_path, monkeypatch
) -> None:
    current = resource_payload()
    with TestClient(create_app(Settings(
        model_root=model_root,
        data_dir=tmp_path / "data",
    ))) as client:
        client.app.state.read_board_resources = lambda: current
        monkeypatch.setattr(app_module, "read_cpu_times", lambda: None)
        before = client.get("/api/v1/resources").json()
        assert before["max"]["cmm_used_kb"] == 200.0

        reset = client.post("/api/v1/resources/max/reset").json()
        assert reset["cleared_max"]["cmm_used_kb"] == 200.0
        assert reset["max"]["cmm_used_kb"] == 200.0
        assert reset["current"]["cmm"]["used_kb"] == 200
        assert reset["sample_count"] == 1
        assert reset["max_reset_at"] is not None


def test_dashboard_includes_resource_monitor_payload(
    model_root, tmp_path, monkeypatch
) -> None:
    with TestClient(create_app(Settings(
        model_root=model_root,
        data_dir=tmp_path / "data",
    ))) as client:
        client.app.state.read_board_resources = lambda: resource_payload()
        monkeypatch.setattr(app_module, "read_cpu_times", lambda: None)
        payload = client.get("/api/v1/dashboard").json()
        assert payload["resources"]["available"] is True
        assert payload["resource_monitor"]["max"]["cmm_used_kb"] == 200.0
