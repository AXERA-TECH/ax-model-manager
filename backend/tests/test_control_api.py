import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from ax_model_manager.app import create_app
from ax_model_manager.config import Settings
from conftest import write_model_package


def control_settings(model_root: Path, tmp_path: Path) -> Settings:
    return Settings(
        model_root=model_root,
        data_dir=tmp_path / "manager-data",
        read_only=False,
        enable_control=True,
        process_backend="fake-subprocess",
        max_running_models=1,
    )


def wait_for_state(client: TestClient, job_id: str, state: str, timeout: float = 3.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 200
        job = response.json()
        if job["state"] == state:
            return job
        if job["state"] == "failed":
            raise AssertionError(job)
        time.sleep(0.02)
    raise AssertionError(f"timeout waiting for {state}: {job}")


def test_control_endpoints_are_disabled_by_default(model_root: Path) -> None:
    with TestClient(create_app(Settings(model_root=model_root))) as client:
        assert client.post("/api/v1/models/fixture-model/start", json={}).status_code == 404
        assert client.post("/api/v1/models/fixture-model/stop", json={}).status_code == 404


def test_confirm_draft_manifest_without_touching_model_directory(model_root: Path, tmp_path: Path) -> None:
    package = model_root / "draft-model"
    from conftest import write_model_package
    write_model_package(package, manifest=False)

    with TestClient(create_app(control_settings(model_root, tmp_path))) as client:
        before = client.get("/api/v1/models/draft-model").json()
        assert before["manifest_source"] == "draft"
        assert before["ready_to_start"] is False

        confirmed = client.post("/api/v1/models/draft-model/manifest/confirm")
        assert confirmed.status_code == 200
        assert confirmed.json()["ready_to_start"] is True
        assert not (package / "model.json").exists()


def test_start_and_stop_fake_model(model_root: Path, tmp_path: Path) -> None:
    with TestClient(create_app(control_settings(model_root, tmp_path))) as client:
        started = client.post(
            "/api/v1/models/fixture-model/start",
            json={"profile": "default"},
        )
        assert started.status_code == 200
        job_id = started.json()["job"]["job_id"]
        job = wait_for_state(client, job_id, "running")
        assert job["port"]
        assert job["runtime_endpoint"]

        runtime = client.get(job["runtime_endpoint"] + "/health")
        assert runtime.json()["status"] == "ok"

        stopped = client.post(
            "/api/v1/models/fixture-model/stop",
            json={"timeout_seconds": 2},
        )
        assert stopped.status_code == 200
        stop_job_id = stopped.json()["job"]["job_id"]
        wait_for_state(client, stop_job_id, "stopped")
        wait_for_state(client, job_id, "stopped")

        audit_path = tmp_path / "manager-data/audit.jsonl"
        audit = [json.loads(line) for line in audit_path.read_text().splitlines()]
        assert any(item["action"] == "start_requested" for item in audit)
        assert any(item["action"] == "stop_completed" for item in audit)


def test_second_model_is_rejected_while_limit_is_reached(model_root: Path, tmp_path: Path) -> None:
    second = write_model_package(model_root / "second-model")
    manifest_path = second / "model.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["id"] = "second-model"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with TestClient(create_app(control_settings(model_root, tmp_path))) as client:
        first = client.post("/api/v1/models/fixture-model/start", json={})
        job_id = first.json()["job"]["job_id"]
        wait_for_state(client, job_id, "running")

        second = client.post("/api/v1/models/second-model/start", json={})
        assert second.status_code == 409
        assert second.json()["detail"]["message"] == "model limit reached"

        client.post("/api/v1/models/fixture-model/stop", json={})
        wait_for_state(client, job_id, "stopped")


def test_stop_is_blocked_while_slot_is_processing_and_force_stops(model_root: Path, tmp_path: Path) -> None:
    with TestClient(create_app(control_settings(model_root, tmp_path))) as client:
        started = client.post(
            "/api/v1/models/fixture-model/start",
            json={"profile": "default", "debug_busy": True},
        )
        job_id = started.json()["job"]["job_id"]
        wait_for_state(client, job_id, "running")

        blocked = client.post("/api/v1/models/fixture-model/stop", json={})
        assert blocked.status_code == 409
        assert blocked.json()["detail"]["error"] == "model_in_use"

        forced = client.post(
            "/api/v1/models/fixture-model/stop",
            json={"force": True, "timeout_seconds": 2},
        )
        assert forced.status_code == 200
        stop_job_id = forced.json()["job"]["job_id"]
        wait_for_state(client, stop_job_id, "stopped")
        wait_for_state(client, job_id, "stopped")


def test_switch_stops_current_and_starts_target(model_root: Path, tmp_path: Path) -> None:
    second = write_model_package(model_root / "second-model")
    manifest_path = second / "model.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["id"] = "second-model"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with TestClient(create_app(control_settings(model_root, tmp_path))) as client:
        first = client.post("/api/v1/models/fixture-model/start", json={}).json()["job"]
        wait_for_state(client, first["job_id"], "running")

        switched = client.post(
            "/api/v1/models/second-model/switch-to",
            json={"context": 4096, "port": 18080, "stop_timeout_seconds": 3},
        )
        assert switched.status_code == 200
        switch_job = switched.json()["job"]
        wait_for_state(client, switch_job["job_id"], "running", timeout=10.0)
        wait_for_state(client, first["job_id"], "stopped")

        second_jobs = [
            job for job in client.get("/api/v1/jobs").json()["jobs"]
            if job["model"] == "second-model" and job["action"] == "start"
        ]
        assert second_jobs
        assert second_jobs[0]["state"] == "running"

        client.post("/api/v1/models/second-model/stop", json={"timeout_seconds": 2})
        wait_for_state(client, second_jobs[0]["job_id"], "stopped")
        wait_for_state(client, switch_job["job_id"], "stopped")
