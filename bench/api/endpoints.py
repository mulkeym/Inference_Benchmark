import sqlite3
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from bench.adapters.base import classify_exception, make_adapter
from bench.storage import crypto, db

router = APIRouter(prefix="/api")


def serialize_endpoint(row: dict) -> dict:
    output = {key: value for key, value in row.items() if key != "api_key_encrypted"}
    output["has_api_key"] = bool(row.get("api_key_encrypted"))
    output["verify_tls"] = bool(row.get("verify_tls"))
    return output


class EndpointIn(BaseModel):
    name: str
    type: Literal["openai", "asksage"]
    base_url: str
    api_key: str | None = None
    default_model: str | None = None
    verify_tls: bool = True


class EndpointPatch(BaseModel):
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    default_model: str | None = None
    verify_tls: bool | None = None


class EndpointModelsIn(BaseModel):
    type: Literal["openai", "asksage"]
    base_url: str = Field(min_length=1)
    api_key: str | None = None
    verify_tls: bool = True
    endpoint_id: int | None = None


def _get_or_404(request: Request, endpoint_id: int) -> dict:
    from bench.api.app import ApiError
    endpoint = db.get_endpoint(request.app.state.db_conn, endpoint_id)
    if endpoint is None:
        raise ApiError(404, "not_found", f"endpoint {endpoint_id} not found")
    return endpoint


@router.get("/endpoints")
async def list_endpoints(request: Request):
    return [serialize_endpoint(endpoint)
            for endpoint in db.list_endpoints(request.app.state.db_conn)]


@router.post("/endpoints")
async def create_endpoint(request: Request, body: EndpointIn):
    from bench.api.app import ApiError
    state = request.app.state
    data = body.model_dump(exclude={"api_key"})
    data["verify_tls"] = int(body.verify_tls)
    data["api_key_encrypted"] = crypto.encrypt(state.secret, body.api_key) if body.api_key else None
    try:
        return serialize_endpoint(db.create_endpoint(state.db_conn, data))
    except sqlite3.IntegrityError as exc:
        raise ApiError(409, "duplicate", f"endpoint name '{body.name}' already exists") from exc


@router.post("/endpoints/models")
async def list_endpoint_models(request: Request, body: EndpointModelsIn):
    """Discover models using unsaved form values without running an inference request."""
    from bench.api.app import ApiError
    state = request.app.state
    api_key = body.api_key or None
    if api_key is None and body.endpoint_id is not None:
        saved = _get_or_404(request, body.endpoint_id)
        # Only release the stored key to the URL it was saved for; otherwise a
        # caller could exfiltrate it by pointing base_url at their own server.
        same_url = body.base_url.rstrip("/") == saved["base_url"].rstrip("/")
        if same_url and saved["api_key_encrypted"]:
            api_key = crypto.decrypt(state.secret, saved["api_key_encrypted"])
    adapter = make_adapter(body.type, body.base_url, api_key,
                           body.verify_tls, 15.0, True)
    try:
        models = await adapter.list_models()
    except Exception as exc:
        _, detail = classify_exception(exc)
        raise ApiError(502, "model_discovery", detail) from exc
    finally:
        await adapter.aclose()
    return {"models": sorted({model.strip() for model in models if model.strip()},
                             key=str.casefold)}


@router.put("/endpoints/{endpoint_id}")
async def update_endpoint(request: Request, endpoint_id: int, body: EndpointPatch):
    from bench.api.app import ApiError
    state = request.app.state
    _get_or_404(request, endpoint_id)
    data = body.model_dump(exclude_none=True, exclude={"api_key"})
    if "verify_tls" in data:
        data["verify_tls"] = int(data["verify_tls"])
    if body.api_key is not None:
        data["api_key_encrypted"] = crypto.encrypt(state.secret, body.api_key) if body.api_key else None
    try:
        endpoint = db.update_endpoint(state.db_conn, endpoint_id, data)
    except sqlite3.IntegrityError as exc:
        raise ApiError(409, "duplicate", "endpoint name already exists") from exc
    return serialize_endpoint(endpoint)


@router.delete("/endpoints/{endpoint_id}")
async def delete_endpoint(request: Request, endpoint_id: int):
    from bench.api.app import ApiError
    state = request.app.state
    _get_or_404(request, endpoint_id)
    if db.list_tests(state.db_conn, endpoint_id=endpoint_id):
        raise ApiError(409, "has_tests", "endpoint has test history; delete those tests first")
    db.delete_endpoint(state.db_conn, endpoint_id)
    return {"ok": True}


@router.post("/endpoints/{endpoint_id}/probe")
async def probe_endpoint(request: Request, endpoint_id: int):
    state = request.app.state
    endpoint = _get_or_404(request, endpoint_id)
    api_key = crypto.decrypt(state.secret, endpoint["api_key_encrypted"]) if endpoint["api_key_encrypted"] else None
    adapter = make_adapter(endpoint["type"], endpoint["base_url"], api_key,
                           bool(endpoint["verify_tls"]), 15.0, True)
    try:
        result = await adapter.probe()
    finally:
        await adapter.aclose()
    db.update_endpoint(state.db_conn, endpoint_id,
                       {"supports_streaming": int(result["supports_streaming"])})
    return result
