from __future__ import annotations

from hmac import compare_digest
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .config import Settings, load_settings
from .scanner import ModelRootError, scan_models, summary


def create_app(settings: Settings | None = None) -> FastAPI:
    selected = settings or load_settings()
    app = FastAPI(
        title="AXERA Model Manager",
        version=__version__,
        description="Read-only model discovery MVP. Control endpoints are intentionally not enabled yet.",
    )
    app.state.settings = selected

    if selected.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(selected.allowed_origins),
            allow_credentials=False,
            allow_methods=["GET"],
            allow_headers=["Authorization", "Content-Type"],
        )

    async def require_auth(request: Request) -> None:
        token = request.app.state.settings.auth_token
        if not token:
            return
        authorization = request.headers.get("Authorization", "")
        expected = f"Bearer {token}".encode("utf-8")
        if not compare_digest(authorization.encode("utf-8"), expected):
            raise HTTPException(status_code=401, detail="unauthorized")

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "mode": "read-only" if app.state.settings.read_only else "control-enabled",
        }

    @app.get("/api/v1/config", dependencies=[Depends(require_auth)])
    async def config() -> dict[str, Any]:
        return app.state.settings.public_dict()

    @app.get("/api/v1/models", dependencies=[Depends(require_auth)])
    async def models() -> dict[str, Any]:
        try:
            records = scan_models(Path(app.state.settings.model_root))
        except ModelRootError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {
            "count": len(records),
            "valid_count": sum(1 for item in records if item["valid"]),
            "models": [summary(item) for item in records],
        }

    def find_model(model_id: str) -> dict[str, Any]:
        try:
            records = scan_models(Path(app.state.settings.model_root))
        except ModelRootError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        for record in records:
            if record["id"] == model_id:
                return record
        raise HTTPException(status_code=404, detail=f"model not found: {model_id}")

    @app.get("/api/v1/models/{model_id}", dependencies=[Depends(require_auth)])
    async def model_detail(model_id: str) -> dict[str, Any]:
        return find_model(model_id)

    @app.get("/api/v1/models/{model_id}/manifest", dependencies=[Depends(require_auth)])
    async def model_manifest(model_id: str) -> dict[str, Any]:
        record = find_model(model_id)
        return {
            "id": record["id"],
            "source": record["manifest_source"],
            "valid": record["manifest_valid"],
            "review_required": record["manifest_review_required"],
            "errors": record["errors"],
            "warnings": record["warnings"],
            "manifest": record["manifest"],
        }

    @app.get("/api/v1/models/{model_id}/validate", dependencies=[Depends(require_auth)])
    async def validate_model(model_id: str) -> dict[str, Any]:
        record = find_model(model_id)
        return {
            "id": record["id"],
            "valid": record["valid"],
            "ready_to_start": record["ready_to_start"],
            "errors": record["errors"],
            "warnings": record["warnings"],
            "artifacts": record["artifacts"],
        }

    return app


app = create_app()
