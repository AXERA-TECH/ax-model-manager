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
        assert payload["resources"]["thermal_zones"] == []
        assert payload["resources"]["disks"] == []
        assert payload["resources"]["llama"] is None
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
        log_path.write_text("one\nwarning line\nfailed error\nthree\n", encoding="utf-8")
        app.state.jobs.update(job, log_path=str(log_path))

        response = client.get(f"/api/v1/jobs/{job.id}/logs?lines=2")
        assert response.status_code == 200
        payload = response.json()
        assert payload["lines"] == ["failed error", "three"]
        assert payload["truncated"] is False

        filtered = client.get(f"/api/v1/jobs/{job.id}/logs?lines=20&search=error").json()
        assert filtered["lines"] == ["failed error"]
        errors_only = client.get(
            f"/api/v1/jobs/{job.id}/logs?lines=20&errors_only=true"
        ).json()
        assert errors_only["lines"] == ["failed error"]
        text = client.get(
            f"/api/v1/jobs/{job.id}/logs?lines=20&output_format=text"
        )
        assert text.headers["content-type"].startswith("text/plain")
        assert "failed error" in text.text


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
        assert 'id="device-details"' in index.text
        assert 'id="metric-os-memory"' not in index.text
        assert 'id="metric-cmm"' not in index.text
        assert 'id="device-grid"' in index.text
        assert 'id="device-status"' in index.text
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
        assert '<option value="4096">4K</option>' in index.text
        assert '<option value="8192">8K</option>' in index.text
        assert 'id="launch-task-message"' in index.text
        assert '<option value="16384">16K</option>' in index.text
        assert '<option value="262144">256K</option>' in index.text
        assert 'class="panel audit-panel"' in index.text
        assert "max-height: 14rem" in (
            Path(__file__).resolve().parents[1] / "ax_model_manager/static/styles.css"
        ).read_text(encoding="utf-8")
        assert 'id="auto-log"' in index.text
        assert 'id="follow-log"' in index.text
        assert client.get("/ui/app.js").status_code == 200
        assert client.get("/ui/../config.py").status_code == 404


def test_asr_preview_uses_minimal_recommended_server_arguments(
    model_root: Path,
    tmp_path: Path,
) -> None:
    from conftest import write_model_package

    package = write_model_package(model_root / "qwen3-asr", manifest={
        "schema_version": 1,
        "id": "qwen3-asr",
        "engine": "llama-server",
        "artifacts": {
            "binary": "bin/llama-server",
            "model": "fixture.gguf",
            "mmproj": "mmproj-fixture.gguf",
            "kernels": "kernels",
        },
        "capabilities": ["audio"],
        "runtime_type": "service",
        "max_context": 4096,
        "profiles": {"default": {"context": 4096}},
    })

    with make_client(model_root, tmp_path) as client:
        response = client.post("/api/v1/models/qwen3-asr/preview", json={})

        assert response.status_code == 200
        argv = response.json()["argv"]
        assert argv == [
            str(package / "bin/llama-server"),
            "-m", str(package / "fixture.gguf"),
            "-ngl", "99",
            "-np", "1",
            "-c", "4096",
            "--mmproj", str(package / "mmproj-fixture.gguf"),
            "--host", "0.0.0.0",
            "--port", "18080",
        ]
        assert "--jinja" not in argv
        assert "--reasoning" not in argv
        assert "--spec-type" not in argv


def test_llama_preview_can_enable_prometheus_metrics_endpoint(
    model_root: Path,
    tmp_path: Path,
) -> None:
    from conftest import write_model_package

    write_model_package(model_root / "metrics-model", manifest={
        "schema_version": 1,
        "id": "metrics-model",
        "engine": "llama-server",
        "artifacts": {
            "binary": "bin/llama-server",
            "model": "fixture.gguf",
        },
        "profiles": {"default": {"context": 4096, "metrics": True}},
    })

    with make_client(model_root, tmp_path) as client:
        response = client.post("/api/v1/models/metrics-model/preview", json={})

        assert response.status_code == 200
        assert "--metrics" in response.json()["argv"]

        index = client.get("/")
        assert 'id="metrics"' in index.text


def test_voxcpm2_preview_uses_acoustic_model_and_malloc_environment(
    model_root: Path,
    tmp_path: Path,
) -> None:
    from conftest import write_model_package

    package = write_model_package(model_root / "voxcpm2", manifest={
        "schema_version": 1,
        "id": "voxcpm2",
        "engine": "llama-server",
        "artifacts": {
            "binary": "bin/llama-server",
            "model": "fixture.gguf",
            "acoustic": "acoustic.gguf",
            "kernels": "kernels",
        },
        "capabilities": ["tts"],
        "runtime_type": "service",
        "max_context": 8192,
        "profiles": {
            "default": {
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
            },
        },
    })
    (package / "acoustic.gguf").write_bytes(b"0" * 128)

    with make_client(model_root, tmp_path) as client:
        response = client.post("/api/v1/models/voxcpm2/preview", json={})

        assert response.status_code == 200
        payload = response.json()
        argv = payload["argv"]
        assert "--voxcpm2-acoustic" in argv
        assert str(package / "acoustic.gguf") in argv
        assert "-b" in argv and "1024" in argv
        assert "-t" in argv and "8" in argv
        assert "--threads-http" in argv and "1" in argv
        assert "--jinja" not in argv
        assert payload["env"]["MALLOC_ARENA_MAX"] == "2"
        assert payload["env"]["MALLOC_TRIM_THRESHOLD_"] == "131072"
