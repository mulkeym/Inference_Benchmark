import json

import httpx
import pytest

from bench.adapters.asksage import AskSageAdapter
from bench.adapters.openai import OpenAIAdapter
from bench.api.app import create_app
from tools.mockserver.app import create_app as create_mock


async def test_adapters_against_mock():
    mock = create_mock(ttft_ms=1,tps=10000,output_tokens=3)
    transport = httpx.ASGITransport(app=mock)
    openai = OpenAIAdapter("http://mock/v1",None,True,5,True,transport=transport)
    result = await openai.execute("hi","mock-model",10,0)
    assert result.ok and result.ttft_ms is not None and result.output_tokens == 3
    await openai.aclose()
    asksage = AskSageAdapter("http://mock",None,True,5,transport=transport)
    result = await asksage.execute("hi","mock-model",10,0)
    assert result.ok and result.ttft_ms is None and result.tokens_estimated
    await asksage.aclose()


@pytest.mark.parametrize(("base_url", "expected_header", "absent_header"), [
    ("https://zwppgcai-openai.azure.ie/openai/v1", "api-key", "authorization"),
    ("https://openai.example/v1", "authorization", "api-key"),
])
async def test_openai_uses_provider_auth_header(base_url, expected_header,
                                                absent_header):
    async def handler(request):
        assert request.headers[expected_header] in ("secret", "Bearer secret")
        assert absent_header not in request.headers
        return httpx.Response(200, json={"data": [{"id": "model"}]})

    adapter = OpenAIAdapter(base_url, "secret", True, 5, False,
                            transport=httpx.MockTransport(handler))
    assert await adapter.list_models() == ["model"]
    await adapter.aclose()


async def test_openai_probe_uses_configured_model_not_first_discovered_model():
    async def handler(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "catalog-model"}]})
        body = json.loads(request.content)
        assert body["model"] == "azure-deployment"
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(404, json={"error": "not supported"})
        return httpx.Response(200, json={
            "output_text": "OK",
            "usage": {"input_tokens": 3, "output_tokens": 1},
        })

    adapter = OpenAIAdapter("https://resource.openai.azure.com/openai/v1",
                            "secret", True, 5, False,
                            transport=httpx.MockTransport(handler))
    result = await adapter.probe("azure-deployment")
    await adapter.aclose()

    assert result["auth_ok"] is True


async def test_openai_plain_falls_back_to_responses_and_remembers_api():
    paths = []

    async def handler(request):
        paths.append(request.url.path)
        if request.url.path == "/v1/chat/completions":
            return httpx.Response(404, json={"error": "not supported"})
        body = json.loads(request.content)
        assert body == {"model": "responses-model", "input": "hello",
                        "max_output_tokens": 20, "temperature": 0.25}
        return httpx.Response(200, json={
            "output": [{"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": "hello back"}
            ]}],
            "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
        })

    adapter = OpenAIAdapter("http://mock/v1", None, True, 5, False,
                            transport=httpx.MockTransport(handler))
    first = await adapter.execute("hello", "responses-model", 20, 0.25)
    second = await adapter.execute("hello", "responses-model", 20, 0.25)
    await adapter.aclose()

    assert first.ok and first.prompt_tokens == 3 and first.output_tokens == 2
    assert second.ok
    assert paths == ["/v1/chat/completions", "/v1/responses", "/v1/responses"]


async def test_openai_streaming_falls_back_to_responses_events():
    async def handler(request):
        if request.url.path == "/v1/chat/completions":
            return httpx.Response(405, json={"error": "not supported"})
        body = json.loads(request.content)
        assert body["input"] == "hello" and body["stream"] is True
        events = [
            {"type": "response.created", "response": {"id": "resp_1"}},
            {"type": "response.output_text.delta", "delta": "hello "},
            {"type": "response.output_text.delta", "delta": "back"},
            {"type": "response.completed", "response": {
                "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}
            }},
        ]
        content = "".join(
            f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events)
        return httpx.Response(200, text=content,
                              headers={"content-type": "text/event-stream"})

    adapter = OpenAIAdapter("http://mock/v1", None, True, 5, True,
                            transport=httpx.MockTransport(handler))
    result = await adapter.execute("hello", "responses-model", 20, 0)
    await adapter.aclose()

    assert result.ok and result.ttft_ms is not None
    assert result.prompt_tokens == 3 and result.output_tokens == 2
    assert result.tokens_estimated is False


@pytest.fixture
async def client(tmp_path):
    app = create_app(tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as value:
        yield value


async def test_endpoint_crud_is_write_only(client):
    response = await client.post("/api/endpoints",json={"name":"e","type":"openai","base_url":"http://x/v1","api_key":"secret"})
    assert response.status_code == 200 and response.json()["has_api_key"] is True
    assert "secret" not in response.text and "api_key_encrypted" not in response.text
    assert (await client.get("/healthz")).json()["db_ok"] is True
    duplicate = await client.post("/api/endpoints",json={"name":"e","type":"openai","base_url":"u"})
    assert duplicate.status_code == 409
    invalid = await client.post("/api/endpoints",json={"name":"bad","type":"grpc","base_url":"u"})
    assert invalid.status_code == 422 and invalid.json()["error"]["code"] == "validation"
