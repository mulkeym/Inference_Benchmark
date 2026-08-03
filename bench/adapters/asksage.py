import time

import httpx

from bench.adapters.base import RequestResult, classify_exception, now_wall
from bench.engine.tokens import count_tokens


class AskSageAdapter:
    def __init__(self, base_url: str, api_key: str | None, verify_tls: bool,
                 timeout_s: float, transport=None):
        self.base_url = base_url.rstrip("/")
        headers = {"x-access-tokens": api_key} if api_key else {}
        self._client = httpx.AsyncClient(headers=headers, verify=verify_tls,
                                         timeout=timeout_s, transport=transport)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def list_models(self) -> list[str]:
        try:
            response = await self._client.post(f"{self.base_url}/get-models", json={})
            response.raise_for_status()
            data = response.json()
            return [str(model) for model in (data.get("response") or data.get("models") or [])]
        except Exception:
            return []

    async def probe(self) -> dict:
        result = {"reachable": False, "auth_ok": False, "models": [],
                  "supports_streaming": False, "latency_ms": None, "error": None}
        started = time.perf_counter()
        try:
            result["models"] = await self.list_models()
            request = await self.execute("Say OK.", result["models"][0] if result["models"] else "gpt-4o", 1, 0.0)
            result["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
            result["reachable"] = True
            if request.ok:
                result["auth_ok"] = True
            else:
                result["error"] = request.error_detail
        except Exception as exc:
            result["error"] = classify_exception(exc)[1]
        return result

    async def execute(self, text: str, model: str, max_tokens: int,
                      temperature: float) -> RequestResult:
        wall, started = now_wall(), time.perf_counter()
        try:
            response = await self._client.post(f"{self.base_url}/query", json={
                "message": text, "model": model, "temperature": temperature,
                "dataset": "none", "live": 0})
            finished = time.perf_counter()
            if response.status_code >= 300:
                return self._error(wall, "http", f"HTTP {response.status_code}: {response.text[:200]}")
            data = response.json()
            if data.get("status") not in (None, 200):
                return self._error(wall, "http", f"body status {data.get('status')}: {str(data.get('response'))[:200]}")
            output = data.get("message") or ""
            return RequestResult("", wall, None, round((finished - started) * 1000, 2),
                                 count_tokens(text), count_tokens(output) if output else 0,
                                 True, None, None)
        except Exception as exc:
            error_class, detail = classify_exception(exc)
            return self._error(wall, error_class, detail)

    @staticmethod
    def _error(wall: str, error_class: str, detail: str) -> RequestResult:
        return RequestResult("", wall, None, None, None, None, False, error_class, detail)
