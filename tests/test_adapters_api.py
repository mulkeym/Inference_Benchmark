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
