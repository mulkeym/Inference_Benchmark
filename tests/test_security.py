import httpx
import pytest

import bench.api.endpoints as endpoints_module
from bench.api.app import create_app
from bench.reports.export import to_csv


@pytest.fixture
async def client(tmp_path):
    app = create_app(tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as value:
        yield value


class _CapturingAdapter:
    """Stands in for make_adapter to record which api_key the route released."""
    last_key = "UNSET"

    def __init__(self, api_key):
        _CapturingAdapter.last_key = api_key

    async def list_models(self):
        return ["m1"]

    async def aclose(self):
        pass


async def test_saved_key_not_released_to_foreign_base_url(client, monkeypatch):
    monkeypatch.setattr(
        endpoints_module, "make_adapter",
        lambda type_, base_url, api_key, verify_tls, timeout_s, streaming:
        _CapturingAdapter(api_key))
    created = await client.post("/api/endpoints", json={
        "name": "prod", "type": "openai", "base_url": "http://real-host/v1",
        "api_key": "sk-secret"})
    endpoint_id = created.json()["id"]

    # Same URL (modulo trailing slash): stored key may be used.
    response = await client.post("/api/endpoints/models", json={
        "type": "openai", "base_url": "http://real-host/v1/",
        "endpoint_id": endpoint_id})
    assert response.status_code == 200
    assert _CapturingAdapter.last_key == "sk-secret"

    # Different URL: the stored key must NOT be attached (exfiltration guard).
    response = await client.post("/api/endpoints/models", json={
        "type": "openai", "base_url": "http://attacker-host/v1",
        "endpoint_id": endpoint_id})
    assert response.status_code == 200
    assert _CapturingAdapter.last_key is None


def test_csv_export_defuses_formula_injection():
    rows = [{"concurrency": 1, "prompt_id": "=2+5|cmd", "t_send_wall": "x",
             "ttft_ms": 1.0, "e2e_ms": 2.0, "prompt_tokens": 3,
             "output_tokens": 4, "tokens_estimated": 0,
             "error_class": "http", "error_detail": "@SUM(A1)"},
            {"concurrency": 2, "prompt_id": "chat-01", "t_send_wall": "x",
             "ttft_ms": None, "e2e_ms": 2.0, "prompt_tokens": 3,
             "output_tokens": 4, "tokens_estimated": 0,
             "error_class": None, "error_detail": "-1 unexpected"}]
    out = to_csv({}, rows)
    assert "'=2+5|cmd" in out and "'@SUM(A1)" in out and "'-1 unexpected" in out
    assert "\n2,chat-01" in out  # ordinary cells untouched
