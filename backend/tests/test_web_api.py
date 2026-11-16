import json
from pathlib import Path

from fastapi.testclient import TestClient

from ax_model_manager.app import create_app
from ax_model_manager.config import Settings


def make_client(model_root: Path, tmp_path: Path) -> TestClient:
    return TestClient(create_app(Settings(
        model_root=model_root,
        data_dir=tmp_path / "data",
    )))


def test_dashboard_reports_models_and_config(model_root: Path, tmp_path: Path) -> None:
    with make_client(model_root, tmp_path) as client:
        response = client.get("/api/v1/dashboard")
        assert response.status_code == 200
        payload = response.json()
        assert payload["model_count"] == 1
        assert payload["valid_model_count"] == 1
        assert payload["active_job_count"] == 0
        assert payload["configured_model_limit"] == 1
        assert payload["resources"]["available"] is False
        assert payload["config"]["read_only"] is True


def test_model_rows_include_active_job(model_root: Path, tmp_path: Path) -> None:
    with make_client(model_root, tmp_path) as client:
        app = client.app
        job = app.state.jobs.create("start", "fixture-model", "running")
        app.state.jobs.update(job, port=18080)
        models = client.get("/api/v1/models").json()["models"]
        assert models[0]["state"] == "running"
        assert models[0]["active_job"]["job_id"] == job.id

        detail = client.get("/api/v1/models/fixture-model").json()
        assert detail["active_job"]["port"] == 18080


def test_job_log_endpoint_returns_tail(model_root: Path, tmp_path: Path) -> None:
    with make_client(model_root, tmp_path) as client:
        app = client.app
        job = app.state.jobs.create("start", "fixture-model", "stopped")
        log_path = tmp_path / "data/logs/test.log"
        log_path.parent.mkdir(parents=True)
        log_path.write_text("one\ntwo\nthree\n", encoding="utf-8")
        app.state.jobs.update(job, log_path=str(log_path))

        response = client.get(f"/api/v1/jobs/{job.id}/logs?lines=2")
        assert response.status_code == 200
        payload = response.json()
        assert payload["lines"] == ["two", "three"]
        assert payload["truncated"] is False


def test_audit_endpoint_returns_recent_records(model_root: Path, tmp_path: Path) -> None:
    with make_client(model_root, tmp_path) as client:
        audit = tmp_path / "data/audit.jsonl"
        audit.parent.mkdir(parents=True)
        records = [{"action": "start_requested", "model": "fixture"}, {"action": "stop_completed", "model": "fixture"}]
        audit.write_text("\n".join(json.dumps(item) for item in records), encoding="utf-8")
        payload = client.get("/api/v1/audit").json()["records"]
        assert [item["action"] for item in payload] == ["start_requested", "stop_completed"]


def test_static_admin_ui_is_served(model_root: Path, tmp_path: Path) -> None:
    with make_client(model_root, tmp_path) as client:
        index = client.get("/")
        assert index.status_code == 200
        assert "AXERA Model Manager" in index.text
        assert "OpenAI-compatible API" in index.text
        assert 'id="theme-toggle"' in index.text
        assert 'id="metric-os-memory"' in index.text
        assert 'id="max-running-models"' in index.text
        assert 'id="save-model-limit"' in index.text
        assert "height: var(--model-list-height)" in (
            Path(__file__).resolve().parents[1] / "ax_model_manager/static/styles.css"
        ).read_text(encoding="utf-8")
        assert ".side .panel { display:flex; flex-direction:column; min-height:0; margin-bottom:0; }" in (
            Path(__file__).resolve().parents[1] / "ax_model_manager/static/styles.css"
        ).read_text(encoding="utf-8")
        assert 'id="launch-dialog"' in index.text
        assert 'id="launch-force"' in index.text
        assert 'class="panel audit-panel"' in index.text
        assert "max-height: 14rem" in (
            Path(__file__).resolve().parents[1] / "ax_model_manager/static/styles.css"
        ).read_text(encoding="utf-8")
        assert 'id="auto-log"' in index.text
        assert 'id="follow-log"' in index.text
        assert client.get("/ui/app.js").status_code == 200
        assert client.get("/ui/../config.py").status_code == 404
