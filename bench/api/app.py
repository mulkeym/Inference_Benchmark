import contextlib
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.websockets import WebSocketDisconnect

from bench.api.ws import LiveHub
from bench.storage import crypto, db


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message


def create_app(data_dir: Path, secret_key: str | None = None) -> FastAPI:
    data_dir.mkdir(parents=True, exist_ok=True)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        db.mark_running_tests_stopped(app.state.db_conn)
        yield
        active = app.state.active
        if active:
            active["stop_event"].set()
            with contextlib.suppress(Exception):
                await active["task"]
        app.state.db_conn.close()

    app = FastAPI(lifespan=lifespan)
    app.state.db_conn = db.connect(data_dir / "benchmark.db")
    app.state.secret = crypto.load_or_create_secret(data_dir, secret_key)
    app.state.active = None
    app.state.hub = LiveHub()

    @app.exception_handler(ApiError)
    async def api_error(_request: Request, exc: ApiError):
        return JSONResponse(status_code=exc.status,
                            content={"error": {"code": exc.code, "message": exc.message}})

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        details = exc.errors()
        message = details[0].get("msg", "invalid request") if details else "invalid request"
        return JSONResponse(status_code=422,
                            content={"error": {"code": "validation", "message": message}})

    from bench.api.endpoints import router as endpoints_router
    from bench.api.tests_routes import router as tests_router
    app.include_router(endpoints_router)
    app.include_router(tests_router)

    @app.get("/healthz")
    async def healthz():
        app.state.db_conn.execute("SELECT 1").fetchone()
        return {"status": "ok", "active_test_id": db.active_test_id(app.state.db_conn),
                "db_ok": True}

    @app.websocket("/ws/tests/{test_id}")
    async def ws_test(websocket: WebSocket, test_id: int):
        await websocket.accept()
        test = db.get_test(app.state.db_conn, test_id)
        if test is None:
            await websocket.close(code=4404)
            return
        await websocket.send_json({"type": "snapshot", "data": {
            "test": test, "steps": db.list_steps(app.state.db_conn, test_id)}})
        if test["status"] != "running":
            await websocket.close()
            return
        queue = app.state.hub.subscribe(test_id)
        try:
            while True:
                message = await queue.get()
                await websocket.send_json(message)
                if message["type"] == "status":
                    break
        except WebSocketDisconnect:
            pass
        finally:
            app.state.hub.unsubscribe(test_id, queue)

    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if dist.exists():
        assets = dist / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            candidate = (dist / path).resolve()
            if candidate.is_relative_to(dist.resolve()) and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")
    return app
