import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from bench.adapters.base import make_adapter
from bench.engine.sweep import SweepConfig, run_sweep
from bench.engine.prompt_analysis import analyze_requests
from bench.engine.workload import PRESETS, load_prompts
from bench.reports import export
from bench.storage import crypto, db

router = APIRouter(prefix="/api")


class TestIn(BaseModel):
    endpoint_id: int
    model: str
    workload: str
    budget_ttft_ms: float | None = None
    budget_e2e_ms: float | None = None
    settings: dict = Field(default_factory=dict)


def _serialize(state, test: dict) -> dict:
    endpoint = db.get_endpoint(state.db_conn, test["endpoint_id"])
    return {**test, "endpoint_name": endpoint["name"] if endpoint else None,
            "endpoint_type": endpoint["type"] if endpoint else None,
            "supports_streaming": endpoint["supports_streaming"] if endpoint else None}


@router.post("/tests")
async def start_test(request: Request, body: TestIn):
    from bench.api.app import ApiError
    state = request.app.state
    active = db.active_test_id(state.db_conn)
    if active is not None:
        raise ApiError(409, "test_active", f"test {active} is already running")
    endpoint = db.get_endpoint(state.db_conn, body.endpoint_id)
    if endpoint is None:
        raise ApiError(404, "not_found", f"endpoint {body.endpoint_id} not found")
    if body.workload not in PRESETS:
        raise ApiError(422, "bad_workload", f"unknown workload '{body.workload}'")

    allowed = {"max_concurrency", "dwell_s", "min_requests", "warmup_requests",
               "timeout_s", "temperature", "seed"}
    try:
        config = SweepConfig(
            workload=body.workload,
            budget_ttft_ms=body.budget_ttft_ms,
            budget_e2e_ms=body.budget_e2e_ms,
            **{key: value for key, value in body.settings.items() if key in allowed},
        )
    except TypeError as exc:
        raise ApiError(422, "bad_settings", str(exc)) from exc
    streaming = (bool(endpoint["supports_streaming"])
                 if endpoint["supports_streaming"] is not None
                 else endpoint["type"] == "openai")
    api_key = crypto.decrypt(state.secret, endpoint["api_key_encrypted"]) if endpoint["api_key_encrypted"] else None
    adapter = make_adapter(endpoint["type"], endpoint["base_url"], api_key,
                           bool(endpoint["verify_tls"]), config.timeout_s, streaming)
    test = db.create_test(state.db_conn, {
        "endpoint_id": endpoint["id"], "model": body.model, "workload": body.workload,
        "budget_ttft_ms": body.budget_ttft_ms, "budget_e2e_ms": body.budget_e2e_ms,
        "settings": config.__dict__,
    })
    stop_event = asyncio.Event()
    test_id = test["id"]

    def publish(kind: str, data: dict):
        state.hub.publish(test_id, kind, data)

    async def runner():
        try:
            await run_sweep(state.db_conn, test_id, adapter, body.model,
                            streaming, config, publish, stop_event)
        finally:
            if state.active and state.active["test_id"] == test_id:
                state.active = None

    task = asyncio.create_task(runner())
    state.active = {"test_id": test_id, "task": task, "stop_event": stop_event}
    return _serialize(state, test)


@router.get("/tests")
async def list_tests(request: Request, endpoint_id: int | None = None,
                     model: str | None = None):
    state = request.app.state
    return [_serialize(state, test)
            for test in db.list_tests(state.db_conn, endpoint_id, model)]


@router.get("/tests/{test_id}")
async def get_test(request: Request, test_id: int):
    from bench.api.app import ApiError
    state = request.app.state
    test = db.get_test(state.db_conn, test_id)
    if test is None:
        raise ApiError(404, "not_found", f"test {test_id} not found")
    return {**_serialize(state, test), "steps": db.list_steps(state.db_conn, test_id)}


@router.post("/tests/{test_id}/stop")
async def stop_test(request: Request, test_id: int):
    from bench.api.app import ApiError
    state = request.app.state
    if not state.active or state.active["test_id"] != test_id:
        raise ApiError(409, "not_running", f"test {test_id} is not running")
    state.active["stop_event"].set()
    return {"ok": True}


@router.delete("/tests/{test_id}")
async def delete_test(request: Request, test_id: int):
    from bench.api.app import ApiError
    state = request.app.state
    if state.active and state.active["test_id"] == test_id:
        raise ApiError(409, "test_active", "stop the test before deleting it")
    if db.get_test(state.db_conn, test_id) is None:
        raise ApiError(404, "not_found", f"test {test_id} not found")
    db.delete_test(state.db_conn, test_id)
    return {"ok": True}


@router.get("/tests/{test_id}/export.csv")
async def export_csv(request: Request, test_id: int):
    from bench.api.app import ApiError
    state = request.app.state
    test = db.get_test(state.db_conn, test_id)
    if test is None:
        raise ApiError(404, "not_found", f"test {test_id} not found")
    body = export.to_csv(test, db.list_requests(state.db_conn, test_id))
    return Response(body, media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="test-{test_id}.csv"'})


@router.get("/tests/{test_id}/prompt-analysis")
async def prompt_analysis(request: Request, test_id: int):
    from bench.api.app import ApiError
    state = request.app.state
    test = db.get_test(state.db_conn, test_id)
    if test is None:
        raise ApiError(404, "not_found", f"test {test_id} not found")
    prompts = load_prompts(test["workload"])
    prompt_texts = {prompt["id"]: prompt["text"] for prompt in prompts}
    return analyze_requests(db.list_requests(state.db_conn, test_id), prompt_texts)


@router.get("/tests/{test_id}/export.html")
async def export_html(request: Request, test_id: int):
    from bench.api.app import ApiError
    state = request.app.state
    test = db.get_test(state.db_conn, test_id)
    if test is None:
        raise ApiError(404, "not_found", f"test {test_id} not found")
    endpoint = db.get_endpoint(state.db_conn, test["endpoint_id"])
    body = export.to_html(test, db.list_steps(state.db_conn, test_id),
                          endpoint["name"] if endpoint else "?")
    return Response(body, media_type="text/html", headers={
        "Content-Disposition": f'attachment; filename="test-{test_id}.html"'})
