import json
import time

import httpx

from bench.adapters.base import RequestResult, classify_exception, now_wall
from bench.engine.tokens import count_tokens


class OpenAIAdapter:
    def __init__(self, base_url: str, api_key: str | None, verify_tls: bool,
                 timeout_s: float, streaming: bool, transport=None):
        self.base_url = base_url.rstrip("/")
        self.streaming = streaming
        self._api_style = "chat"
        hostname = (httpx.URL(self.base_url).host or "").lower()
        is_azure = ("openai.azure." in hostname or
                    "services.ai.azure." in hostname or
                    "cognitiveservices.azure." in hostname)
        if api_key and is_azure:
            headers = {"api-key": api_key}
        elif api_key:
            headers = {"Authorization": f"Bearer {api_key}"}
        else:
            headers = {}
        self._client = httpx.AsyncClient(headers=headers, verify=verify_tls,
                                         timeout=timeout_s, transport=transport)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def list_models(self) -> list[str]:
        response = await self._client.get(f"{self.base_url}/models")
        response.raise_for_status()
        data = response.json().get("data", [])
        return [str(item["id"]) for item in data if item.get("id")]

    async def probe(self, model: str | None = None) -> dict:
        result = {"reachable": False, "auth_ok": False, "models": [],
                  "supports_streaming": False, "latency_ms": None, "error": None}
        start = time.perf_counter()
        try:
            try:
                result["models"] = await self.list_models()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (401, 403):
                    result.update(reachable=True, error="authentication failed")
                    return result
            result["reachable"] = True
            probe_model = model or (result["models"][0] if result["models"] else "default")
            request = await self.execute("Say OK.", probe_model, 1, 0.0)
            result["latency_ms"] = round((time.perf_counter() - start) * 1000, 1)
            if request.ok:
                result["auth_ok"] = True
                result["supports_streaming"] = self.streaming and request.ttft_ms is not None
            else:
                result["error"] = request.error_detail
        except Exception as exc:
            result["error"] = classify_exception(exc)[1]
        return result

    async def execute(self, text: str, model: str, max_tokens: int,
                      temperature: float) -> RequestResult:
        wall, started = now_wall(), time.perf_counter()
        try:
            if self._api_style == "responses":
                return await self._execute_responses(
                    text, model, max_tokens, temperature, wall, started)

            body = {"model": model, "messages": [{"role": "user", "content": text}],
                    "max_tokens": max_tokens, "temperature": temperature}
            if self.streaming:
                body.update(stream=True, stream_options={"include_usage": True})
                result = await self._execute_chat_stream(body, text, wall, started)
            else:
                result = await self._execute_chat_plain(body, text, wall, started)
            if result.ok or result.error_class != "http":
                return result

            fallback = await self._execute_responses(
                text, model, max_tokens, temperature, wall, started)
            if fallback.ok:
                self._api_style = "responses"
            return fallback
        except Exception as exc:
            error_class, detail = classify_exception(exc)
            return RequestResult("", wall, None, None, None, None, False,
                                 error_class, detail)

    async def _execute_responses(self, text, model, max_tokens, temperature,
                                 wall, started) -> RequestResult:
        body = {"model": model, "input": text, "max_output_tokens": max_tokens,
                "temperature": temperature}
        if self.streaming:
            body["stream"] = True
            return await self._execute_responses_stream(body, text, wall, started)
        return await self._execute_responses_plain(body, text, wall, started)

    async def _execute_chat_stream(self, body, text, wall, started) -> RequestResult:
        first = None
        chunks: list[str] = []
        usage = None
        async with self._client.stream("POST", f"{self.base_url}/chat/completions", json=body) as response:
            if response.status_code >= 300:
                await response.aread()
                return self._http_error(response, wall)
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload.strip() == "[DONE]":
                    break
                chunk = json.loads(payload)
                if chunk.get("usage"):
                    usage = chunk["usage"]
                choices = chunk.get("choices") or []
                delta = choices[0].get("delta") or {} if choices else {}
                content = delta.get("content")
                if content:
                    first = first or time.perf_counter()
                    chunks.append(content)
        finished = time.perf_counter()
        output = "".join(chunks)
        return RequestResult(
            "", wall, round((first - started) * 1000, 2) if first else None,
            round((finished - started) * 1000, 2),
            usage["prompt_tokens"] if usage else count_tokens(text),
            usage["completion_tokens"] if usage else (count_tokens(output) if output else 0),
            usage is None, None, None)

    async def _execute_chat_plain(self, body, text, wall, started) -> RequestResult:
        response = await self._client.post(f"{self.base_url}/chat/completions", json=body)
        if response.status_code >= 300:
            return self._http_error(response, wall)
        finished = time.perf_counter()
        data = response.json()
        output = data["choices"][0]["message"]["content"] or ""
        usage = data.get("usage")
        return RequestResult(
            "", wall, None, round((finished - started) * 1000, 2),
            usage["prompt_tokens"] if usage else count_tokens(text),
            usage["completion_tokens"] if usage else count_tokens(output),
            usage is None, None, None)

    async def _execute_responses_stream(self, body, text, wall, started) -> RequestResult:
        first = None
        chunks: list[str] = []
        usage = None
        async with self._client.stream(
                "POST", f"{self.base_url}/responses", json=body) as response:
            if response.status_code >= 300:
                await response.aread()
                return self._http_error(response, wall)
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].lstrip()
                if not payload or payload == "[DONE]":
                    continue
                event = json.loads(payload)
                event_type = event.get("type")
                if event_type == "response.output_text.delta":
                    delta = event.get("delta")
                    if delta:
                        first = first or time.perf_counter()
                        chunks.append(delta)
                if event_type in ("response.completed", "response.incomplete"):
                    usage = (event.get("response") or {}).get("usage") or usage
        finished = time.perf_counter()
        output = "".join(chunks)
        prompt_tokens, output_tokens = self._response_usage(usage)
        return RequestResult(
            "", wall, round((first - started) * 1000, 2) if first else None,
            round((finished - started) * 1000, 2),
            prompt_tokens if prompt_tokens is not None else count_tokens(text),
            output_tokens if output_tokens is not None else (count_tokens(output) if output else 0),
            prompt_tokens is None or output_tokens is None, None, None)

    async def _execute_responses_plain(self, body, text, wall, started) -> RequestResult:
        response = await self._client.post(f"{self.base_url}/responses", json=body)
        if response.status_code >= 300:
            return self._http_error(response, wall)
        finished = time.perf_counter()
        data = response.json()
        output = self._response_output_text(data)
        usage = data.get("usage")
        prompt_tokens, output_tokens = self._response_usage(usage)
        return RequestResult(
            "", wall, None, round((finished - started) * 1000, 2),
            prompt_tokens if prompt_tokens is not None else count_tokens(text),
            output_tokens if output_tokens is not None else count_tokens(output),
            prompt_tokens is None or output_tokens is None, None, None)

    @staticmethod
    def _response_output_text(data: dict) -> str:
        if isinstance(data.get("output_text"), str):
            return data["output_text"]
        chunks = []
        for item in data.get("output") or []:
            for content in item.get("content") or []:
                if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                    chunks.append(content["text"])
        return "".join(chunks)

    @staticmethod
    def _response_usage(usage: dict | None) -> tuple[int | None, int | None]:
        if not usage:
            return None, None
        return usage.get("input_tokens"), usage.get("output_tokens")

    @staticmethod
    def _http_error(response, wall) -> RequestResult:
        return RequestResult("", wall, None, None, None, None, False, "http",
                             f"HTTP {response.status_code}: {response.text[:200]}")
