from pathlib import Path

from fastapi.testclient import TestClient

from ax_model_manager.app import create_app
from ax_model_manager.config import Settings


def make_client(model_root: Path, token: str = "") -> TestClient:
    return TestClient(create_app(Settings(
        model_root=model_root,
        auth_token=token,
        allowed_origins=("http://localhost:5173",) if token else (),
    )))


def test_health_is_public(model_root: Path) -> None:
    with make_client(model_root, token="secret") as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {
            "status": "ok",
            "version": "0.1.0",
            "mode": "read-only",
        }


def test_read_endpoints_require_token_when_configured(model_root: Path) -> None:
    with make_client(model_root, token="secret") as client:
        assert client.get("/api/v1/models").status_code == 401
        assert client.get("/api/v1/models/fixture-model").status_code == 401

        response = client.get("/api/v1/models", headers={"Authorization": "Bearer secret"})
        assert response.status_code == 200
        assert response.json()["count"] == 1


def test_models_endpoint_returns_summaries(model_root: Path) -> None:
    with make_client(model_root) as client:
        payload = client.get("/api/v1/models").json()
        assert payload["count"] == 1
        assert payload["valid_count"] == 1
        assert "manifest" not in payload["models"][0]
        assert "artifacts" not in payload["models"][0]


def test_model_detail_manifest_and_validate(model_root: Path) -> None:
    with make_client(model_root) as client:
        detail = client.get("/api/v1/models/fixture-model")
        assert detail.status_code == 200
        assert detail.json()["artifacts"][0]["key"] == "binary"

        manifest = client.get("/api/v1/models/fixture-model/manifest")
        assert manifest.status_code == 200
        assert manifest.json()["source"] == "package"

        validation = client.get("/api/v1/models/fixture-model/validate")
        assert validation.status_code == 200
        assert validation.json()["ready_to_start"] is True


def test_missing_model_returns_404(model_root: Path) -> None:
    with make_client(model_root) as client:
        assert client.get("/api/v1/models/missing").status_code == 404


def test_invalid_model_root_returns_503(tmp_path: Path) -> None:
    with make_client(tmp_path / "missing") as client:
        response = client.get("/api/v1/models")
        assert response.status_code == 503


def test_config_does_not_expose_token(model_root: Path) -> None:
    with make_client(model_root, token="secret") as client:
        response = client.get("/api/v1/config", headers={"Authorization": "Bearer secret"})
        assert response.status_code == 200
        assert response.json()["authentication"] is True
        assert "secret" not in response.text
