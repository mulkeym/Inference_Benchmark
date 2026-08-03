import datetime
from dataclasses import dataclass
from typing import Protocol

import httpx


@dataclass
class RequestResult:
    prompt_id: str
    t_send_wall: str
    ttft_ms: float | None
    e2e_ms: float | None
    prompt_tokens: int | None
    output_tokens: int | None
    tokens_estimated: bool
    error_class: str | None
    error_detail: str | None

    @property
    def ok(self) -> bool:
        return self.error_class is None

    def to_row(self, test_id: int, concurrency: int) -> dict:
        return {"test_id": test_id, "concurrency": concurrency,
                "prompt_id": self.prompt_id, "t_send_wall": self.t_send_wall,
                "ttft_ms": self.ttft_ms, "e2e_ms": self.e2e_ms,
                "prompt_tokens": self.prompt_tokens, "output_tokens": self.output_tokens,
                "tokens_estimated": int(self.tokens_estimated),
                "error_class": self.error_class, "error_detail": self.error_detail}


class Adapter(Protocol):
    async def probe(self, model: str | None = None) -> dict: ...
    async def list_models(self) -> list[str]: ...
    async def execute(self, text: str, model: str, max_tokens: int,
                      temperature: float) -> RequestResult: ...
    async def aclose(self) -> None: ...


def now_wall() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def classify_exception(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, httpx.TimeoutException):
        return "timeout", str(exc) or "request timed out"
    if isinstance(exc, (httpx.ConnectError, httpx.NetworkError)):
        return "connect", str(exc) or "connection failed"
    return "bad_response", str(exc) or exc.__class__.__name__


def make_adapter(type_: str, base_url: str, api_key: str | None,
                 verify_tls: bool, timeout_s: float, streaming: bool) -> Adapter:
    if type_ == "openai":
        from bench.adapters.openai import OpenAIAdapter
        return OpenAIAdapter(base_url, api_key, verify_tls, timeout_s, streaming)
    if type_ == "asksage":
        from bench.adapters.asksage import AskSageAdapter
        return AskSageAdapter(base_url, api_key, verify_tls, timeout_s)
    raise ValueError(f"unknown endpoint type: {type_}")
