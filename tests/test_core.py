import asyncio
from pathlib import Path

from bench.adapters.base import RequestResult, now_wall
from bench.config import load_settings
from bench.engine.metrics import aggregate_step, percentile
from bench.engine.prompt_analysis import analyze_requests
from bench.engine.sweep import SweepConfig, run_sweep
from bench.engine.verdict import compute_verdict, interpolate_budget
from bench.engine.workload import PRESETS, PromptCycler, load_prompts
from bench.reports import export
from bench.storage import crypto, db


class ModelListAdapter:
    def __init__(self, captured:dict, api_key:str|None):
        captured["api_key"] = api_key

    async def list_models(self): return ["model-z", "model-a", "model-a"]
    async def aclose(self): pass


def test_settings_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.delenv("SECRET_KEY", raising=False)
    settings = load_settings()
    assert settings.data_dir == Path(tmp_path)
    assert settings.port == 8080 and settings.secret_key is None


def test_unsaved_endpoint_model_discovery_reuses_saved_key(monkeypatch,tmp_path):
    from fastapi.testclient import TestClient
    from bench.api.app import create_app
    captured = {}
    monkeypatch.setattr("bench.api.endpoints.make_adapter",
                        lambda _type,_url,key,_verify,_timeout,_streaming: ModelListAdapter(captured,key))
    with TestClient(create_app(tmp_path)) as client:
        saved = client.post("/api/endpoints",json={"name":"e","type":"openai",
            "base_url":"https://example.test/v1","api_key":"secret"}).json()
        response = client.post("/api/endpoints/models",json={"type":"openai",
            "base_url":"https://example.test/v1","endpoint_id":saved["id"]})
    assert response.status_code == 200
    assert response.json() == {"models":["model-a","model-z"]}
    assert captured["api_key"] == "secret"


def test_storage_crypto_and_cascade(tmp_path):
    secret = crypto.load_or_create_secret(tmp_path)
    token = crypto.encrypt(secret, "sk-secret")
    assert token != "sk-secret" and crypto.decrypt(secret, token) == "sk-secret"
    assert (tmp_path / ".secret").stat().st_mode & 0o777 == 0o600
    conn = db.connect(tmp_path / "benchmark.db")
    endpoint = db.create_endpoint(conn, {"name":"e","type":"openai","base_url":"u"})
    test = db.create_test(conn, {"endpoint_id":endpoint["id"],"model":"m","workload":"chat","settings":{}})
    db.insert_request(conn, {"test_id":test["id"],"concurrency":1,"prompt_id":"p",
        "t_send_wall":"x","ttft_ms":1,"e2e_ms":2,"prompt_tokens":3,"output_tokens":4,
        "tokens_estimated":0,"error_class":None,"error_detail":None})
    db.delete_test(conn, test["id"])
    assert db.list_requests(conn, test["id"]) == []


def test_metrics_and_verdict():
    results = [RequestResult("p","x",100,1000,50,100,False,None,None),
               RequestResult("p","x",300,3000,50,100,False,None,None),
               RequestResult("p","x",None,None,None,None,False,"timeout","slow")]
    row = aggregate_step(2, results, 10, "x")
    assert percentile([10,20,30,40],95) == 38.5
    assert row["throughput_tps"] == 20 and row["error_count"] == 1
    steps = [
        {"concurrency":1,"throughput_tps":100,"ttft_p95_ms":200,"e2e_p95_ms":2000},
        {"concurrency":2,"throughput_tps":190,"ttft_p95_ms":250,"e2e_p95_ms":2500},
        {"concurrency":4,"throughput_tps":340,"ttft_p95_ms":350,"e2e_p95_ms":3500},
        {"concurrency":8,"throughput_tps":560,"ttft_p95_ms":600,"e2e_p95_ms":6000},
        {"concurrency":16,"throughput_tps":610,"ttft_p95_ms":1200,"e2e_p95_ms":12000},
    ]
    assert interpolate_budget(steps,"ttft_p95_ms",900) == 12.0
    verdict = compute_verdict(steps,900,None,True,{})
    assert verdict["knee_concurrency"] == 8 and verdict["budget"]["limited_by"] == "ttft"
    assert verdict["budget"]["crossed"] is True


def test_workloads_and_cache_buster():
    for preset in PRESETS:
        assert len(load_prompts(preset)) == 20
    a, b = PromptCycler(load_prompts("chat"),7), PromptCycler(load_prompts("chat"),7)
    first_a, first_b = a.next(), b.next()
    assert first_a["id"] == first_b["id"]
    assert first_a["text"].startswith("[req ") and first_a["text"] != first_b["text"]


def test_prompt_analysis_groups_requests_and_computes_output_rate():
    requests = [
        {"prompt_id":"chat-01","concurrency":2,"ttft_ms":100,"e2e_ms":1100,
         "prompt_tokens":500,"output_tokens":100,"error_class":None},
        {"prompt_id":"chat-01","concurrency":2,"ttft_ms":200,"e2e_ms":2200,
         "prompt_tokens":500,"output_tokens":200,"error_class":None},
        {"prompt_id":"chat-01","concurrency":2,"ttft_ms":None,"e2e_ms":None,
         "prompt_tokens":None,"output_tokens":None,"error_class":"timeout"},
        {"prompt_id":"chat-02","concurrency":4,"ttft_ms":300,"e2e_ms":1300,
         "prompt_tokens":510,"output_tokens":50,"error_class":None},
    ]
    analysis = analyze_requests(requests, {"chat-01":"Explain the first topic.",
                                           "chat-02":"Explain the second topic."})
    assert analysis["prompts"] == ["chat-01", "chat-02"]
    assert analysis["concurrencies"] == [2, 4]
    assert analysis["prompt_texts"]["chat-01"] == "Explain the first topic."
    first = analysis["cells"][0]
    assert first["request_count"] == 3 and first["error_count"] == 1
    assert first["ttft_p50_ms"] == 150
    assert first["e2e_p50_ms"] == 1650
    assert first["output_rate_tps_p50"] == 100


