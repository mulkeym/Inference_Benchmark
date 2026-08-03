import argparse
import asyncio
import json
import random
import re

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

DIRECTIVE = re.compile(r"@@([^@]+)@@")
WORDS = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel"]


def _parse_directives(text: str, defaults: dict) -> dict:
    config = dict(defaults)
    match = DIRECTIVE.search(text or "")
    if match:
        for part in match.group(1).split(";"):
            key, _, value = part.partition("=")
            key = key.strip()
            if key == "ttft": config["ttft_ms"] = float(value)
            elif key == "tps": config["tps"] = float(value)
            elif key == "tokens": config["output_tokens"] = int(value)
            elif key == "error": config["force_error"] = value.strip() == "1"
    return config


def create_app(ttft_ms: float = 250.0, tps: float = 40.0,
               output_tokens: int = 64, error_rate: float = 0.0) -> FastAPI:
    app = FastAPI()
    defaults = {"ttft_ms": ttft_ms, "tps": tps, "output_tokens": output_tokens,
                "force_error": False}

    def words(n: int) -> list[str]:
        return [WORDS[i % len(WORDS)] + (" " if i < n - 1 else "") for i in range(n)]

    def should_error(config) -> bool:
        return config["force_error"] or (error_rate > 0 and random.random() < error_rate)

    @app.get("/v1/models")
    async def models():
        return {"data": [{"id": "mock-model"}]}

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        body = await request.json()
        text = body["messages"][-1]["content"]
        config = _parse_directives(text, defaults)
        count = min(config["output_tokens"], body.get("max_tokens", 10**9))
        if should_error(config):
            return JSONResponse({"error": "mock error"}, status_code=500)
        usage = {"prompt_tokens": max(1, len(text) // 4), "completion_tokens": count}
        if body.get("stream"):
            async def generate():
                await asyncio.sleep(config["ttft_ms"] / 1000)
                for word in words(count):
                    yield "data: " + json.dumps({"choices": [{"delta": {"content": word}, "index": 0}]}) + "\n\n"
                    await asyncio.sleep(1 / config["tps"])
                yield "data: " + json.dumps({"choices": [], "usage": usage}) + "\n\n"
                yield "data: [DONE]\n\n"
            return StreamingResponse(generate(), media_type="text/event-stream")
        await asyncio.sleep(config["ttft_ms"] / 1000 + count / config["tps"])
        return {"choices": [{"message": {"content": "".join(words(count))}}], "usage": usage}

    @app.post("/query")
    async def query(request: Request):
        body = await request.json()
        config = _parse_directives(body.get("message", ""), defaults)
        if should_error(config):
            return {"status": 500, "response": "mock error"}
        count = config["output_tokens"]
        await asyncio.sleep(config["ttft_ms"] / 1000 + count / config["tps"])
        return {"status": 200, "message": "".join(words(count))}

    @app.post("/get-models")
    async def get_models():
        return {"response": ["mock-model"]}

    return app


if __name__ == "__main__":
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--ttft-ms", type=float, default=250.0)
    parser.add_argument("--tps", type=float, default=40.0)
    parser.add_argument("--output-tokens", type=int, default=64)
    parser.add_argument("--error-rate", type=float, default=0.0)
    args = parser.parse_args()
    uvicorn.run(create_app(args.ttft_ms, args.tps, args.output_tokens, args.error_rate),
                host="0.0.0.0", port=args.port, log_level="warning")