def test_html_export_renders_ttft_as_explicit_high_contrast_series():
    test = {"id":1,"model":"m","workload":"chat","status":"completed","verdict":None}
    steps = [{"concurrency":1,"requests_completed":20,"throughput_tps":100,
              "ttft_p50_ms":150,"ttft_p95_ms":250,"e2e_p50_ms":900,
              "e2e_p95_ms":1200,"error_count":0}]
    report = export.to_html(test, steps, "endpoint")
    assert "name:'p95 TTFT (ms)'" in report
    assert "color:'#f4c152',symbol:'triangle'" in report
    assert "ttftSteps.map(s=>[s.concurrency,s.ttft_p95_ms])" in report
    assert "name:'output tok/s',position:'left',nameTextStyle:{color:'#5ba3f5'" in report
    assert "name:'p95 latency (ms)',position:'right',nameTextStyle:{color:'#d9a13d'" in report
    assert "name:'latency threshold',type:'line',yAxisIndex:1,data:[],silent:true,animation:false" in report


class FakeAdapter:
    def __init__(self): self.in_flight = 0
    async def execute(self, text, model, max_tokens, temperature):
        self.in_flight += 1
        current = self.in_flight
        try:
            await asyncio.sleep(.003 * max(1,current / 4))
            return RequestResult("",now_wall(),3,8,100,20,False,None,None)
        finally: self.in_flight -= 1
    async def aclose(self): pass


class BudgetAdapter:
    """Latency rises with active concurrency; throughput keeps scaling."""
    def __init__(self): self.in_flight = 0
    async def execute(self, text, model, max_tokens, temperature):
        self.in_flight += 1
        concurrency = self.in_flight
        try:
            await asyncio.sleep(.004)
            return RequestResult("",now_wall(),concurrency * 100,concurrency * 100,
                                 100,20,False,None,None)
        finally: self.in_flight -= 1
    async def aclose(self): pass


async def test_fast_sweep_writes_verdict(tmp_path):
    conn = db.connect(tmp_path / "sweep.db")
    endpoint = db.create_endpoint(conn,{"name":"e","type":"openai","base_url":"u"})
    test = db.create_test(conn,{"endpoint_id":endpoint["id"],"model":"m","workload":"chat","settings":{}})
    config = SweepConfig(max_concurrency=4,dwell_s=.02,min_requests=3,warmup_requests=1)
    await run_sweep(conn,test["id"],FakeAdapter(),"m",True,config,lambda *_:None,asyncio.Event())
    saved = db.get_test(conn,test["id"])
    assert saved["status"] == "completed" and saved["verdict"] is not None
    assert len(db.list_steps(conn,test["id"])) == 3


async def test_explicit_budget_continues_then_refines_crossing(tmp_path):
    conn = db.connect(tmp_path / "budget.db")
    endpoint = db.create_endpoint(conn,{"name":"e","type":"openai","base_url":"u"})
    test = db.create_test(conn,{"endpoint_id":endpoint["id"],"model":"m","workload":"chat","settings":{}})
    config = SweepConfig(max_concurrency=32,dwell_s=.02,min_requests=6,
                         warmup_requests=0,budget_e2e_ms=1000)
    await run_sweep(conn,test["id"],BudgetAdapter(),"m",True,config,
                    lambda *_:None,asyncio.Event())
    saved = db.get_test(conn,test["id"])
    concurrencies = [step["concurrency"] for step in db.list_steps(conn,test["id"])]
    assert 16 in concurrencies                 # ignored the 5x TTFT guard at 8
    assert 12 in concurrencies                 # midpoint between safe 8 and crossed 16
    assert saved["flags"]["stop_reason"] == "budget_exceeded"
    assert saved["flags"]["refinement_concurrency"] == 12
    assert saved["verdict"]["budget"]["crossed"] is True
    assert 8 < saved["verdict"]["budget"]["max_concurrency"] < 12
    assert saved["verdict"]["knee_concurrency"] == 8


async def test_adaptive_latency_guard_refines_and_excludes_crossed_step(tmp_path):
    conn = db.connect(tmp_path / "guard.db")
    endpoint = db.create_endpoint(conn,{"name":"e","type":"openai","base_url":"u"})
    test = db.create_test(conn,{"endpoint_id":endpoint["id"],"model":"m","workload":"chat","settings":{}})
    config = SweepConfig(max_concurrency=32,dwell_s=.02,min_requests=6,warmup_requests=0)
    await run_sweep(conn,test["id"],BudgetAdapter(),"m",True,config,
                    lambda *_:None,asyncio.Event())
    saved = db.get_test(conn,test["id"])
    concurrencies = [step["concurrency"] for step in db.list_steps(conn,test["id"])]
    assert saved["flags"]["stop_reason"] == "latency_blowup"
    assert saved["flags"]["latency_guard_ms"] == 500
    assert saved["flags"]["refinement_concurrency"] == 6
    assert 6 in concurrencies and 8 in concurrencies
    assert saved["verdict"]["knee_concurrency"] == 4
    assert saved["verdict"]["guard"]["crossed"] is True
