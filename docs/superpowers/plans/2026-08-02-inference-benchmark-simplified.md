# Simplified Inference Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the simplified inference benchmark from `docs/superpowers/specs/2026-08-02-inference-benchmark-simplified-design.md`: one auto concurrency sweep that finds the sweet spot between concurrency, tokens/sec, and latency.

**Architecture:** FastAPI backend with the sweep engine as an in-process asyncio task, SQLite (WAL) storage, two HTTP adapters (OpenAI-compatible, AskSage), and a Vite + React + ECharts SPA served as static files. One container. Fresh start — the old `bench/`, `frontend/`, and `tests/` code is deleted in Task 1.

**Tech Stack:** Python 3.12, FastAPI, uvicorn, httpx, numpy, psutil, cryptography (Fernet), tiktoken (optional at runtime), pytest + pytest-asyncio; Vite, React 18, TypeScript, react-router-dom, ECharts; Playwright for the smoke test.

## Global Constraints

- Spec is authoritative: `docs/superpowers/specs/2026-08-02-inference-benchmark-simplified-design.md`.
- Sweep steps double: 1, 2, 4, … up to ceiling (default **128**). Step dwell: ≥ **45 s** AND ≥ **20** completed requests (defaults; configurable). Warmup: **3** discarded sequential requests.
- Early stop rules (spec §3.2): throughput gain < **10%** for **2** consecutive steps; step error rate > **10%**; p95 latency > **5×** concurrency-1 baseline; any set budget exceeded **2×**; ceiling reached; user stop.
- Verdict requires ≥ **3** completed steps and no `client_saturated` flag; budget line interpolates linearly, never extrapolates (spec §3.4).
- Non-streaming endpoints: TTFT is NULL everywhere; all TTFT-based logic falls back to E2E.
- API keys: encrypted at rest (Fernet), write-only through the API, never in exports or logs.
- One active test at a time; starting a second returns HTTP **409**.
- No silent retries, ever. Failures are recorded with `error_class` ∈ {`timeout`, `connect`, `http`, `bad_response`}.
- Cache-buster prefix `[req {uuid4}] ` is always prepended to prompts.
- Env vars: `PORT` (8080), `DATA_DIR` (`/data`), `SECRET_KEY` (optional), `LOG_LEVEL` (`info`).
- All Python commits run `pytest` first; commit messages use conventional prefixes (`feat:`, `test:`, `chore:`).

---

### Task 1: Fresh scaffold

Delete the failed build, create the new package skeleton and test harness.

**Files:**
- Delete: `bench/`, `frontend/`, `tests/`, `.pytest_cache/`
- Create: `pyproject.toml`, `bench/__init__.py`, `bench/config.py`, `bench/{storage,engine,adapters,api,reports}/__init__.py`, `tests/__init__.py`, `tests/test_scaffold.py`, `.gitignore` (update)

**Interfaces:**
- Consumes: nothing.
- Produces: `bench.config.Settings` dataclass with `data_dir: Path`, `port: int`, `secret_key: str | None`; `bench.config.load_settings() -> Settings` reading `DATA_DIR`, `PORT`, `SECRET_KEY` env vars.

- [ ] **Step 1: Delete the old build**

```bash
git rm -r bench frontend tests
rm -rf .pytest_cache bench frontend tests
```

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[project]
name = "inference-benchmark"
version = "2.0.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.110",
    "uvicorn[standard]>=0.29",
    "httpx>=0.27",
    "numpy>=1.26",
    "psutil>=5.9",
    "cryptography>=42",
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.23", "tiktoken>=0.7"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.setuptools.packages.find]
include = ["bench*"]

[tool.setuptools.package-data]
bench = ["data/workloads/*.json", "reports/vendor/*.js"]
```

- [ ] **Step 3: Write the failing scaffold test**

`tests/test_scaffold.py`:

```python
from pathlib import Path

from bench.config import load_settings


def test_load_settings_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.delenv("SECRET_KEY", raising=False)
    s = load_settings()
    assert s.data_dir == Path(tmp_path)
    assert s.port == 8080
    assert s.secret_key is None
```

- [ ] **Step 4: Run it to verify it fails**

Run: `pip install -e ".[dev]" && pytest tests/test_scaffold.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bench.config'`

- [ ] **Step 5: Create the package skeleton and `bench/config.py`**

Create empty `__init__.py` files: `bench/__init__.py`, `bench/storage/__init__.py`, `bench/engine/__init__.py`, `bench/adapters/__init__.py`, `bench/api/__init__.py`, `bench/reports/__init__.py`, `tests/__init__.py`.

`bench/config.py`:

```python
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    port: int
    secret_key: str | None


def load_settings() -> Settings:
    return Settings(
        data_dir=Path(os.environ.get("DATA_DIR", "/data")),
        port=int(os.environ.get("PORT", "8080")),
        secret_key=os.environ.get("SECRET_KEY") or None,
    )
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_scaffold.py -v`
Expected: PASS

- [ ] **Step 7: Update `.gitignore`**

Ensure these lines exist (append if missing):

```
__pycache__/
*.pyc
.pytest_cache/
node_modules/
frontend/dist/
.superpowers/
*.egg-info/
```

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "chore: fresh scaffold for simplified benchmark (v2 spec)"
```

---

### Task 2: Storage — encryption, schema, CRUD

**Files:**
- Create: `bench/storage/crypto.py`, `bench/storage/db.py`
- Test: `tests/test_storage.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `crypto.load_or_create_secret(data_dir: Path) -> bytes` (uses `SECRET_KEY` if passed, else `$DATA_DIR/.secret` mode 0600); `crypto.encrypt(secret: bytes, plaintext: str) -> str`; `crypto.decrypt(secret: bytes, token: str) -> str`.
  - `db.connect(db_path: Path) -> sqlite3.Connection` — WAL, foreign keys on, `row_factory=sqlite3.Row`, migrations applied via `PRAGMA user_version`.
  - Endpoint CRUD: `create_endpoint(db, data: dict) -> dict`, `list_endpoints(db) -> list[dict]`, `get_endpoint(db, endpoint_id: int) -> dict | None`, `update_endpoint(db, endpoint_id, data: dict) -> dict`, `delete_endpoint(db, endpoint_id)`. Endpoint dict keys mirror columns: `id,name,type,base_url,api_key_encrypted,default_model,verify_tls,supports_streaming,created_at`.
  - Test rows: `create_test(db, data: dict) -> dict`, `get_test(db, test_id) -> dict | None` (includes parsed `flags`, `verdict`, `settings` dicts), `list_tests(db, endpoint_id=None, model=None) -> list[dict]`, `finish_test(db, test_id, status: str, verdict: dict | None, flags: dict)`, `delete_test(db, test_id)`, `mark_running_tests_stopped(db) -> list[int]`, `active_test_id(db) -> int | None`, `set_flag(db, test_id, flag: str)`.
  - Step/request rows: `insert_step(db, step: dict)`, `list_steps(db, test_id) -> list[dict]`, `insert_request(db, req: dict)`, `list_requests(db, test_id) -> list[dict]`.

- [ ] **Step 1: Write failing crypto tests**

`tests/test_storage.py`:

```python
import sqlite3

from bench.storage import crypto, db


def test_encrypt_roundtrip(tmp_path):
    secret = crypto.load_or_create_secret(tmp_path)
    token = crypto.encrypt(secret, "sk-abc123")
    assert token != "sk-abc123"
    assert crypto.decrypt(secret, token) == "sk-abc123"


def test_secret_persisted(tmp_path):
    s1 = crypto.load_or_create_secret(tmp_path)
    s2 = crypto.load_or_create_secret(tmp_path)
    assert s1 == s2
    assert (tmp_path / ".secret").stat().st_mode & 0o777 == 0o600


def test_secret_env_override(tmp_path):
    s = crypto.load_or_create_secret(tmp_path, secret_key="my-passphrase")
    assert crypto.decrypt(s, crypto.encrypt(s, "x")) == "x"
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_storage.py -v`
Expected: FAIL with `ImportError` / `AttributeError`

- [ ] **Step 3: Implement `bench/storage/crypto.py`**

```python
import base64
import hashlib
import os
from pathlib import Path

from cryptography.fernet import Fernet


def load_or_create_secret(data_dir: Path, secret_key: str | None = None) -> bytes:
    if secret_key:
        digest = hashlib.sha256(secret_key.encode()).digest()
        return base64.urlsafe_b64encode(digest)
    path = data_dir / ".secret"
    if path.exists():
        return path.read_bytes()
    key = Fernet.generate_key()
    data_dir.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key)
    os.chmod(path, 0o600)
    return key


def encrypt(secret: bytes, plaintext: str) -> str:
    return Fernet(secret).encrypt(plaintext.encode()).decode()


def decrypt(secret: bytes, token: str) -> str:
    return Fernet(secret).decrypt(token.encode()).decode()
```

- [ ] **Step 4: Run crypto tests — expect PASS. Then write failing db tests**

Append to `tests/test_storage.py`:

```python
def _db(tmp_path):
    return db.connect(tmp_path / "benchmark.db")


def test_endpoint_crud(tmp_path):
    conn = _db(tmp_path)
    ep = db.create_endpoint(conn, {
        "name": "vllm", "type": "openai", "base_url": "http://x:8000/v1",
        "api_key_encrypted": "tok", "default_model": "llama", "verify_tls": 1,
    })
    assert ep["id"] == 1 and ep["supports_streaming"] is None
    db.update_endpoint(conn, 1, {"supports_streaming": 1, "default_model": "llama-70b"})
    assert db.get_endpoint(conn, 1)["default_model"] == "llama-70b"
    assert len(db.list_endpoints(conn)) == 1
    db.delete_endpoint(conn, 1)
    assert db.get_endpoint(conn, 1) is None


def test_test_lifecycle_and_cascade(tmp_path):
    conn = _db(tmp_path)
    ep = db.create_endpoint(conn, {"name": "e", "type": "openai", "base_url": "u"})
    t = db.create_test(conn, {
        "endpoint_id": ep["id"], "model": "m", "workload": "chat",
        "budget_ttft_ms": None, "budget_e2e_ms": 8000.0,
        "settings": {"max_concurrency": 128, "seed": 42},
    })
    assert t["status"] == "running"
    assert db.active_test_id(conn) == t["id"]
    db.insert_step(conn, {
        "test_id": t["id"], "concurrency": 1, "requests_completed": 20,
        "throughput_tps": 55.0, "ttft_p50_ms": 200.0, "ttft_p95_ms": 300.0,
        "e2e_p50_ms": 4000.0, "e2e_p95_ms": 5000.0, "error_count": 0,
        "started_at": "2026-08-02T10:00:00Z", "duration_s": 45.0,
    })
    db.insert_request(conn, {
        "test_id": t["id"], "concurrency": 1, "prompt_id": "chat-01",
        "t_send_wall": "2026-08-02T10:00:00Z", "ttft_ms": 200.0, "e2e_ms": 4100.0,
        "prompt_tokens": 500, "output_tokens": 280, "tokens_estimated": 0,
        "error_class": None, "error_detail": None,
    })
    db.finish_test(conn, t["id"], "completed",
                   verdict={"knee_concurrency": 1}, flags={"stopped_early": True})
    got = db.get_test(conn, t["id"])
    assert got["status"] == "completed"
    assert got["verdict"]["knee_concurrency"] == 1
    assert got["flags"]["stopped_early"] is True
    assert db.active_test_id(conn) is None
    assert len(db.list_steps(conn, t["id"])) == 1
    assert len(db.list_requests(conn, t["id"])) == 1
    db.delete_test(conn, t["id"])
    assert db.list_steps(conn, t["id"]) == []
    assert db.list_requests(conn, t["id"]) == []


def test_mark_running_stopped(tmp_path):
    conn = _db(tmp_path)
    ep = db.create_endpoint(conn, {"name": "e", "type": "openai", "base_url": "u"})
    t = db.create_test(conn, {"endpoint_id": ep["id"], "model": "m",
                              "workload": "chat", "settings": {}})
    assert db.mark_running_tests_stopped(conn) == [t["id"]]
    assert db.get_test(conn, t["id"])["status"] == "stopped"
```

- [ ] **Step 5: Run to verify db tests fail, then implement `bench/storage/db.py`**

```python
import json
import sqlite3
from pathlib import Path

MIGRATIONS = [
    """
    CREATE TABLE endpoints(
      id INTEGER PRIMARY KEY,
      name TEXT NOT NULL UNIQUE,
      type TEXT NOT NULL CHECK(type IN ('openai','asksage')),
      base_url TEXT NOT NULL,
      api_key_encrypted TEXT,
      default_model TEXT,
      verify_tls INTEGER NOT NULL DEFAULT 1,
      supports_streaming INTEGER,
      created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE tests(
      id INTEGER PRIMARY KEY,
      endpoint_id INTEGER NOT NULL REFERENCES endpoints(id),
      model TEXT NOT NULL,
      workload TEXT NOT NULL,
      budget_ttft_ms REAL,
      budget_e2e_ms REAL,
      settings_json TEXT NOT NULL DEFAULT '{}',
      status TEXT NOT NULL DEFAULT 'running'
        CHECK(status IN ('running','completed','stopped','failed')),
      flags_json TEXT NOT NULL DEFAULT '{}',
      verdict_json TEXT,
      error TEXT,
      started_at TEXT NOT NULL DEFAULT (datetime('now')),
      finished_at TEXT
    );
    CREATE TABLE steps(
      test_id INTEGER NOT NULL REFERENCES tests(id) ON DELETE CASCADE,
      concurrency INTEGER NOT NULL,
      requests_completed INTEGER NOT NULL,
      throughput_tps REAL,
      ttft_p50_ms REAL, ttft_p95_ms REAL,
      e2e_p50_ms REAL, e2e_p95_ms REAL,
      error_count INTEGER NOT NULL DEFAULT 0,
      started_at TEXT NOT NULL,
      duration_s REAL NOT NULL,
      PRIMARY KEY (test_id, concurrency)
    );
    CREATE TABLE requests(
      id INTEGER PRIMARY KEY,
      test_id INTEGER NOT NULL REFERENCES tests(id) ON DELETE CASCADE,
      concurrency INTEGER NOT NULL,
      prompt_id TEXT NOT NULL,
      t_send_wall TEXT NOT NULL,
      ttft_ms REAL,
      e2e_ms REAL,
      prompt_tokens INTEGER,
      output_tokens INTEGER,
      tokens_estimated INTEGER NOT NULL DEFAULT 0,
      error_class TEXT,
      error_detail TEXT
    );
    CREATE INDEX idx_requests_test_step ON requests(test_id, concurrency);
    """,
]


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for i, migration in enumerate(MIGRATIONS[version:], start=version + 1):
        conn.executescript(migration)
        conn.execute(f"PRAGMA user_version={i}")
        conn.commit()
    return conn


def _row(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


# --- endpoints ---

def create_endpoint(db: sqlite3.Connection, data: dict) -> dict:
    cur = db.execute(
        """INSERT INTO endpoints(name,type,base_url,api_key_encrypted,default_model,verify_tls)
           VALUES(:name,:type,:base_url,:api_key_encrypted,:default_model,:verify_tls)""",
        {"api_key_encrypted": None, "default_model": None, "verify_tls": 1, **data},
    )
    db.commit()
    return get_endpoint(db, cur.lastrowid)


def get_endpoint(db, endpoint_id: int) -> dict | None:
    return _row(db.execute("SELECT * FROM endpoints WHERE id=?", (endpoint_id,)).fetchone())


def list_endpoints(db) -> list[dict]:
    return [dict(r) for r in db.execute("SELECT * FROM endpoints ORDER BY name")]


def update_endpoint(db, endpoint_id: int, data: dict) -> dict:
    cols = ", ".join(f"{k}=:{k}" for k in data)
    db.execute(f"UPDATE endpoints SET {cols} WHERE id=:id", {**data, "id": endpoint_id})
    db.commit()
    return get_endpoint(db, endpoint_id)


def delete_endpoint(db, endpoint_id: int) -> None:
    db.execute("DELETE FROM endpoints WHERE id=?", (endpoint_id,))
    db.commit()


# --- tests ---

def create_test(db, data: dict) -> dict:
    cur = db.execute(
        """INSERT INTO tests(endpoint_id,model,workload,budget_ttft_ms,budget_e2e_ms,settings_json)
           VALUES(:endpoint_id,:model,:workload,:budget_ttft_ms,:budget_e2e_ms,:settings_json)""",
        {"budget_ttft_ms": None, "budget_e2e_ms": None, **data,
         "settings_json": json.dumps(data.get("settings", {}))},
    )
    db.commit()
    return get_test(db, cur.lastrowid)


def get_test(db, test_id: int) -> dict | None:
    row = _row(db.execute("SELECT * FROM tests WHERE id=?", (test_id,)).fetchone())
    if row is None:
        return None
    row["settings"] = json.loads(row.pop("settings_json") or "{}")
    row["flags"] = json.loads(row.pop("flags_json") or "{}")
    row["verdict"] = json.loads(row.pop("verdict_json")) if row.get("verdict_json") else None
    row.pop("verdict_json", None)
    return row


def list_tests(db, endpoint_id: int | None = None, model: str | None = None) -> list[dict]:
    q, args = "SELECT id FROM tests", []
    clauses = []
    if endpoint_id is not None:
        clauses.append("endpoint_id=?"); args.append(endpoint_id)
    if model:
        clauses.append("model=?"); args.append(model)
    if clauses:
        q += " WHERE " + " AND ".join(clauses)
    q += " ORDER BY started_at DESC, id DESC"
    return [get_test(db, r["id"]) for r in db.execute(q, args)]


def finish_test(db, test_id: int, status: str, verdict: dict | None, flags: dict) -> None:
    db.execute(
        """UPDATE tests SET status=?, verdict_json=?, flags_json=?,
           finished_at=datetime('now') WHERE id=?""",
        (status, json.dumps(verdict) if verdict else None, json.dumps(flags), test_id),
    )
    db.commit()


def set_flag(db, test_id: int, flag: str) -> None:
    t = get_test(db, test_id)
    flags = {**t["flags"], flag: True}
    db.execute("UPDATE tests SET flags_json=? WHERE id=?", (json.dumps(flags), test_id))
    db.commit()


def delete_test(db, test_id: int) -> None:
    db.execute("DELETE FROM tests WHERE id=?", (test_id,))
    db.commit()


def mark_running_tests_stopped(db) -> list[int]:
    ids = [r["id"] for r in db.execute("SELECT id FROM tests WHERE status='running'")]
    db.execute("UPDATE tests SET status='stopped', finished_at=datetime('now') "
               "WHERE status='running'")
    db.commit()
    return ids


def active_test_id(db) -> int | None:
    row = db.execute("SELECT id FROM tests WHERE status='running' LIMIT 1").fetchone()
    return row["id"] if row else None


# --- steps and requests ---

STEP_COLS = ("test_id", "concurrency", "requests_completed", "throughput_tps",
             "ttft_p50_ms", "ttft_p95_ms", "e2e_p50_ms", "e2e_p95_ms",
             "error_count", "started_at", "duration_s")


def insert_step(db, step: dict) -> None:
    cols = ",".join(STEP_COLS)
    ph = ",".join(f":{c}" for c in STEP_COLS)
    db.execute(f"INSERT INTO steps({cols}) VALUES({ph})",
               {c: step.get(c) for c in STEP_COLS})
    db.commit()


def list_steps(db, test_id: int) -> list[dict]:
    return [dict(r) for r in db.execute(
        "SELECT * FROM steps WHERE test_id=? ORDER BY concurrency", (test_id,))]


REQ_COLS = ("test_id", "concurrency", "prompt_id", "t_send_wall", "ttft_ms", "e2e_ms",
            "prompt_tokens", "output_tokens", "tokens_estimated",
            "error_class", "error_detail")


def insert_request(db, req: dict) -> None:
    cols = ",".join(REQ_COLS)
    ph = ",".join(f":{c}" for c in REQ_COLS)
    db.execute(f"INSERT INTO requests({cols}) VALUES({ph})",
               {c: req.get(c) for c in REQ_COLS})
    db.commit()


def list_requests(db, test_id: int) -> list[dict]:
    return [dict(r) for r in db.execute(
        "SELECT * FROM requests WHERE test_id=? ORDER BY id", (test_id,))]
```

- [ ] **Step 6: Run all storage tests**

Run: `pytest tests/test_storage.py -v`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add bench/storage tests/test_storage.py
git commit -m "feat: storage layer with encrypted keys, schema, CRUD"
```

---

### Task 3: Token counting and metrics

**Files:**
- Create: `bench/engine/tokens.py`, `bench/engine/metrics.py`, `bench/adapters/base.py`
- Test: `tests/test_metrics.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `bench.adapters.base.RequestResult` dataclass — the single per-request record used by adapters, engine, and metrics:
    ```python
    @dataclass
    class RequestResult:
        prompt_id: str
        t_send_wall: str          # ISO-8601 UTC, display only
        ttft_ms: float | None     # None for non-streaming or errors
        e2e_ms: float | None      # None only when the request errored before completion
        prompt_tokens: int | None
        output_tokens: int | None
        tokens_estimated: bool
        error_class: str | None   # timeout|connect|http|bad_response|None
        error_detail: str | None
    ```
    plus `RequestResult.ok` property (`error_class is None`).
  - `tokens.count_tokens(text: str) -> int` — tiktoken `o200k_base` if importable, else `max(1, len(text) // 4)`.
  - `metrics.percentile(values: Sequence[float], p: float) -> float` — numpy linear interpolation.
  - `metrics.aggregate_step(concurrency: int, results: list[RequestResult], duration_s: float, started_at: str) -> dict` — returns a `steps` row dict (keys = `db.STEP_COLS` minus `test_id`). Throughput = successful output tokens ÷ `duration_s`. Latency percentiles over successful requests only; `ttft_*` None when no successful request has a TTFT.

- [ ] **Step 1: Write failing tests**

`tests/test_metrics.py`:

```python
from bench.adapters.base import RequestResult
from bench.engine import metrics, tokens


def _res(ttft=200.0, e2e=1000.0, out=100, err=None):
    return RequestResult(
        prompt_id="p", t_send_wall="2026-08-02T10:00:00Z",
        ttft_ms=ttft, e2e_ms=e2e, prompt_tokens=500, output_tokens=out,
        tokens_estimated=False, error_class=err, error_detail=None,
    )


def test_count_tokens_positive():
    assert tokens.count_tokens("hello world, this is a test") >= 4


def test_percentile_linear_interpolation():
    assert metrics.percentile([10, 20, 30, 40], 50) == 25.0
    assert metrics.percentile([10, 20, 30, 40], 95) == 38.5


def test_aggregate_step_basic():
    results = [_res(ttft=100.0, e2e=1000.0, out=50),
               _res(ttft=300.0, e2e=3000.0, out=150),
               _res(ttft=None, e2e=None, out=None, err="timeout")]
    row = metrics.aggregate_step(4, results, duration_s=10.0,
                                 started_at="2026-08-02T10:00:00Z")
    assert row["concurrency"] == 4
    assert row["requests_completed"] == 2
    assert row["error_count"] == 1
    assert row["throughput_tps"] == 20.0          # (50+150)/10
    assert row["ttft_p50_ms"] == 200.0
    assert row["e2e_p50_ms"] == 2000.0


def test_aggregate_step_non_streaming():
    results = [_res(ttft=None, e2e=1000.0, out=50)]
    row = metrics.aggregate_step(1, results, 10.0, "2026-08-02T10:00:00Z")
    assert row["ttft_p50_ms"] is None and row["ttft_p95_ms"] is None
    assert row["e2e_p95_ms"] == 1000.0
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_metrics.py -v`
Expected: FAIL with import errors

- [ ] **Step 3: Implement**

`bench/adapters/base.py`:

```python
from dataclasses import dataclass


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
```

`bench/engine/tokens.py`:

```python
try:
    import tiktoken
    _enc = tiktoken.get_encoding("o200k_base")
except Exception:  # tiktoken missing or no cached BPE file
    _enc = None


def count_tokens(text: str) -> int:
    if _enc is not None:
        return max(1, len(_enc.encode(text)))
    return max(1, len(text) // 4)
```

`bench/engine/metrics.py`:

```python
from typing import Sequence

import numpy as np

from bench.adapters.base import RequestResult


def percentile(values: Sequence[float], p: float) -> float:
    return float(np.percentile(np.asarray(values, dtype=float), p))


def aggregate_step(concurrency: int, results: list[RequestResult],
                   duration_s: float, started_at: str) -> dict:
    ok = [r for r in results if r.ok]
    ttfts = [r.ttft_ms for r in ok if r.ttft_ms is not None]
    e2es = [r.e2e_ms for r in ok if r.e2e_ms is not None]
    out_tokens = sum(r.output_tokens or 0 for r in ok)
    return {
        "concurrency": concurrency,
        "requests_completed": len(ok),
        "throughput_tps": (out_tokens / duration_s) if duration_s > 0 else None,
        "ttft_p50_ms": percentile(ttfts, 50) if ttfts else None,
        "ttft_p95_ms": percentile(ttfts, 95) if ttfts else None,
        "e2e_p50_ms": percentile(e2es, 50) if e2es else None,
        "e2e_p95_ms": percentile(e2es, 95) if e2es else None,
        "error_count": len(results) - len(ok),
        "started_at": started_at,
        "duration_s": duration_s,
    }
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `pytest tests/test_metrics.py -v`

- [ ] **Step 5: Commit**

```bash
git add bench/engine/tokens.py bench/engine/metrics.py bench/adapters/base.py tests/test_metrics.py
git commit -m "feat: RequestResult, token counting, step aggregation"
```

---

### Task 4: Verdict — knee detection and budget interpolation

**Files:**
- Create: `bench/engine/verdict.py`
- Test: `tests/test_verdict.py`

**Interfaces:**
- Consumes: step row dicts from `metrics.aggregate_step` / `db.list_steps` (keys: `concurrency`, `throughput_tps`, `ttft_p95_ms`, `e2e_p95_ms`, …).
- Produces: `compute_verdict(steps: list[dict], budget_ttft_ms: float | None, budget_e2e_ms: float | None, streaming: bool, flags: dict) -> dict | None`. Returns `None` (verdict suppressed) when `len(steps) < 3` or `flags.get("client_saturated")`. Otherwise:
  ```python
  {
    "knee_concurrency": int,
    "sweet_zone": [int, int],            # knee's neighbors' concurrencies (clamped to measured range)
    "throughput_tps": float,             # at the knee
    "p95_latency_ms": float,             # at the knee: ttft_p95 if streaming else e2e_p95
    "latency_metric": "ttft" | "e2e",
    "budget": None | {
        "max_concurrency": float,        # interpolated, never > max measured
        "limited_by": "ttft" | "e2e",
        "met": bool,                     # False when even concurrency 1 misses budget
    },
  }
  ```
  Also exports `interpolate_budget(steps, field: str, budget: float) -> float | None` (None = not limited within measured range → caller uses max measured concurrency).

- [ ] **Step 1: Write failing tests**

`tests/test_verdict.py`:

```python
from bench.engine.verdict import compute_verdict, interpolate_budget


def _step(c, tps, ttft95=None, e2e95=None):
    return {"concurrency": c, "throughput_tps": tps,
            "ttft_p95_ms": ttft95, "e2e_p95_ms": e2e95}


STEPS = [
    _step(1, 100, ttft95=200, e2e95=2000),
    _step(2, 190, ttft95=250, e2e95=2500),
    _step(4, 340, ttft95=350, e2e95=3500),
    _step(8, 560, ttft95=600, e2e95=6000),
    _step(16, 610, ttft95=1200, e2e95=12000),   # gain 8.9% -> knee is 8
    _step(32, 620, ttft95=2600, e2e95=26000),
]


def test_knee_detection():
    v = compute_verdict(STEPS, None, None, streaming=True, flags={})
    assert v["knee_concurrency"] == 8
    assert v["sweet_zone"] == [4, 16]
    assert v["throughput_tps"] == 560
    assert v["p95_latency_ms"] == 600
    assert v["latency_metric"] == "ttft"
    assert v["budget"] is None


def test_all_gains_high_knee_is_last_step():
    steps = STEPS[:4]
    v = compute_verdict(steps, None, None, streaming=True, flags={})
    assert v["knee_concurrency"] == 8
    assert v["sweet_zone"] == [4, 8]   # clamped at measured max


def test_budget_interpolation():
    # budget 900 ms sits between steps 8 (600) and 16 (1200):
    # 8 + (900-600)*(16-8)/(1200-600) = 12.0
    c = interpolate_budget(STEPS, "ttft_p95_ms", 900.0)
    assert c == 12.0


def test_budget_within_all_steps_returns_none():
    assert interpolate_budget(STEPS, "ttft_p95_ms", 99999.0) is None


def test_budget_binding_metric_named():
    v = compute_verdict(STEPS, 900.0, 30000.0, streaming=True, flags={})
    assert v["budget"]["limited_by"] == "ttft"
    assert v["budget"]["max_concurrency"] == 12.0
    assert v["budget"]["met"] is True


def test_budget_unmet_at_concurrency_one():
    v = compute_verdict(STEPS, 100.0, None, streaming=True, flags={})
    assert v["budget"]["met"] is False


def test_suppressed_when_too_few_steps():
    assert compute_verdict(STEPS[:2], None, None, True, {}) is None


def test_suppressed_when_client_saturated():
    assert compute_verdict(STEPS, None, None, True, {"client_saturated": True}) is None


def test_non_streaming_uses_e2e():
    steps = [_step(1, 100, e2e95=2000), _step(2, 190, e2e95=2500),
             _step(4, 340, e2e95=3500)]
    v = compute_verdict(steps, None, 3000.0, streaming=False, flags={})
    assert v["latency_metric"] == "e2e"
    assert v["budget"]["limited_by"] == "e2e"
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_verdict.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `bench/engine/verdict.py`**

```python
GAIN_THRESHOLD = 0.10


def _knee_index(steps: list[dict]) -> int:
    """Index of the last step whose throughput gain over the previous step >= 10%."""
    knee = 0
    for i in range(1, len(steps)):
        prev, cur = steps[i - 1]["throughput_tps"], steps[i]["throughput_tps"]
        if prev and cur and (cur - prev) / prev >= GAIN_THRESHOLD:
            knee = i
    return knee


def interpolate_budget(steps: list[dict], field: str, budget: float) -> float | None:
    usable = [s for s in steps if s.get(field) is not None]
    if not usable:
        return None
    if usable[0][field] > budget:
        return 0.0
    for a, b in zip(usable, usable[1:]):
        if a[field] <= budget < b[field]:
            span = b[field] - a[field]
            frac = (budget - a[field]) / span if span > 0 else 0.0
            return round(a["concurrency"] + frac * (b["concurrency"] - a["concurrency"]), 1)
    return None  # never exceeded within measured range


def compute_verdict(steps: list[dict], budget_ttft_ms: float | None,
                    budget_e2e_ms: float | None, streaming: bool,
                    flags: dict) -> dict | None:
    if len(steps) < 3 or flags.get("client_saturated"):
        return None
    steps = sorted(steps, key=lambda s: s["concurrency"])
    k = _knee_index(steps)
    knee = steps[k]
    lat_field = "ttft_p95_ms" if streaming else "e2e_p95_ms"
    verdict = {
        "knee_concurrency": knee["concurrency"],
        "sweet_zone": [steps[max(0, k - 1)]["concurrency"],
                       steps[min(len(steps) - 1, k + 1)]["concurrency"]],
        "throughput_tps": knee["throughput_tps"],
        "p95_latency_ms": knee.get(lat_field),
        "latency_metric": "ttft" if streaming else "e2e",
        "budget": None,
    }
    budgets = []
    if budget_ttft_ms is not None and streaming:
        budgets.append(("ttft", "ttft_p95_ms", budget_ttft_ms))
    if budget_e2e_ms is not None:
        budgets.append(("e2e", "e2e_p95_ms", budget_e2e_ms))
    if budgets:
        max_measured = steps[-1]["concurrency"]
        results = []
        for name, field, budget in budgets:
            c = interpolate_budget(steps, field, budget)
            results.append((name, max_measured if c is None else c))
        limited_by, max_c = min(results, key=lambda r: r[1])
        verdict["budget"] = {"max_concurrency": max_c, "limited_by": limited_by,
                             "met": max_c > 0.0}
    return verdict
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `pytest tests/test_verdict.py -v`

- [ ] **Step 5: Commit**

```bash
git add bench/engine/verdict.py tests/test_verdict.py
git commit -m "feat: verdict with knee detection and budget interpolation"
```

---

### Task 5: Workload presets

**Files:**
- Create: `tools/corpus/seed.txt`, `tools/build_workloads.py`, `bench/data/workloads/` (generated JSON, committed), `bench/engine/workload.py`
- Test: `tests/test_workload.py`

**Interfaces:**
- Consumes: `bench.engine.tokens.count_tokens`.
- Produces:
  - Committed data files `bench/data/workloads/{chat,long_context,generation}.json`, each `{"preset": str, "version": "2.0.0", "prompts": [{"id": str, "text": str, "max_tokens": int, "expected_prompt_tokens": int}, ...]}` with ~20 prompts per preset. Token targets (±15%): chat ~500 in / `max_tokens` 400; long_context ~4000 in / 256; generation ~80 in / 1024.
  - `workload.load_prompts(preset: str) -> list[dict]` — raises `ValueError` on unknown preset.
  - `workload.PromptCycler(prompts, seed: int)` with `next() -> dict` returning a copy of the prompt whose `text` is prefixed `[req {uuid4}] ` (cache-buster); reshuffles each full cycle with `seed + cycle`.
  - `workload.PRESETS = ("chat", "long_context", "generation")`.

- [ ] **Step 1: Write `tools/corpus/seed.txt`**

Public-domain seed text (opening of *Pride and Prejudice*, tiled by the build script):

```
It is a truth universally acknowledged, that a single man in possession of a good
fortune, must be in want of a wife. However little known the feelings or views of
such a man may be on his first entering a neighbourhood, this truth is so well
fixed in the minds of the surrounding families, that he is considered as the
rightful property of some one or other of their daughters. My dear Mr. Bennet,
said his lady to him one day, have you heard that Netherfield Park is let at
last? Mr. Bennet replied that he had not. But it is, returned she; for Mrs. Long
has just been here, and she told me all about it. Mr. Bennet made no answer. Do
you not want to know who has taken it? cried his wife impatiently. You want to
tell me, and I have no objection to hearing it. This was invitation enough.
```

- [ ] **Step 2: Write `tools/build_workloads.py`**

```python
"""Deterministically generate the bundled workload presets. Output is committed."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bench.engine.tokens import count_tokens  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "bench" / "data" / "workloads"
SEED_TEXT = (ROOT / "tools" / "corpus" / "seed.txt").read_text().split()

QUESTIONS = [
    "Summarize the passage above in three sentences.",
    "List the named characters and what each one wants.",
    "What is the central claim of the passage? Answer briefly.",
    "Extract every proper noun from the passage above.",
]
GEN_TASKS = [
    "Write an exhaustive, thoroughly detailed essay about the economics of {}. "
    "Cover its history, its participants, its costs, its incentives, its failures, "
    "and its future. Continue until you have covered at least twelve distinct "
    "aspects, with several sentences on each; do not summarize and do not stop early.",
    "Write a very long, richly detailed story about {}. Include many scenes, "
    "several characters with distinct voices, extended dialogue, and full "
    "descriptions of every location. Keep writing until the story has at least "
    "ten separate scenes; do not stop early.",
]
TOPICS = ["marriage in the nineteenth century", "country estates", "letter writing",
          "inheritance law", "village society", "carriage travel", "social visits",
          "reputation", "courtship rituals", "family fortunes"]


def passage(target_tokens: int, offset: int) -> str:
    words, i = [], offset
    while count_tokens(" ".join(words)) < target_tokens:
        words.append(SEED_TEXT[i % len(SEED_TEXT)])
        i += 1
    return " ".join(words)


def build(preset: str, n: int, prompt_tokens: int, max_tokens: int, kind: str) -> dict:
    prompts = []
    for i in range(n):
        if kind == "analysis":
            text = passage(prompt_tokens, offset=i * 37) + "\n\n" + QUESTIONS[i % len(QUESTIONS)]
        else:
            text = GEN_TASKS[i % len(GEN_TASKS)].format(TOPICS[i % len(TOPICS)])
        prompts.append({"id": f"{preset}-{i:02d}", "text": text,
                        "max_tokens": max_tokens,
                        "expected_prompt_tokens": count_tokens(text)})
    return {"preset": preset, "version": "2.0.0", "prompts": prompts}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    specs = [("chat", 20, 500, 400, "analysis"),
             ("long_context", 20, 4000, 256, "analysis"),
             ("generation", 20, 80, 1024, "generation")]
    for preset, n, ptok, mtok, kind in specs:
        data = build(preset, n, ptok, mtok, kind)
        (OUT / f"{preset}.json").write_text(json.dumps(data, indent=1))
        print(f"{preset}: {n} prompts")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Generate the data**

Run: `python tools/build_workloads.py`
Expected: three JSON files under `bench/data/workloads/`. Spot-check one prompt's `expected_prompt_tokens` is within ±15% of target.

- [ ] **Step 4: Write failing loader tests**

`tests/test_workload.py`:

```python
import pytest

from bench.engine.workload import PRESETS, PromptCycler, load_prompts


def test_presets_load_with_expected_shapes():
    for preset in PRESETS:
        prompts = load_prompts(preset)
        assert len(prompts) >= 15
        for p in prompts:
            assert p["id"] and p["text"] and p["max_tokens"] > 0


def test_unknown_preset_raises():
    with pytest.raises(ValueError):
        load_prompts("nope")


def test_cycler_cache_buster_and_reproducibility():
    prompts = load_prompts("chat")
    a, b = PromptCycler(prompts, seed=7), PromptCycler(prompts, seed=7)
    seq_a = [a.next() for _ in range(30)]
    seq_b = [b.next() for _ in range(30)]
    assert [p["id"] for p in seq_a] == [p["id"] for p in seq_b]  # seeded order
    assert all(p["text"].startswith("[req ") for p in seq_a)      # cache buster
    texts = {p["text"] for p in seq_a}
    assert len(texts) == 30                                        # unique prefixes
```

- [ ] **Step 5: Run to verify failure, then implement `bench/engine/workload.py`**

```python
import json
import random
import uuid
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "workloads"
PRESETS = ("chat", "long_context", "generation")


def load_prompts(preset: str) -> list[dict]:
    if preset not in PRESETS:
        raise ValueError(f"unknown workload preset: {preset}")
    return json.loads((DATA_DIR / f"{preset}.json").read_text())["prompts"]


class PromptCycler:
    def __init__(self, prompts: list[dict], seed: int):
        self._prompts = list(prompts)
        self._seed = seed
        self._cycle = 0
        self._order: list[dict] = []
        self._i = 0

    def next(self) -> dict:
        if self._i >= len(self._order):
            rng = random.Random(self._seed + self._cycle)
            self._order = self._prompts[:]
            rng.shuffle(self._order)
            self._cycle += 1
            self._i = 0
        p = dict(self._order[self._i])
        self._i += 1
        p["text"] = f"[req {uuid.uuid4()}] " + p["text"]
        return p
```

- [ ] **Step 6: Run tests — expect PASS**

Run: `pytest tests/test_workload.py -v`

- [ ] **Step 7: Commit (including generated data)**

```bash
git add tools bench/data bench/engine/workload.py tests/test_workload.py
git commit -m "feat: bundled workload presets with seeded cycler and cache buster"
```

---

### Task 6: OpenAI-compatible adapter

**Files:**
- Create: `bench/adapters/openai.py`; extend `bench/adapters/base.py`
- Test: `tests/test_adapter_openai.py`, `tests/fixtures/openai_stream.txt`

**Interfaces:**
- Consumes: `RequestResult`, `tokens.count_tokens`.
- Produces (in `base.py`):
  - `class Adapter(Protocol)`: `async probe() -> dict` (`{reachable, auth_ok, models, supports_streaming, latency_ms, error}`), `async list_models() -> list[str]`, `async execute(text: str, model: str, max_tokens: int, temperature: float) -> RequestResult`, `async aclose()`.
  - `make_adapter(type_: str, base_url: str, api_key: str | None, verify_tls: bool, timeout_s: float, streaming: bool) -> Adapter` — dispatches to `OpenAIAdapter` / `AskSageAdapter` (AskSage added in Task 7; until then raise `ValueError` for it).
  - `classify_exception(exc) -> tuple[str, str]` mapping httpx exceptions → (`timeout`|`connect`|`bad_response`, detail).
- Produces (in `openai.py`): `OpenAIAdapter` — streaming SSE with `stream_options: {"include_usage": true}`; TTFT from first non-empty `choices[0].delta.content`; usage from final usage chunk, else tiktoken estimate with `tokens_estimated=True`; non-streaming mode when constructed with `streaming=False`; HTTP non-2xx → `error_class="http"`.

- [ ] **Step 1: Write the SSE fixture**

`tests/fixtures/openai_stream.txt` (blank line after each `data:` line, as in real SSE):

```
data: {"id":"c1","choices":[{"delta":{"role":"assistant"},"index":0}]}

data: {"id":"c1","choices":[{"delta":{"content":"Hello"},"index":0}]}

data: {"id":"c1","choices":[{"delta":{"content":" world"},"index":0}]}

data: {"id":"c1","choices":[{"delta":{},"finish_reason":"stop","index":0}]}

data: {"id":"c1","choices":[],"usage":{"prompt_tokens":12,"completion_tokens":2}}

data: [DONE]

```

- [ ] **Step 2: Write failing tests using httpx.MockTransport**

`tests/test_adapter_openai.py`:

```python
from pathlib import Path

import httpx
import pytest

from bench.adapters.openai import OpenAIAdapter

FIXTURE = (Path(__file__).parent / "fixtures" / "openai_stream.txt").read_text()


def _adapter(handler, streaming=True):
    transport = httpx.MockTransport(handler)
    return OpenAIAdapter("http://svc/v1", "key", verify_tls=True,
                         timeout_s=5.0, streaming=streaming,
                         transport=transport)


async def test_streaming_success_with_usage():
    def handler(request):
        assert request.headers["authorization"] == "Bearer key"
        return httpx.Response(200, text=FIXTURE,
                              headers={"content-type": "text/event-stream"})
    r = await _adapter(handler).execute("hi", "m", 100, 0.0)
    assert r.ok and r.ttft_ms is not None and r.e2e_ms is not None
    assert r.prompt_tokens == 12 and r.output_tokens == 2
    assert r.tokens_estimated is False


async def test_streaming_without_usage_estimates_tokens():
    body = "\n".join(line for line in FIXTURE.splitlines()
                     if '"usage"' not in line) + "\n"
    def handler(request):
        return httpx.Response(200, text=body,
                              headers={"content-type": "text/event-stream"})
    r = await _adapter(handler).execute("hi", "m", 100, 0.0)
    assert r.ok and r.tokens_estimated is True and r.output_tokens >= 1


async def test_non_streaming_mode():
    def handler(request):
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "Hello world"}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 2}})
    r = await _adapter(handler, streaming=False).execute("hi", "m", 100, 0.0)
    assert r.ok and r.ttft_ms is None and r.e2e_ms is not None
    assert r.output_tokens == 2


async def test_http_error_classified():
    def handler(request):
        return httpx.Response(401, json={"error": "bad key"})
    r = await _adapter(handler).execute("hi", "m", 100, 0.0)
    assert r.error_class == "http" and "401" in r.error_detail


async def test_timeout_classified():
    def handler(request):
        raise httpx.ReadTimeout("slow")
    r = await _adapter(handler).execute("hi", "m", 100, 0.0)
    assert r.error_class == "timeout"


async def test_malformed_stream_classified():
    def handler(request):
        return httpx.Response(200, text="data: {not json}\n\n",
                              headers={"content-type": "text/event-stream"})
    r = await _adapter(handler).execute("hi", "m", 100, 0.0)
    assert r.error_class == "bad_response"


async def test_list_models():
    def handler(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "a"}, {"id": "b"}]})
        return httpx.Response(404)
    assert await _adapter(handler).list_models() == ["a", "b"]
```

- [ ] **Step 3: Run to verify failure**

Run: `pytest tests/test_adapter_openai.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 4: Extend `bench/adapters/base.py`**

Append:

```python
import datetime
import time
from typing import Protocol

import httpx


class Adapter(Protocol):
    async def probe(self) -> dict: ...
    async def list_models(self) -> list[str]: ...
    async def execute(self, text: str, model: str, max_tokens: int,
                      temperature: float) -> "RequestResult": ...
    async def aclose(self) -> None: ...


def now_wall() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def classify_exception(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, httpx.TimeoutException):
        return "timeout", str(exc) or "request timed out"
    if isinstance(exc, (httpx.ConnectError, httpx.NetworkError)):
        return "connect", str(exc) or "connection failed"
    return "bad_response", f"{type(exc).__name__}: {exc}"


def make_adapter(type_: str, base_url: str, api_key: str | None, verify_tls: bool,
                 timeout_s: float, streaming: bool):
    from bench.adapters.openai import OpenAIAdapter
    if type_ == "openai":
        return OpenAIAdapter(base_url, api_key, verify_tls, timeout_s, streaming)
    if type_ == "asksage":
        from bench.adapters.asksage import AskSageAdapter
        return AskSageAdapter(base_url, api_key, verify_tls, timeout_s)
    raise ValueError(f"unknown adapter type: {type_}")
```

- [ ] **Step 5: Implement `bench/adapters/openai.py`**

```python
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
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(
            headers=headers, verify=verify_tls, timeout=timeout_s,
            transport=transport)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def list_models(self) -> list[str]:
        resp = await self._client.get(f"{self.base_url}/models")
        resp.raise_for_status()
        return [m["id"] for m in resp.json().get("data", [])]

    async def probe(self) -> dict:
        result = {"reachable": False, "auth_ok": False, "models": [],
                  "supports_streaming": False, "latency_ms": None, "error": None}
        start = time.perf_counter()
        try:
            try:
                result["models"] = await self.list_models()
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (401, 403):
                    result.update(reachable=True, error="authentication failed")
                    return result
            result["reachable"] = True
            r = await self.execute("Say OK.", result["models"][0] if result["models"]
                                   else "default", 1, 0.0)
            result["latency_ms"] = round((time.perf_counter() - start) * 1000, 1)
            if r.ok:
                result["auth_ok"] = True
                result["supports_streaming"] = self.streaming and r.ttft_ms is not None
            else:
                result["error"] = r.error_detail
        except Exception as e:
            result["error"] = classify_exception(e)[1]
        return result

    async def execute(self, text: str, model: str, max_tokens: int,
                      temperature: float) -> RequestResult:
        body = {"model": model, "messages": [{"role": "user", "content": text}],
                "max_tokens": max_tokens, "temperature": temperature}
        if self.streaming:
            body["stream"] = True
            body["stream_options"] = {"include_usage": True}
        wall = now_wall()
        t_send = time.perf_counter()
        try:
            if self.streaming:
                return await self._execute_stream(body, text, wall, t_send)
            return await self._execute_plain(body, text, wall, t_send)
        except Exception as e:
            cls, detail = classify_exception(e)
            return RequestResult(prompt_id="", t_send_wall=wall, ttft_ms=None,
                                 e2e_ms=None, prompt_tokens=None, output_tokens=None,
                                 tokens_estimated=False, error_class=cls,
                                 error_detail=detail)

    async def _execute_stream(self, body, text, wall, t_send) -> RequestResult:
        t_first = None
        chunks: list[str] = []
        usage = None
        async with self._client.stream(
                "POST", f"{self.base_url}/chat/completions", json=body) as resp:
            if resp.status_code >= 300:
                await resp.aread()
                return self._http_error(resp, wall)
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload.strip() == "[DONE]":
                    break
                chunk = json.loads(payload)  # malformed -> bad_response via classify
                if chunk.get("usage"):
                    usage = chunk["usage"]
                choices = chunk.get("choices") or []
                delta = (choices[0].get("delta") or {}) if choices else {}
                content = delta.get("content")
                if content:
                    if t_first is None:
                        t_first = time.perf_counter()
                    chunks.append(content)
        t_done = time.perf_counter()
        out_text = "".join(chunks)
        estimated = usage is None
        return RequestResult(
            prompt_id="", t_send_wall=wall,
            ttft_ms=round((t_first - t_send) * 1000, 2) if t_first else None,
            e2e_ms=round((t_done - t_send) * 1000, 2),
            prompt_tokens=usage["prompt_tokens"] if usage else count_tokens(text),
            output_tokens=(usage["completion_tokens"] if usage
                           else count_tokens(out_text) if out_text else 0),
            tokens_estimated=estimated, error_class=None, error_detail=None)

    async def _execute_plain(self, body, text, wall, t_send) -> RequestResult:
        resp = await self._client.post(f"{self.base_url}/chat/completions", json=body)
        if resp.status_code >= 300:
            return self._http_error(resp, wall)
        t_done = time.perf_counter()
        data = resp.json()
        out_text = data["choices"][0]["message"]["content"] or ""
        usage = data.get("usage")
        return RequestResult(
            prompt_id="", t_send_wall=wall, ttft_ms=None,
            e2e_ms=round((t_done - t_send) * 1000, 2),
            prompt_tokens=usage["prompt_tokens"] if usage else count_tokens(text),
            output_tokens=(usage["completion_tokens"] if usage
                           else count_tokens(out_text)),
            tokens_estimated=usage is None, error_class=None, error_detail=None)

    def _http_error(self, resp, wall) -> RequestResult:
        return RequestResult(prompt_id="", t_send_wall=wall, ttft_ms=None,
                             e2e_ms=None, prompt_tokens=None, output_tokens=None,
                             tokens_estimated=False, error_class="http",
                             error_detail=f"HTTP {resp.status_code}: {resp.text[:200]}")
```

- [ ] **Step 6: Run tests — expect PASS**

Run: `pytest tests/test_adapter_openai.py -v`

- [ ] **Step 7: Commit**

```bash
git add bench/adapters tests/test_adapter_openai.py tests/fixtures
git commit -m "feat: OpenAI-compatible adapter with streaming TTFT and usage fallback"
```

---

### Task 7: AskSage adapter

**Files:**
- Create: `bench/adapters/asksage.py`
- Test: `tests/test_adapter_asksage.py`

**Interfaces:**
- Consumes: `RequestResult`, `classify_exception`, `now_wall`, `count_tokens`; registered in `make_adapter` (already wired in Task 6).
- Produces: `AskSageAdapter(base_url, api_key, verify_tls, timeout_s, transport=None)` — non-streaming: `ttft_ms` always None; `POST {base_url}/query` with header `x-access-tokens`, body `{"message": text, "model": model, "temperature": t, "dataset": "none", "live": 0}`; output text from response `message` field; HTTP 200 with body `status != 200` → `error_class="http"`; token counts always tiktoken-estimated; `list_models()` via `POST {base_url}/get-models` (empty list on failure); `probe()` same shape as OpenAI's with `supports_streaming` always False.

- [ ] **Step 1: Write failing tests**

`tests/test_adapter_asksage.py`:

```python
import httpx

from bench.adapters.asksage import AskSageAdapter


def _adapter(handler):
    return AskSageAdapter("http://sage/server", "tok", verify_tls=True,
                          timeout_s=5.0, transport=httpx.MockTransport(handler))


async def test_query_success():
    def handler(request):
        assert request.headers["x-access-tokens"] == "tok"
        assert request.url.path.endswith("/query")
        return httpx.Response(200, json={"status": 200, "message": "Hello there"})
    r = await _adapter(handler).execute("hi", "gpt-4o", 100, 0.0)
    assert r.ok and r.ttft_ms is None and r.e2e_ms is not None
    assert r.output_tokens >= 1 and r.tokens_estimated is True


async def test_body_status_error():
    def handler(request):
        return httpx.Response(200, json={"status": 500, "response": "model overloaded"})
    r = await _adapter(handler).execute("hi", "m", 100, 0.0)
    assert r.error_class == "http" and "500" in r.error_detail


async def test_http_error():
    def handler(request):
        return httpx.Response(403, text="forbidden")
    r = await _adapter(handler).execute("hi", "m", 100, 0.0)
    assert r.error_class == "http"


async def test_get_models_failure_returns_empty():
    def handler(request):
        return httpx.Response(404)
    assert await _adapter(handler).list_models() == []


async def test_probe_reports_non_streaming():
    def handler(request):
        if request.url.path.endswith("/get-models"):
            return httpx.Response(200, json={"response": ["gpt-4o"]})
        return httpx.Response(200, json={"status": 200, "message": "OK"})
    p = await _adapter(handler).probe()
    assert p["reachable"] and p["auth_ok"] and p["supports_streaming"] is False
    assert p["models"] == ["gpt-4o"]
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_adapter_asksage.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `bench/adapters/asksage.py`**

```python
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
            resp = await self._client.post(f"{self.base_url}/get-models", json={})
            resp.raise_for_status()
            data = resp.json()
            models = data.get("response") or data.get("models") or []
            return [str(m) for m in models]
        except Exception:
            return []

    async def probe(self) -> dict:
        result = {"reachable": False, "auth_ok": False, "models": [],
                  "supports_streaming": False, "latency_ms": None, "error": None}
        start = time.perf_counter()
        try:
            result["models"] = await self.list_models()
            r = await self.execute("Say OK.",
                                   result["models"][0] if result["models"] else "gpt-4o",
                                   1, 0.0)
            result["latency_ms"] = round((time.perf_counter() - start) * 1000, 1)
            result["reachable"] = True
            if r.ok:
                result["auth_ok"] = True
            else:
                result["error"] = r.error_detail
        except Exception as e:
            result["error"] = classify_exception(e)[1]
        return result

    async def execute(self, text: str, model: str, max_tokens: int,
                      temperature: float) -> RequestResult:
        wall = now_wall()
        t_send = time.perf_counter()
        try:
            resp = await self._client.post(f"{self.base_url}/query", json={
                "message": text, "model": model, "temperature": temperature,
                "dataset": "none", "live": 0})
            t_done = time.perf_counter()
            if resp.status_code >= 300:
                return self._err(wall, "http",
                                 f"HTTP {resp.status_code}: {resp.text[:200]}")
            data = resp.json()
            if data.get("status") not in (None, 200):
                return self._err(wall, "http",
                                 f"body status {data.get('status')}: "
                                 f"{str(data.get('response'))[:200]}")
            out_text = data.get("message") or ""
            return RequestResult(
                prompt_id="", t_send_wall=wall, ttft_ms=None,
                e2e_ms=round((t_done - t_send) * 1000, 2),
                prompt_tokens=count_tokens(text),
                output_tokens=count_tokens(out_text) if out_text else 0,
                tokens_estimated=True, error_class=None, error_detail=None)
        except Exception as e:
            cls, detail = classify_exception(e)
            return self._err(wall, cls, detail)

    def _err(self, wall: str, cls: str, detail: str) -> RequestResult:
        return RequestResult(prompt_id="", t_send_wall=wall, ttft_ms=None,
                             e2e_ms=None, prompt_tokens=None, output_tokens=None,
                             tokens_estimated=False, error_class=cls,
                             error_detail=detail)
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `pytest tests/test_adapter_asksage.py -v`

- [ ] **Step 5: Commit**

```bash
git add bench/adapters/asksage.py tests/test_adapter_asksage.py
git commit -m "feat: AskSage adapter (non-streaming, E2E-only metrics)"
```

---

### Task 8: Mock inference server

**Files:**
- Create: `tools/mockserver/__init__.py`, `tools/mockserver/app.py`
- Test: `tests/test_mockserver.py`

**Interfaces:**
- Consumes: nothing from `bench` (standalone FastAPI app).
- Produces: `tools.mockserver.app.create_app(ttft_ms=250.0, tps=40.0, output_tokens=64, error_rate=0.0) -> FastAPI`, plus CLI `python -m tools.mockserver.app --port 9000 --ttft-ms 250 --tps 40 --output-tokens 64 --error-rate 0`. Serves both dialects:
  - `POST /v1/chat/completions` — streaming SSE (respects `stream: true`) or plain JSON, both with real `usage`; honors `max_tokens` as a cap on output tokens.
  - `GET /v1/models` — `{"data": [{"id": "mock-model"}]}`.
  - `POST /query`, `POST /get-models` — AskSage dialect, output delayed by the full generation time.
  - Prompt directives override per request: a prompt containing `@@ttft=100;tps=200;tokens=32;error=1@@` uses those values (`error=1` forces HTTP 500).
- Later consumers: engine integration test (Task 9), API tests (Tasks 10–11), container e2e (Task 16). **The `--tps` rate is per-request generation speed; total throughput scales with concurrency until you cap it — that linearity is what the integration tests rely on.**

- [ ] **Step 1: Write failing tests**

`tests/test_mockserver.py`:

```python
import json
import time

import httpx

from tools.mockserver.app import create_app


def _client(**kw):
    app = create_app(**kw)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url="http://mock")


async def test_openai_plain_timing_and_usage():
    async with _client(ttft_ms=100, tps=100, output_tokens=10) as c:
        start = time.perf_counter()
        resp = await c.post("/v1/chat/completions", json={
            "model": "m", "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 50})
        elapsed = time.perf_counter() - start
        data = resp.json()
        assert data["usage"]["completion_tokens"] == 10
        assert data["choices"][0]["message"]["content"]
        assert elapsed >= 0.1 + 10 / 100 - 0.02  # ttft + gen time


async def test_openai_streaming_shape():
    async with _client(ttft_ms=10, tps=1000, output_tokens=5) as c:
        async with c.stream("POST", "/v1/chat/completions", json={
                "model": "m", "messages": [{"role": "user", "content": "hi"}],
                "stream": True}) as resp:
            lines = [l async for l in resp.aiter_lines() if l.startswith("data: ")]
    assert lines[-1] == "data: [DONE]"
    assert any('"usage"' in l for l in lines)
    content = [l for l in lines if '"content"' in l]
    assert len(content) == 5


async def test_prompt_directives_override():
    async with _client(ttft_ms=1, tps=10000, output_tokens=5) as c:
        resp = await c.post("/v1/chat/completions", json={
            "model": "m",
            "messages": [{"role": "user", "content": "@@tokens=3@@ hi"}]})
        assert resp.json()["usage"]["completion_tokens"] == 3
        resp = await c.post("/v1/chat/completions", json={
            "model": "m", "messages": [{"role": "user", "content": "@@error=1@@ hi"}]})
        assert resp.status_code == 500


async def test_asksage_dialect():
    async with _client(ttft_ms=1, tps=10000, output_tokens=5) as c:
        resp = await c.post("/query", json={"message": "hi", "model": "m"})
        data = resp.json()
        assert data["status"] == 200 and data["message"]
        resp = await c.post("/get-models", json={})
        assert resp.json()["response"] == ["mock-model"]
```

- [ ] **Step 2: Run to verify failure, then implement `tools/mockserver/app.py`**

Create empty `tools/mockserver/__init__.py` and `tools/__init__.py`, then:

```python
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
    cfg = dict(defaults)
    m = DIRECTIVE.search(text or "")
    if m:
        for part in m.group(1).split(";"):
            k, _, v = part.partition("=")
            k = k.strip()
            if k == "ttft":
                cfg["ttft_ms"] = float(v)
            elif k == "tps":
                cfg["tps"] = float(v)
            elif k == "tokens":
                cfg["output_tokens"] = int(v)
            elif k == "error":
                cfg["force_error"] = v.strip() == "1"
    return cfg


def create_app(ttft_ms: float = 250.0, tps: float = 40.0,
               output_tokens: int = 64, error_rate: float = 0.0) -> FastAPI:
    app = FastAPI()
    defaults = {"ttft_ms": ttft_ms, "tps": tps, "output_tokens": output_tokens,
                "force_error": False}

    def _words(n: int) -> list[str]:
        return [WORDS[i % len(WORDS)] + (" " if i < n - 1 else "") for i in range(n)]

    def _should_error(cfg) -> bool:
        return cfg["force_error"] or (error_rate > 0 and random.random() < error_rate)

    @app.get("/v1/models")
    async def models():
        return {"data": [{"id": "mock-model"}]}

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        body = await request.json()
        text = body["messages"][-1]["content"]
        cfg = _parse_directives(text, defaults)
        n = min(cfg["output_tokens"], body.get("max_tokens", 10**9))
        if _should_error(cfg):
            return JSONResponse({"error": "mock error"}, status_code=500)
        prompt_tokens = max(1, len(text) // 4)
        usage = {"prompt_tokens": prompt_tokens, "completion_tokens": n}
        if body.get("stream"):
            async def gen():
                await asyncio.sleep(cfg["ttft_ms"] / 1000)
                for w in _words(n):
                    yield ("data: " + json.dumps(
                        {"choices": [{"delta": {"content": w}, "index": 0}]}) + "\n\n")
                    await asyncio.sleep(1 / cfg["tps"])
                yield "data: " + json.dumps({"choices": [], "usage": usage}) + "\n\n"
                yield "data: [DONE]\n\n"
            return StreamingResponse(gen(), media_type="text/event-stream")
        await asyncio.sleep(cfg["ttft_ms"] / 1000 + n / cfg["tps"])
        return {"choices": [{"message": {"content": "".join(_words(n))}}],
                "usage": usage}

    @app.post("/query")
    async def query(request: Request):
        body = await request.json()
        cfg = _parse_directives(body.get("message", ""), defaults)
        if _should_error(cfg):
            return {"status": 500, "response": "mock error"}
        n = cfg["output_tokens"]
        await asyncio.sleep(cfg["ttft_ms"] / 1000 + n / cfg["tps"])
        return {"status": 200, "message": "".join(_words(n))}

    @app.post("/get-models")
    async def get_models():
        return {"response": ["mock-model"]}

    return app


if __name__ == "__main__":
    import uvicorn
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=9000)
    p.add_argument("--ttft-ms", type=float, default=250.0)
    p.add_argument("--tps", type=float, default=40.0)
    p.add_argument("--output-tokens", type=int, default=64)
    p.add_argument("--error-rate", type=float, default=0.0)
    args = p.parse_args()
    uvicorn.run(create_app(args.ttft_ms, args.tps, args.output_tokens,
                           args.error_rate),
                host="0.0.0.0", port=args.port, log_level="warning")
```

- [ ] **Step 3: Run tests — expect PASS**

Run: `pytest tests/test_mockserver.py -v`

- [ ] **Step 4: Commit**

```bash
git add tools/mockserver tools/__init__.py tests/test_mockserver.py
git commit -m "feat: mock inference server speaking both dialects"
```

---

### Task 9: Sweep engine

**Files:**
- Create: `bench/engine/sweep.py`, `bench/engine/saturation.py`
- Test: `tests/test_sweep.py`

**Interfaces:**
- Consumes: `Adapter.execute`, `PromptCycler`, `metrics.aggregate_step`, `verdict.compute_verdict`, all `db.*` functions.
- Produces:
  - `sweep.SweepConfig` dataclass (all defaults per spec):
    ```python
    @dataclass
    class SweepConfig:
        max_concurrency: int = 128
        dwell_s: float = 45.0
        min_requests: int = 20
        warmup_requests: int = 3
        timeout_s: float = 180.0
        temperature: float = 0.0
        seed: int = 0
        workload: str = "chat"
        budget_ttft_ms: float | None = None
        budget_e2e_ms: float | None = None
    ```
  - `async sweep.run_sweep(conn, test_id: int, adapter, model: str, streaming: bool, cfg: SweepConfig, publish: Callable[[str, dict], None], stop_event: asyncio.Event) -> None` — runs the whole sweep, writes steps/requests/verdict to the db, publishes `tick` / `step` / `flag` / `status` events (spec §6 shapes), never raises (failures → status `failed` with `error` set on the row).
  - `saturation.SaturationMonitor` — `start()` creates a 1 s sampler task; `saturated: bool` property; loop-lag > 100 ms or CPU > 90% for 5 consecutive samples trips it; `stop()`.
  - Early-stop rules exactly as Global Constraints; user stop via `stop_event` → status `stopped`.
- Note: `publish` is threadsafe-agnostic — it is called from the engine's event loop; Task 11 supplies one that fans out to WebSocket queues.

- [ ] **Step 1: Write failing engine tests (fake adapter — fast, no sleeps beyond ms)**

`tests/test_sweep.py`:

```python
import asyncio

from bench.adapters.base import RequestResult, now_wall
from bench.engine.sweep import SweepConfig, run_sweep
from bench.storage import db


class FakeAdapter:
    """Concurrency-aware fake: per-request rate degrades past `knee`."""

    def __init__(self, knee=4, fail_at=None):
        self.knee = knee
        self.fail_at = fail_at
        self.in_flight = 0

    async def execute(self, text, model, max_tokens, temperature) -> RequestResult:
        self.in_flight += 1
        c = self.in_flight
        try:
            if self.fail_at and c >= self.fail_at:
                return RequestResult("", now_wall(), None, None, None, None,
                                     False, "timeout", "boom")
            slowdown = max(1.0, c / self.knee)
            await asyncio.sleep(0.005 * slowdown)
            return RequestResult("p", now_wall(), 5.0 * slowdown,
                                 5.0 * slowdown + 10.0, 100, 50, False, None, None)
        finally:
            self.in_flight -= 1


def _fast_cfg(**kw):
    return SweepConfig(max_concurrency=kw.pop("max_concurrency", 16),
                       dwell_s=0.05, min_requests=5, warmup_requests=1, **kw)


def _setup(tmp_path):
    conn = db.connect(tmp_path / "b.db")
    ep = db.create_endpoint(conn, {"name": "e", "type": "openai", "base_url": "u"})
    t = db.create_test(conn, {"endpoint_id": ep["id"], "model": "m",
                              "workload": "chat", "settings": {}})
    return conn, t["id"]


async def test_sweep_completes_with_steps_and_verdict(tmp_path):
    conn, tid = _setup(tmp_path)
    events = []
    await run_sweep(conn, tid, FakeAdapter(knee=4), "m", True, _fast_cfg(),
                    lambda kind, data: events.append((kind, data)), asyncio.Event())
    t = db.get_test(conn, tid)
    assert t["status"] == "completed"
    steps = db.list_steps(conn, tid)
    assert len(steps) >= 3
    assert steps[0]["concurrency"] == 1
    assert t["verdict"] is not None
    assert db.list_requests(conn, tid)          # ground truth rows written
    kinds = [k for k, _ in events]
    assert "step" in kinds and kinds[-1] == "status"
    assert events[-1][1]["status"] == "completed"


async def test_sweep_stops_early_on_flat_throughput(tmp_path):
    conn, tid = _setup(tmp_path)
    await run_sweep(conn, tid, FakeAdapter(knee=2), "m", True,
                    _fast_cfg(max_concurrency=128),
                    lambda *a: None, asyncio.Event())
    steps = db.list_steps(conn, tid)
    assert steps[-1]["concurrency"] < 128       # early stop fired
    assert db.get_test(conn, tid)["flags"].get("stopped_early") is True


async def test_sweep_fail_fast_when_first_step_all_errors(tmp_path):
    conn, tid = _setup(tmp_path)
    await run_sweep(conn, tid, FakeAdapter(fail_at=1), "m", True, _fast_cfg(),
                    lambda *a: None, asyncio.Event())
    t = db.get_test(conn, tid)
    assert t["status"] == "failed"
    assert "boom" in (t["error"] or "")


async def test_user_stop_keeps_steps(tmp_path):
    conn, tid = _setup(tmp_path)
    stop = asyncio.Event()

    def publisher(kind, data):
        if kind == "step" and data["concurrency"] >= 2:
            stop.set()

    await run_sweep(conn, tid, FakeAdapter(knee=8), "m", True,
                    _fast_cfg(max_concurrency=128), publisher, stop)
    t = db.get_test(conn, tid)
    assert t["status"] == "stopped"
    assert len(db.list_steps(conn, tid)) >= 2
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_sweep.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement `bench/engine/saturation.py`**

```python
import asyncio
import time

import psutil


class SaturationMonitor:
    def __init__(self, lag_threshold_ms: float = 100.0, cpu_threshold: float = 90.0,
                 consecutive: int = 5):
        self.lag_threshold_ms = lag_threshold_ms
        self.cpu_threshold = cpu_threshold
        self.consecutive = consecutive
        self.saturated = False
        self._hits = 0
        self._task: asyncio.Task | None = None
        self._proc = psutil.Process()

    def start(self) -> None:
        self._proc.cpu_percent(None)  # prime
        self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        while True:
            start = time.perf_counter()
            await asyncio.sleep(1.0)
            lag_ms = (time.perf_counter() - start - 1.0) * 1000
            cpu = self._proc.cpu_percent(None)
            if lag_ms > self.lag_threshold_ms or cpu > self.cpu_threshold:
                self._hits += 1
                if self._hits >= self.consecutive:
                    self.saturated = True
            else:
                self._hits = 0
```

- [ ] **Step 4: Implement `bench/engine/sweep.py`**

```python
import asyncio
import time
from dataclasses import dataclass
from typing import Callable

from bench.adapters.base import now_wall
from bench.engine import metrics
from bench.engine.saturation import SaturationMonitor
from bench.engine.verdict import compute_verdict
from bench.engine.workload import PromptCycler, load_prompts
from bench.storage import db

GAIN_THRESHOLD = 0.10
ERROR_RATE_LIMIT = 0.10
LATENCY_BLOWUP = 5.0
BUDGET_BLOWN = 2.0


@dataclass
class SweepConfig:
    max_concurrency: int = 128
    dwell_s: float = 45.0
    min_requests: int = 20
    warmup_requests: int = 3
    timeout_s: float = 180.0
    temperature: float = 0.0
    seed: int = 0
    workload: str = "chat"
    budget_ttft_ms: float | None = None
    budget_e2e_ms: float | None = None


async def _run_step(conn, test_id, adapter, model, cycler, concurrency, cfg,
                    streaming, publish, stop_event):
    results = []
    done_flag = asyncio.Event()
    started_wall = now_wall()
    start = time.perf_counter()

    async def worker():
        while not done_flag.is_set() and not stop_event.is_set():
            p = cycler.next()
            r = await adapter.execute(p["text"], model, p["max_tokens"],
                                      cfg.temperature)
            r.prompt_id = p["id"]
            results.append(r)
            db.insert_request(conn, r.to_row(test_id, concurrency))
            elapsed = time.perf_counter() - start
            if elapsed >= cfg.dwell_s and len(results) >= cfg.min_requests:
                done_flag.set()

    async def ticker():
        while not done_flag.is_set() and not stop_event.is_set():
            await asyncio.sleep(1.0)
            ok = [r for r in results if r.ok]
            lat = [r.ttft_ms if streaming else r.e2e_ms for r in ok]
            lat = [x for x in lat if x is not None]
            elapsed = time.perf_counter() - start
            frac_time = elapsed / cfg.dwell_s if cfg.dwell_s else 1.0
            frac_reqs = (len(results) / cfg.min_requests
                         if cfg.min_requests else 1.0)
            publish("tick", {
                "concurrency": concurrency, "requests_done": len(results),
                # step ends when BOTH minimums are met -> progress is the slower one
                "step_pct": min(99, int(100 * min(frac_time, frac_reqs))),
                "tps_now": round(sum(r.output_tokens or 0 for r in ok) / elapsed, 1)
                if elapsed > 0 else 0,
                "p95_latency_now_ms": round(metrics.percentile(lat, 95), 1)
                if lat else None,
                "errors": len(results) - len(ok),
                "elapsed_s": int(elapsed)})

    tick_task = asyncio.create_task(ticker())
    workers = [asyncio.create_task(worker()) for _ in range(concurrency)]
    await asyncio.gather(*workers)           # workers drain naturally
    tick_task.cancel()
    duration = time.perf_counter() - start
    return metrics.aggregate_step(concurrency, results, duration, started_wall)


async def run_sweep(conn, test_id: int, adapter, model: str, streaming: bool,
                    cfg: SweepConfig, publish: Callable[[str, dict], None],
                    stop_event: asyncio.Event) -> None:
    flags: dict = {}
    monitor = SaturationMonitor()
    try:
        prompts = load_prompts(cfg.workload)
        cycler = PromptCycler(prompts, cfg.seed)
        monitor.start()

        for _ in range(cfg.warmup_requests):          # warmup, discarded
            p = cycler.next()
            await adapter.execute(p["text"], model, p["max_tokens"], cfg.temperature)

        steps: list[dict] = []
        baseline_p95 = None
        flat_count = 0
        concurrency = 1
        while concurrency <= cfg.max_concurrency and not stop_event.is_set():
            step = await _run_step(conn, test_id, adapter, model, cycler,
                                   concurrency, cfg, streaming, publish, stop_event)
            if stop_event.is_set() and step["requests_completed"] == 0:
                break
            db.insert_step(conn, {**step, "test_id": test_id})
            steps.append(step)
            publish("step", step)

            if monitor.saturated and not flags.get("client_saturated"):
                flags["client_saturated"] = True
                db.set_flag(conn, test_id, "client_saturated")
                publish("flag", {"flag": "client_saturated"})

            # fail fast: first step produced nothing but errors
            if concurrency == 1 and step["requests_completed"] == 0:
                reqs = db.list_requests(conn, test_id)
                detail = next((r["error_detail"] for r in reversed(reqs)
                               if r["error_detail"]), "all requests failed")
                conn.execute("UPDATE tests SET error=? WHERE id=?",
                             (detail, test_id))
                db.finish_test(conn, test_id, "failed", None, flags)
                publish("status", {"status": "failed", "verdict": None})
                return

            lat_key = "ttft_p95_ms" if streaming else "e2e_p95_ms"
            if concurrency == 1:
                baseline_p95 = step.get(lat_key)

            stop_reason = None
            total = step["requests_completed"] + step["error_count"]
            if total and step["error_count"] / total > ERROR_RATE_LIMIT:
                stop_reason = "error_rate"
            if len(steps) >= 2:
                prev, cur = steps[-2]["throughput_tps"], steps[-1]["throughput_tps"]
                if prev and cur is not None and (cur - prev) / prev < GAIN_THRESHOLD:
                    flat_count += 1
                else:
                    flat_count = 0
                if flat_count >= 2:
                    stop_reason = stop_reason or "flat_throughput"
            p95 = step.get(lat_key)
            if baseline_p95 and p95 and p95 > LATENCY_BLOWUP * baseline_p95:
                stop_reason = stop_reason or "latency_blowup"
            for budget, key in ((cfg.budget_ttft_ms, "ttft_p95_ms"),
                                (cfg.budget_e2e_ms, "e2e_p95_ms")):
                if budget and step.get(key) and step[key] > BUDGET_BLOWN * budget:
                    stop_reason = stop_reason or "budget_blown"
            if stop_reason:
                flags["stopped_early"] = True
                break
            concurrency *= 2

        status = "stopped" if stop_event.is_set() else "completed"
        step_rows = db.list_steps(conn, test_id)
        if any(r["tokens_estimated"] for r in db.list_requests(conn, test_id)):
            flags["tokens_estimated"] = True
        verdict = compute_verdict(step_rows, cfg.budget_ttft_ms, cfg.budget_e2e_ms,
                                  streaming, flags)
        db.finish_test(conn, test_id, status, verdict, flags)
        publish("status", {"status": status, "verdict": verdict})
    except Exception as e:  # engine bug or unexpected adapter crash
        conn.execute("UPDATE tests SET error=? WHERE id=?", (str(e)[:500], test_id))
        db.finish_test(conn, test_id, "failed", None, flags)
        publish("status", {"status": "failed", "verdict": None})
    finally:
        monitor.stop()
        await adapter.aclose()
```

Note: `FakeAdapter` in tests has no `aclose`; add a no-op `async def aclose(self): pass` to it if the first run errors — or guard with `getattr`. Use the guard: replace `await adapter.aclose()` with:

```python
        close = getattr(adapter, "aclose", None)
        if close:
            await close()
```

- [ ] **Step 5: Run tests — expect PASS**

Run: `pytest tests/test_sweep.py -v`
Expected: all PASS (whole file should run in a few seconds — dwell is 0.05 s)

- [ ] **Step 6: Add the mock-server integration test (accuracy guard)**

Append to `tests/test_sweep.py`:

```python
import httpx

from bench.adapters.openai import OpenAIAdapter
from tools.mockserver.app import create_app as create_mock


async def test_integration_against_mock_within_tolerance(tmp_path):
    """Spec §9.3: measured TTFT and per-request behavior within ±10% of mock config."""
    mock = create_mock(ttft_ms=50, tps=200, output_tokens=20)
    transport = httpx.ASGITransport(app=mock)
    adapter = OpenAIAdapter("http://mock/v1", None, True, 30.0, streaming=True,
                            transport=transport)
    conn = db.connect(tmp_path / "b.db")
    ep = db.create_endpoint(conn, {"name": "e", "type": "openai", "base_url": "u"})
    t = db.create_test(conn, {"endpoint_id": ep["id"], "model": "mock-model",
                              "workload": "chat", "settings": {}})
    cfg = SweepConfig(max_concurrency=4, dwell_s=1.0, min_requests=3,
                      warmup_requests=1)
    await run_sweep(conn, t["id"], adapter, "mock-model", True, cfg,
                    lambda *a: None, asyncio.Event())
    test = db.get_test(conn, t["id"])
    assert test["status"] == "completed"
    steps = db.list_steps(conn, t["id"])
    assert len(steps) >= 3
    s1 = steps[0]
    assert 45 <= s1["ttft_p50_ms"] <= 150       # 50ms configured + overhead margin
    # throughput should grow from step 1 to the last step (mock is linear)
    assert steps[-1]["throughput_tps"] > s1["throughput_tps"]
```

- [ ] **Step 7: Run all tests — expect PASS**

Run: `pytest -v`

- [ ] **Step 8: Commit**

```bash
git add bench/engine/sweep.py bench/engine/saturation.py tests/test_sweep.py
git commit -m "feat: sweep engine with early stop, fail-fast, saturation guard"
```

---

### Task 10: FastAPI app — endpoints CRUD + probe + healthz

**Files:**
- Create: `bench/api/app.py`, `bench/api/endpoints.py`
- Test: `tests/test_api_endpoints.py`

**Interfaces:**
- Consumes: `db.*`, `crypto.*`, `make_adapter`.
- Produces:
  - `app.create_app(data_dir: Path, secret_key: str | None = None) -> FastAPI` — connects the db (`data_dir/benchmark.db`), loads the secret, stores on `app.state`: `db_conn`, `secret`, `active` (dict: `{"test_id", "task", "stop_event", "hub"}` or None), `hub` (added Task 11 — until then keep a placeholder `None`). Startup hook calls `db.mark_running_tests_stopped`. Serves `frontend/dist` at `/` when that directory exists (mount added here, harmless before the frontend exists). Errors return `{"error": {"code": str, "message": str}}` via exception handlers.
  - Routes (in `endpoints.py`, router mounted under `/api`):
    - `GET /api/endpoints` → list; each item includes `has_api_key: bool`, never the key.
    - `POST /api/endpoints` — body `{name, type, base_url, api_key?, default_model?, verify_tls?}`; encrypts `api_key`; 422 on bad type; 409 on duplicate name (code `"duplicate"`).
    - `PUT /api/endpoints/{id}` — same fields, all optional; omitting `api_key` keeps the stored one.
    - `DELETE /api/endpoints/{id}` — 409 with code `"has_tests"` if any tests reference it.
    - `POST /api/endpoints/{id}/probe` — builds the adapter (decrypting the key), runs `probe()`, stores `supports_streaming`, returns the probe dict.
    - `GET /healthz` → `{"status": "ok", "active_test_id": int | None, "db_ok": true}`.
  - `endpoints.serialize_endpoint(row: dict) -> dict` — drops `api_key_encrypted`, adds `has_api_key`.

- [ ] **Step 1: Write failing tests**

`tests/test_api_endpoints.py`:

```python
import httpx
import pytest

from bench.api.app import create_app


@pytest.fixture
async def client(tmp_path):
    app = create_app(tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://t") as c:
        c.app = app
        yield c


async def test_endpoint_crud_key_write_only(client):
    r = await client.post("/api/endpoints", json={
        "name": "vllm", "type": "openai", "base_url": "http://x/v1",
        "api_key": "sk-secret", "default_model": "llama"})
    assert r.status_code == 200
    ep = r.json()
    assert ep["has_api_key"] is True
    assert "api_key" not in ep and "api_key_encrypted" not in ep
    assert "sk-secret" not in r.text

    r = await client.put(f"/api/endpoints/{ep['id']}",
                         json={"default_model": "llama-70b"})
    assert r.json()["default_model"] == "llama-70b"
    assert r.json()["has_api_key"] is True          # key kept when omitted

    r = await client.get("/api/endpoints")
    assert len(r.json()) == 1

    r = await client.delete(f"/api/endpoints/{ep['id']}")
    assert r.status_code == 200
    assert (await client.get("/api/endpoints")).json() == []


async def test_duplicate_name_409(client):
    body = {"name": "a", "type": "openai", "base_url": "u"}
    await client.post("/api/endpoints", json=body)
    r = await client.post("/api/endpoints", json=body)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "duplicate"


async def test_bad_type_422(client):
    r = await client.post("/api/endpoints", json={
        "name": "a", "type": "grpc", "base_url": "u"})
    assert r.status_code == 422


async def test_healthz(client):
    r = await client.get("/healthz")
    assert r.json() == {"status": "ok", "active_test_id": None, "db_ok": True}
```

- [ ] **Step 2: Run to verify failure, then implement**

`bench/api/app.py`:

```python
import contextlib
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

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
        if active:                      # graceful SIGTERM: stop active test
            active["stop_event"].set()
            with contextlib.suppress(Exception):
                await active["task"]

    app = FastAPI(lifespan=lifespan)
    app.state.db_conn = db.connect(data_dir / "benchmark.db")
    app.state.secret = crypto.load_or_create_secret(data_dir, secret_key)
    app.state.active = None
    app.state.hub = None  # set in Task 11

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError):
        return JSONResponse(status_code=exc.status,
                            content={"error": {"code": exc.code,
                                               "message": exc.message}})

    from bench.api.endpoints import router as endpoints_router
    app.include_router(endpoints_router)

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok",
                "active_test_id": db.active_test_id(app.state.db_conn),
                "db_ok": True}

    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="spa")
    return app
```

`bench/api/endpoints.py`:

```python
import sqlite3
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from bench.adapters.base import make_adapter
from bench.storage import crypto, db

router = APIRouter(prefix="/api")


def serialize_endpoint(row: dict) -> dict:
    out = {k: v for k, v in row.items() if k != "api_key_encrypted"}
    out["has_api_key"] = bool(row.get("api_key_encrypted"))
    out["verify_tls"] = bool(row.get("verify_tls"))
    return out


class EndpointIn(BaseModel):
    name: str
    type: Literal["openai", "asksage"]
    base_url: str
    api_key: str | None = None
    default_model: str | None = None
    verify_tls: bool = True


class EndpointPatch(BaseModel):
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    default_model: str | None = None
    verify_tls: bool | None = None


def _get_or_404(request: Request, endpoint_id: int) -> dict:
    from bench.api.app import ApiError
    row = db.get_endpoint(request.app.state.db_conn, endpoint_id)
    if row is None:
        raise ApiError(404, "not_found", f"endpoint {endpoint_id} not found")
    return row


@router.get("/endpoints")
async def list_endpoints(request: Request):
    return [serialize_endpoint(e) for e in db.list_endpoints(request.app.state.db_conn)]


@router.post("/endpoints")
async def create_endpoint(request: Request, body: EndpointIn):
    from bench.api.app import ApiError
    state = request.app.state
    data = body.model_dump(exclude={"api_key"})
    data["verify_tls"] = int(body.verify_tls)
    data["api_key_encrypted"] = (crypto.encrypt(state.secret, body.api_key)
                                 if body.api_key else None)
    try:
        return serialize_endpoint(db.create_endpoint(state.db_conn, data))
    except sqlite3.IntegrityError:
        raise ApiError(409, "duplicate", f"endpoint name '{body.name}' already exists")


@router.put("/endpoints/{endpoint_id}")
async def update_endpoint(request: Request, endpoint_id: int, body: EndpointPatch):
    state = request.app.state
    _get_or_404(request, endpoint_id)
    data = body.model_dump(exclude_none=True, exclude={"api_key"})
    if "verify_tls" in data:
        data["verify_tls"] = int(data["verify_tls"])
    if body.api_key is not None:
        data["api_key_encrypted"] = crypto.encrypt(state.secret, body.api_key)
    if data:
        db.update_endpoint(state.db_conn, endpoint_id, data)
    return serialize_endpoint(db.get_endpoint(state.db_conn, endpoint_id))


@router.delete("/endpoints/{endpoint_id}")
async def delete_endpoint(request: Request, endpoint_id: int):
    from bench.api.app import ApiError
    state = request.app.state
    _get_or_404(request, endpoint_id)
    if db.list_tests(state.db_conn, endpoint_id=endpoint_id):
        raise ApiError(409, "has_tests",
                       "endpoint has test history; delete those tests first")
    db.delete_endpoint(state.db_conn, endpoint_id)
    return {"ok": True}


@router.post("/endpoints/{endpoint_id}/probe")
async def probe_endpoint(request: Request, endpoint_id: int):
    state = request.app.state
    ep = _get_or_404(request, endpoint_id)
    api_key = (crypto.decrypt(state.secret, ep["api_key_encrypted"])
               if ep["api_key_encrypted"] else None)
    adapter = make_adapter(ep["type"], ep["base_url"], api_key,
                           bool(ep["verify_tls"]), timeout_s=15.0, streaming=True)
    try:
        result = await adapter.probe()
    finally:
        await adapter.aclose()
    db.update_endpoint(state.db_conn, endpoint_id,
                       {"supports_streaming": int(result["supports_streaming"])})
    return result
```

- [ ] **Step 3: Run tests — expect PASS**

Run: `pytest tests/test_api_endpoints.py -v`

- [ ] **Step 4: Commit**

```bash
git add bench/api tests/test_api_endpoints.py
git commit -m "feat: API app with endpoints CRUD, probe, healthz"
```

---

### Task 11: API — test lifecycle + WebSocket

**Files:**
- Create: `bench/api/tests_routes.py`, `bench/api/ws.py`, `bench/main.py`
- Modify: `bench/api/app.py` (wire hub + router)
- Test: `tests/test_api_tests.py`

**Interfaces:**
- Consumes: `run_sweep`, `SweepConfig`, `db.*`, `make_adapter`, `crypto.decrypt`, mock server (in tests).
- Produces:
  - `ws.LiveHub` — `subscribe(test_id) -> asyncio.Queue`, `unsubscribe(test_id, q)`, `publish(test_id, kind: str, data: dict)` (puts `{"type": kind, "data": data}` on every subscriber queue).
  - Routes (router mounted under `/api`):
    - `POST /api/tests` — body `{endpoint_id, model, workload, budget_ttft_ms?, budget_e2e_ms?, settings?}` where `settings` may override `max_concurrency`, `dwell_s`, `min_requests`, `timeout_s`, `temperature`, `seed`. **409** code `"test_active"` when one is running; 404 for unknown endpoint; 422 for unknown workload. Creates the row, builds the adapter (streaming per `supports_streaming`, default True for openai / False for asksage), spawns `asyncio.create_task(run_sweep(...))`, records `app.state.active`. Returns the test row.
    - `GET /api/tests` (filters `endpoint_id`, `model`) — each row serialized with its endpoint name attached (`endpoint_name`).
    - `GET /api/tests/{id}` — test row + `"steps": [...]`.
    - `POST /api/tests/{id}/stop` — sets the stop event; 409 code `"not_running"` if not active.
    - `DELETE /api/tests/{id}` — 409 code `"test_active"` if it's the active test.
    - `WS /ws/tests/{id}` — on connect sends `{"type": "snapshot", "data": {test, steps}}` then relays hub messages until a terminal `status` message or disconnect.
  - `bench/main.py` — `uvicorn.run` entry (`python -m bench.main`) using `load_settings()`.

- [ ] **Step 1: Write failing tests (real sweep against in-process mock via monkeypatched adapter factory)**

`tests/test_api_tests.py`:

```python
import asyncio

import httpx
import pytest

import bench.api.tests_routes as tests_routes
from bench.adapters.openai import OpenAIAdapter
from bench.api.app import create_app
from tools.mockserver.app import create_app as create_mock


@pytest.fixture
async def client(tmp_path, monkeypatch):
    mock = create_mock(ttft_ms=5, tps=2000, output_tokens=10)
    transport = httpx.ASGITransport(app=mock)

    def fake_make_adapter(type_, base_url, api_key, verify_tls, timeout_s, streaming):
        return OpenAIAdapter("http://mock/v1", api_key, verify_tls, timeout_s,
                             streaming, transport=transport)

    monkeypatch.setattr(tests_routes, "make_adapter", fake_make_adapter)
    app = create_app(tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://t") as c:
        c.app = app
        yield c


async def _make_endpoint(client) -> int:
    r = await client.post("/api/endpoints", json={
        "name": "mock", "type": "openai", "base_url": "http://mock/v1"})
    return r.json()["id"]


FAST = {"dwell_s": 0.05, "min_requests": 3, "max_concurrency": 8}


async def _wait_done(client, test_id, timeout=30.0):
    for _ in range(int(timeout / 0.1)):
        t = (await client.get(f"/api/tests/{test_id}")).json()
        if t["status"] != "running":
            return t
        await asyncio.sleep(0.1)
    raise TimeoutError


async def test_start_run_and_complete(client):
    ep = await _make_endpoint(client)
    r = await client.post("/api/tests", json={
        "endpoint_id": ep, "model": "mock-model", "workload": "chat",
        "settings": FAST})
    assert r.status_code == 200
    t = await _wait_done(client, r.json()["id"])
    assert t["status"] == "completed"
    assert t["verdict"] is not None
    assert len(t["steps"]) >= 3


async def test_second_start_409(client):
    ep = await _make_endpoint(client)
    r1 = await client.post("/api/tests", json={
        "endpoint_id": ep, "model": "m", "workload": "chat",
        "settings": {**FAST, "dwell_s": 2.0}})
    r2 = await client.post("/api/tests", json={
        "endpoint_id": ep, "model": "m", "workload": "chat", "settings": FAST})
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "test_active"
    await client.post(f"/api/tests/{r1.json()['id']}/stop")
    await _wait_done(client, r1.json()["id"])


async def test_stop_preserves_steps(client):
    ep = await _make_endpoint(client)
    r = await client.post("/api/tests", json={
        "endpoint_id": ep, "model": "m", "workload": "chat",
        "settings": {**FAST, "dwell_s": 0.3, "max_concurrency": 1024}})
    tid = r.json()["id"]
    await asyncio.sleep(1.0)
    assert (await client.post(f"/api/tests/{tid}/stop")).status_code == 200
    t = await _wait_done(client, tid)
    assert t["status"] == "stopped"


async def test_unknown_workload_422(client):
    ep = await _make_endpoint(client)
    r = await client.post("/api/tests", json={
        "endpoint_id": ep, "model": "m", "workload": "nope"})
    assert r.status_code == 422


async def test_delete_and_history(client):
    ep = await _make_endpoint(client)
    r = await client.post("/api/tests", json={
        "endpoint_id": ep, "model": "m", "workload": "chat", "settings": FAST})
    tid = r.json()["id"]
    await _wait_done(client, tid)
    hist = (await client.get("/api/tests")).json()
    assert hist[0]["endpoint_name"] == "mock"
    assert (await client.delete(f"/api/tests/{tid}")).status_code == 200
    assert (await client.get(f"/api/tests/{tid}")).status_code == 404
```

- [ ] **Step 2: Run to verify failure, then implement `bench/api/ws.py`**

```python
import asyncio
from collections import defaultdict


class LiveHub:
    def __init__(self):
        self._subs: dict[int, list[asyncio.Queue]] = defaultdict(list)

    def subscribe(self, test_id: int) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs[test_id].append(q)
        return q

    def unsubscribe(self, test_id: int, q: asyncio.Queue) -> None:
        if q in self._subs.get(test_id, []):
            self._subs[test_id].remove(q)

    def publish(self, test_id: int, kind: str, data: dict) -> None:
        for q in list(self._subs.get(test_id, [])):
            q.put_nowait({"type": kind, "data": data})
```

- [ ] **Step 3: Implement `bench/api/tests_routes.py`**

```python
import asyncio

from fastapi import APIRouter, Request
from pydantic import BaseModel

from bench.adapters.base import make_adapter
from bench.engine.sweep import SweepConfig, run_sweep
from bench.engine.workload import PRESETS
from bench.storage import crypto, db

router = APIRouter(prefix="/api")


class TestIn(BaseModel):
    endpoint_id: int
    model: str
    workload: str
    budget_ttft_ms: float | None = None
    budget_e2e_ms: float | None = None
    settings: dict = {}


def _serialize(state, t: dict) -> dict:
    ep = db.get_endpoint(state.db_conn, t["endpoint_id"])
    return {**t, "endpoint_name": ep["name"] if ep else None,
            "endpoint_type": ep["type"] if ep else None}


@router.post("/tests")
async def start_test(request: Request, body: TestIn):
    from bench.api.app import ApiError
    state = request.app.state
    active = db.active_test_id(state.db_conn)
    if active is not None:
        raise ApiError(409, "test_active", f"test {active} is already running")
    ep = db.get_endpoint(state.db_conn, body.endpoint_id)
    if ep is None:
        raise ApiError(404, "not_found", f"endpoint {body.endpoint_id} not found")
    if body.workload not in PRESETS:
        raise ApiError(422, "bad_workload", f"unknown workload '{body.workload}'")

    allowed = {"max_concurrency", "dwell_s", "min_requests", "timeout_s",
               "temperature", "seed"}
    cfg = SweepConfig(workload=body.workload, budget_ttft_ms=body.budget_ttft_ms,
                      budget_e2e_ms=body.budget_e2e_ms,
                      **{k: v for k, v in body.settings.items() if k in allowed})
    streaming = (bool(ep["supports_streaming"])
                 if ep["supports_streaming"] is not None
                 else ep["type"] == "openai")
    api_key = (crypto.decrypt(state.secret, ep["api_key_encrypted"])
               if ep["api_key_encrypted"] else None)
    adapter = make_adapter(ep["type"], ep["base_url"], api_key,
                           bool(ep["verify_tls"]), cfg.timeout_s, streaming)

    t = db.create_test(state.db_conn, {
        "endpoint_id": ep["id"], "model": body.model, "workload": body.workload,
        "budget_ttft_ms": body.budget_ttft_ms, "budget_e2e_ms": body.budget_e2e_ms,
        "settings": {**cfg.__dict__}})
    stop_event = asyncio.Event()
    test_id = t["id"]

    def publish(kind: str, data: dict):
        state.hub.publish(test_id, kind, data)

    async def runner():
        try:
            await run_sweep(state.db_conn, test_id, adapter, body.model,
                            streaming, cfg, publish, stop_event)
        finally:
            state.active = None

    task = asyncio.create_task(runner())
    state.active = {"test_id": test_id, "task": task, "stop_event": stop_event}
    return _serialize(state, t)


@router.get("/tests")
async def list_tests(request: Request, endpoint_id: int | None = None,
                     model: str | None = None):
    state = request.app.state
    return [_serialize(state, t)
            for t in db.list_tests(state.db_conn, endpoint_id, model)]


@router.get("/tests/{test_id}")
async def get_test(request: Request, test_id: int):
    from bench.api.app import ApiError
    state = request.app.state
    t = db.get_test(state.db_conn, test_id)
    if t is None:
        raise ApiError(404, "not_found", f"test {test_id} not found")
    return {**_serialize(state, t), "steps": db.list_steps(state.db_conn, test_id)}


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
```

The WebSocket is registered on the app itself, not this router (its path is `/ws/...`, outside the `/api` prefix). In `bench/api/app.py`, after `include_router(endpoints_router)` add:

```python
    from bench.api.ws import LiveHub
    app.state.hub = LiveHub()

    from bench.api.tests_routes import router as tests_router
    app.include_router(tests_router)

    from fastapi import WebSocket

    @app.websocket("/ws/tests/{test_id}")
    async def ws_test(websocket: WebSocket, test_id: int):
        from starlette.websockets import WebSocketDisconnect
        conn = app.state.db_conn
        await websocket.accept()
        t = db.get_test(conn, test_id)
        if t is None:
            await websocket.close(code=4404)
            return
        await websocket.send_json({"type": "snapshot", "data": {
            "test": {k: v for k, v in t.items()},
            "steps": db.list_steps(conn, test_id)}})
        if t["status"] != "running":
            await websocket.close()
            return
        q = app.state.hub.subscribe(test_id)
        try:
            while True:
                msg = await q.get()
                await websocket.send_json(msg)
                if msg["type"] == "status":
                    break
        except WebSocketDisconnect:
            pass
        finally:
            app.state.hub.unsubscribe(test_id, q)
```

- [ ] **Step 4: Write `bench/main.py`**

```python
import uvicorn

from bench.api.app import create_app
from bench.config import load_settings


def main() -> None:
    s = load_settings()
    uvicorn.run(create_app(s.data_dir, s.secret_key), host="0.0.0.0", port=s.port)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run tests — expect PASS**

Run: `pytest tests/test_api_tests.py -v` then the full suite `pytest -q`

- [ ] **Step 6: Commit**

```bash
git add bench/api bench/main.py tests/test_api_tests.py
git commit -m "feat: test lifecycle API with 409 guard, stop, delete, live WebSocket"
```

---

### Task 12: Exports — CSV and self-contained HTML

**Files:**
- Create: `bench/reports/export.py`, `bench/reports/vendor/.gitkeep`
- Modify: `bench/api/tests_routes.py` (two routes)
- Test: `tests/test_export.py`

**Interfaces:**
- Consumes: `db.list_requests`, `db.list_steps`, `db.get_test`.
- Produces:
  - `export.to_csv(test: dict, requests: list[dict]) -> str` — header row = `requests` column names (minus `id`/`test_id`), one line per request.
  - `export.to_html(test: dict, steps: list[dict], endpoint_name: str) -> str` — one self-contained HTML string: inlined ECharts source (read from `bench/reports/vendor/echarts.min.js`), inlined steps/verdict JSON, renders the saturation chart + verdict + step table offline. Raises `RuntimeError` if the vendor file is missing.
  - Routes: `GET /api/tests/{id}/export.csv` (`text/csv`, `Content-Disposition: attachment`), `GET /api/tests/{id}/export.html` (`text/html`, attachment).
- The vendor file `bench/reports/vendor/echarts.min.js` is copied from the frontend's node_modules in Task 13 (step 6) and committed then. Until then `to_html` tests use a stub vendor file written by the test.

- [ ] **Step 1: Write failing tests**

`tests/test_export.py`:

```python
import bench.reports.export as export


TEST = {"id": 1, "model": "m", "workload": "chat", "status": "completed",
        "budget_ttft_ms": None, "budget_e2e_ms": None,
        "started_at": "2026-08-02", "finished_at": "2026-08-02",
        "flags": {}, "settings": {},
        "verdict": {"knee_concurrency": 4, "sweet_zone": [2, 8],
                    "throughput_tps": 500.0, "p95_latency_ms": 300.0,
                    "latency_metric": "ttft", "budget": None}}
STEPS = [{"concurrency": 1, "requests_completed": 20, "throughput_tps": 100.0,
          "ttft_p50_ms": 100.0, "ttft_p95_ms": 150.0, "e2e_p50_ms": 900.0,
          "e2e_p95_ms": 1000.0, "error_count": 0, "started_at": "x",
          "duration_s": 45.0}]
REQS = [{"id": 1, "test_id": 1, "concurrency": 1, "prompt_id": "chat-01",
         "t_send_wall": "x", "ttft_ms": 100.0, "e2e_ms": 900.0,
         "prompt_tokens": 500, "output_tokens": 300, "tokens_estimated": 0,
         "error_class": None, "error_detail": None}]


def test_csv_has_header_and_rows():
    out = export.to_csv(TEST, REQS)
    lines = out.strip().splitlines()
    assert lines[0].startswith("concurrency,prompt_id")
    assert len(lines) == 2 and "chat-01" in lines[1]
    assert "test_id" not in lines[0]


def test_html_self_contained(tmp_path, monkeypatch):
    vendor = tmp_path / "echarts.min.js"
    vendor.write_text("/* echarts stub */ var echarts={init:function(){}};")
    monkeypatch.setattr(export, "VENDOR_JS", vendor)
    html = export.to_html(TEST, STEPS, "mock")
    assert "echarts stub" in html            # library inlined
    assert '"knee_concurrency": 4' in html or '"knee_concurrency":4' in html
    assert "<script src=" not in html        # no external references
```

- [ ] **Step 2: Run to verify failure, then implement `bench/reports/export.py`**

```python
import csv
import io
import json
from pathlib import Path

VENDOR_JS = Path(__file__).resolve().parent / "vendor" / "echarts.min.js"

CSV_COLS = ["concurrency", "prompt_id", "t_send_wall", "ttft_ms", "e2e_ms",
            "prompt_tokens", "output_tokens", "tokens_estimated",
            "error_class", "error_detail"]

_TEMPLATE = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>Benchmark {test_id} — {endpoint} / {model}</title>
<style>
 body{{font-family:system-ui,sans-serif;background:#0e1116;color:#e6e6e6;
      max-width:960px;margin:2rem auto;padding:0 1rem}}
 h1{{font-size:1.2rem}} .verdict{{margin:.5rem 0 1rem;font-size:1rem}}
 table{{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}}
 th,td{{border:1px solid #333;padding:4px 8px;text-align:right;font-size:.85rem}}
 th:first-child,td:first-child{{text-align:left}}
</style>
<script>{echarts_js}</script>
</head><body>
<h1>{endpoint} / {model} — {workload} workload — {status}</h1>
<div class="verdict">{verdict_text}</div>
<div id="chart" style="width:100%;height:420px"></div>
<table><thead><tr><th>Concurrency</th><th>Requests</th><th>tok/s</th>
<th>TTFT p50</th><th>TTFT p95</th><th>E2E p50</th><th>E2E p95</th>
<th>Errors</th></tr></thead><tbody>{rows}</tbody></table>
<script>
var steps = {steps_json};
var verdict = {verdict_json};
var chart = echarts.init(document.getElementById('chart'), 'dark');
chart.setOption({{
  xAxis: {{type:'log', logBase:2, name:'concurrency',
           min:steps.length?steps[0].concurrency:1}},
  yAxis: [{{type:'value', name:'tok/s'}},
          {{type:'value', name:'p95 latency (ms)'}}],
  tooltip: {{trigger:'axis'}},
  series: [
    {{name:'throughput', type:'line', yAxisIndex:0,
      data:steps.map(s=>[s.concurrency, s.throughput_tps])}},
    {{name:'p95 latency', type:'line', yAxisIndex:1, lineStyle:{{type:'dashed'}},
      data:steps.map(s=>[s.concurrency,
        s.ttft_p95_ms !== null ? s.ttft_p95_ms : s.e2e_p95_ms])}},
  ].concat(verdict ? [{{
      name:'sweet zone', type:'line', markArea:{{itemStyle:{{color:'rgba(80,200,140,0.12)'}},
      data:[[{{xAxis:verdict.sweet_zone[0]}},{{xAxis:verdict.sweet_zone[1]}}]]}},
      data:[]}}] : [])
}});
</script>
</body></html>"""


def to_csv(test: dict, requests: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_COLS, extrasaction="ignore")
    writer.writeheader()
    for r in requests:
        writer.writerow(r)
    return buf.getvalue()


def _verdict_text(v: dict | None) -> str:
    if not v:
        return "No verdict (fewer than 3 completed steps or client-saturated run)."
    metric = "p95 TTFT" if v["latency_metric"] == "ttft" else "p95 E2E"
    text = (f"Sweet spot <b>{v['knee_concurrency']}</b> concurrent · "
            f"<b>{round(v['throughput_tps'] or 0)}</b> tok/s · "
            f"{metric} <b>{round(v['p95_latency_ms'] or 0)}</b> ms")
    if v.get("budget"):
        b = v["budget"]
        text += ("" if not b["met"] else
                 f" · budgets hold to <b>{b['max_concurrency']}</b> concurrent "
                 f"(limited by {b['limited_by']})")
        if not b["met"]:
            text += " · <b>budget not met at any tested concurrency</b>"
    return text


def to_html(test: dict, steps: list[dict], endpoint_name: str) -> str:
    if not VENDOR_JS.exists():
        raise RuntimeError(f"missing vendored echarts at {VENDOR_JS}")
    return _TEMPLATE.format(
        test_id=test["id"], endpoint=endpoint_name, model=test["model"],
        workload=test["workload"], status=test["status"],
        echarts_js=VENDOR_JS.read_text(),
        verdict_text=_verdict_text(test.get("verdict")),
        steps_json=json.dumps(steps),
        verdict_json=json.dumps(test.get("verdict")),
        rows="".join(
            f"<tr><td>{s['concurrency']}</td><td>{s['requests_completed']}</td>"
            f"<td>{round(s['throughput_tps'] or 0)}</td>"
            f"<td>{s['ttft_p50_ms'] or 'N/A'}</td><td>{s['ttft_p95_ms'] or 'N/A'}</td>"
            f"<td>{s['e2e_p50_ms'] or ''}</td><td>{s['e2e_p95_ms'] or ''}</td>"
            f"<td>{s['error_count']}</td></tr>" for s in steps))
```

- [ ] **Step 3: Add the two routes to `bench/api/tests_routes.py`**

```python
from fastapi.responses import Response

from bench.reports import export


@router.get("/tests/{test_id}/export.csv")
async def export_csv(request: Request, test_id: int):
    from bench.api.app import ApiError
    state = request.app.state
    t = db.get_test(state.db_conn, test_id)
    if t is None:
        raise ApiError(404, "not_found", f"test {test_id} not found")
    body = export.to_csv(t, db.list_requests(state.db_conn, test_id))
    return Response(body, media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="test-{test_id}.csv"'})


@router.get("/tests/{test_id}/export.html")
async def export_html(request: Request, test_id: int):
    from bench.api.app import ApiError
    state = request.app.state
    t = db.get_test(state.db_conn, test_id)
    if t is None:
        raise ApiError(404, "not_found", f"test {test_id} not found")
    ep = db.get_endpoint(state.db_conn, t["endpoint_id"])
    body = export.to_html(t, db.list_steps(state.db_conn, test_id),
                          ep["name"] if ep else "?")
    return Response(body, media_type="text/html", headers={
        "Content-Disposition": f'attachment; filename="test-{test_id}.html"'})
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `pytest tests/test_export.py -v && pytest -q`

- [ ] **Step 5: Commit**

```bash
git add bench/reports tests/test_export.py bench/api/tests_routes.py
git commit -m "feat: CSV and self-contained HTML export"
```

---

### Task 13: Frontend scaffold, shell, and API client

**Files:**
- Create: `frontend/` (Vite scaffold), `frontend/src/api.ts`, `frontend/src/App.tsx`, `frontend/src/main.tsx`, `frontend/src/styles.css`, `frontend/vite.config.ts`
- Modify: commit `bench/reports/vendor/echarts.min.js` (copied from node_modules)

**Interfaces:**
- Consumes: the REST API from Tasks 10–12.
- Produces:
  - `api.ts` typed client used by all pages:
    ```typescript
    export interface Endpoint { id: number; name: string; type: "openai" | "asksage";
      base_url: string; default_model: string | null; verify_tls: boolean;
      supports_streaming: number | null; has_api_key: boolean; }
    export interface Step { concurrency: number; requests_completed: number;
      throughput_tps: number | null; ttft_p50_ms: number | null;
      ttft_p95_ms: number | null; e2e_p50_ms: number | null;
      e2e_p95_ms: number | null; error_count: number; duration_s: number; }
    export interface Verdict { knee_concurrency: number; sweet_zone: [number, number];
      throughput_tps: number; p95_latency_ms: number | null;
      latency_metric: "ttft" | "e2e";
      budget: { max_concurrency: number; limited_by: string; met: boolean } | null; }
    export interface BenchTest { id: number; endpoint_id: number; endpoint_name: string;
      endpoint_type: string; model: string; workload: string; status: string;
      budget_ttft_ms: number | null; budget_e2e_ms: number | null;
      flags: Record<string, boolean>; verdict: Verdict | null;
      started_at: string; finished_at: string | null; error: string | null;
      steps?: Step[]; settings: Record<string, unknown>; }
    export const api = { listEndpoints, createEndpoint, updateEndpoint,
      deleteEndpoint, probeEndpoint, startTest, listTests, getTest, stopTest,
      deleteTest };  // each a thin fetch wrapper throwing Error(message) on {error}
    export function wsUrl(testId: number): string;
    ```
  - `App.tsx` — react-router with top nav (New Test `/`, History `/history`, Endpoints `/endpoints`) and route `/tests/:id`; placeholder `<div>` pages for now (replaced in Tasks 14–15).
  - Dark theme base CSS per old UI spec's palette: background `#0e1116`, surfaces `#161b22`, borders `#262d38`, accent `#4f9cf9`, success `#3fb27f`, warning `#d9a13d`, danger `#e5534b`; tabular numerals for metric values.

- [ ] **Step 1: Scaffold**

```bash
cd frontend
npm create vite@latest . -- --template react-ts
npm install
npm install react-router-dom echarts
```

- [ ] **Step 2: `frontend/vite.config.ts`** (dev proxy to the backend)

```typescript
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://localhost:8080",
      "/ws": { target: "ws://localhost:8080", ws: true },
    },
  },
});
```

- [ ] **Step 3: Write `frontend/src/api.ts`**

```typescript
// Interfaces exactly as in this task's Interfaces block, then:

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json" }, ...init });
  const body = await resp.json().catch(() => null);
  if (!resp.ok) {
    throw new Error(body?.error?.message ?? `HTTP ${resp.status}`);
  }
  return body as T;
}

export const api = {
  listEndpoints: () => request<Endpoint[]>("/api/endpoints"),
  createEndpoint: (data: object) =>
    request<Endpoint>("/api/endpoints", { method: "POST", body: JSON.stringify(data) }),
  updateEndpoint: (id: number, data: object) =>
    request<Endpoint>(`/api/endpoints/${id}`, { method: "PUT", body: JSON.stringify(data) }),
  deleteEndpoint: (id: number) =>
    request<{ ok: boolean }>(`/api/endpoints/${id}`, { method: "DELETE" }),
  probeEndpoint: (id: number) =>
    request<ProbeResult>(`/api/endpoints/${id}/probe`, { method: "POST" }),
  startTest: (data: object) =>
    request<BenchTest>("/api/tests", { method: "POST", body: JSON.stringify(data) }),
  listTests: () => request<BenchTest[]>("/api/tests"),
  getTest: (id: number) => request<BenchTest>(`/api/tests/${id}`),
  stopTest: (id: number) =>
    request<{ ok: boolean }>(`/api/tests/${id}/stop`, { method: "POST" }),
  deleteTest: (id: number) =>
    request<{ ok: boolean }>(`/api/tests/${id}`, { method: "DELETE" }),
};

export interface ProbeResult { reachable: boolean; auth_ok: boolean;
  models: string[]; supports_streaming: boolean; latency_ms: number | null;
  error: string | null; }

export function wsUrl(testId: number): string {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}/ws/tests/${testId}`;
}
```

- [ ] **Step 4: Write `App.tsx`, `main.tsx`, `styles.css`**

`frontend/src/App.tsx`:

```tsx
import { BrowserRouter, NavLink, Route, Routes } from "react-router-dom";

export default function App() {
  return (
    <BrowserRouter>
      <nav className="topnav">
        <span className="brand">Inference Benchmark</span>
        <NavLink to="/" end>New Test</NavLink>
        <NavLink to="/history">History</NavLink>
        <NavLink to="/endpoints">Endpoints</NavLink>
      </nav>
      <main>
        <Routes>
          <Route path="/" element={<div>New Test (Task 15)</div>} />
          <Route path="/tests/:id" element={<div>Test (Task 15)</div>} />
          <Route path="/history" element={<div>History (Task 15)</div>} />
          <Route path="/endpoints" element={<div>Endpoints (Task 14)</div>} />
        </Routes>
      </main>
    </BrowserRouter>
  );
}
```

`frontend/src/main.tsx`:

```tsx
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode><App /></React.StrictMode>);
```

`frontend/src/styles.css` (base; pages add nothing global later):

```css
:root {
  --bg: #0e1116; --surface: #161b22; --border: #262d38;
  --text: #e6edf3; --muted: #8b949e; --accent: #4f9cf9;
  --ok: #3fb27f; --warn: #d9a13d; --danger: #e5534b;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 14px/1.45 Inter, system-ui, sans-serif; }
main { max-width: 1080px; margin: 0 auto; padding: 1.5rem 1rem; }
.topnav { display: flex; gap: 1.25rem; align-items: center;
  padding: 0.6rem 1rem; background: var(--surface);
  border-bottom: 1px solid var(--border); }
.topnav .brand { font-weight: 600; margin-right: 1rem; }
.topnav a { color: var(--muted); text-decoration: none; }
.topnav a.active { color: var(--text); }
.metric { font-family: ui-monospace, "JetBrains Mono", monospace;
  font-variant-numeric: tabular-nums; }
.card { background: var(--surface); border: 1px solid var(--border);
  border-radius: 8px; padding: 1rem; }
button.primary { background: var(--accent); color: #fff; border: 0;
  border-radius: 6px; padding: 0.5rem 1rem; font-weight: 600; cursor: pointer; }
button.danger { background: var(--danger); color: #fff; border: 0;
  border-radius: 6px; padding: 0.4rem 0.9rem; cursor: pointer; }
table { border-collapse: collapse; width: 100%; }
th, td { border-bottom: 1px solid var(--border); padding: 6px 10px;
  text-align: right; }
th:first-child, td:first-child { text-align: left; }
input, select { background: var(--bg); color: var(--text);
  border: 1px solid var(--border); border-radius: 6px; padding: 0.45rem 0.6rem; }
label { display: block; font-size: 11px; text-transform: uppercase;
  letter-spacing: 0.06em; color: var(--muted); margin: 0.7rem 0 0.25rem; }
.badge { display: inline-block; border: 1px solid var(--warn);
  color: var(--warn); border-radius: 10px; padding: 0 8px; font-size: 11px; }
```

Delete Vite's demo files (`src/App.css`, `src/assets/`, `public/vite.svg` references in `index.html`).

- [ ] **Step 5: Verify the build**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: builds cleanly into `frontend/dist/`

- [ ] **Step 6: Vendor ECharts for the HTML export**

```bash
cp frontend/node_modules/echarts/dist/echarts.min.js bench/reports/vendor/echarts.min.js
```

- [ ] **Step 7: Commit**

```bash
git add frontend bench/reports/vendor
git commit -m "feat: frontend scaffold with nav shell, API client, vendored echarts"
```

---

### Task 14: Endpoints page

**Files:**
- Create: `frontend/src/pages/EndpointsPage.tsx`
- Modify: `frontend/src/App.tsx` (route)

**Interfaces:**
- Consumes: `api.listEndpoints/createEndpoint/updateEndpoint/deleteEndpoint/probeEndpoint`, `Endpoint`, `ProbeResult`.
- Produces: `<EndpointsPage />` — table + add/edit form; probe results shown inline; used indirectly by NewTestPage (endpoint list comes from the same API).

- [ ] **Step 1: Implement `frontend/src/pages/EndpointsPage.tsx`**

```tsx
import { useEffect, useState } from "react";
import { api, Endpoint, ProbeResult } from "../api";

const EMPTY = { name: "", type: "openai", base_url: "", api_key: "",
  default_model: "", verify_tls: true };

export default function EndpointsPage() {
  const [endpoints, setEndpoints] = useState<Endpoint[]>([]);
  const [form, setForm] = useState<Record<string, unknown>>(EMPTY);
  const [editing, setEditing] = useState<number | null>(null);
  const [probe, setProbe] = useState<Record<number, ProbeResult | "loading">>({});
  const [error, setError] = useState("");

  const reload = () => api.listEndpoints().then(setEndpoints).catch(e => setError(e.message));
  useEffect(() => { reload(); }, []);

  async function save() {
    setError("");
    const body: Record<string, unknown> = { ...form };
    if (!body.api_key) delete body.api_key;   // omit -> keep stored key
    try {
      if (editing) await api.updateEndpoint(editing, body);
      else await api.createEndpoint(body);
      setForm(EMPTY); setEditing(null); reload();
    } catch (e) { setError((e as Error).message); }
  }

  async function runProbe(id: number) {
    setProbe(p => ({ ...p, [id]: "loading" }));
    try {
      const r = await api.probeEndpoint(id);
      setProbe(p => ({ ...p, [id]: r })); reload();
    } catch (e) { setError((e as Error).message); }
  }

  return (
    <div>
      <h2>Endpoints</h2>
      {error && <p style={{ color: "var(--danger)" }}>{error}</p>}
      <table>
        <thead><tr><th>Name</th><th>Type</th><th>Base URL</th><th>Model</th>
          <th>Streaming</th><th></th></tr></thead>
        <tbody>{endpoints.map(ep => (
          <tr key={ep.id}>
            <td>{ep.name}</td><td>{ep.type}</td>
            <td style={{ textAlign: "left" }}>{ep.base_url}</td>
            <td>{ep.default_model ?? "—"}</td>
            <td>{ep.supports_streaming == null ? "?" :
                 ep.supports_streaming ? "yes" : "no"}</td>
            <td>
              <button onClick={() => runProbe(ep.id)}>Test connection</button>{" "}
              <button onClick={() => { setEditing(ep.id);
                setForm({ name: ep.name, type: ep.type, base_url: ep.base_url,
                  api_key: "", default_model: ep.default_model ?? "",
                  verify_tls: ep.verify_tls }); }}>Edit</button>{" "}
              <button className="danger" onClick={async () => {
                if (!confirm(`Delete endpoint ${ep.name}?`)) return;
                try { await api.deleteEndpoint(ep.id); reload(); }
                catch (e) { setError((e as Error).message); }
              }}>Delete</button>
              {probe[ep.id] === "loading" && <span> probing…</span>}
              {probe[ep.id] && probe[ep.id] !== "loading" && (
                <div style={{ fontSize: 12, color: "var(--muted)" }}>
                  {(probe[ep.id] as ProbeResult).auth_ok
                    ? `OK · ${(probe[ep.id] as ProbeResult).latency_ms} ms · models: ${
                        (probe[ep.id] as ProbeResult).models.join(", ") || "n/a"}`
                    : `Failed: ${(probe[ep.id] as ProbeResult).error}`}
                </div>)}
            </td>
          </tr>))}
        </tbody>
      </table>

      <div className="card" style={{ marginTop: "1.5rem", maxWidth: 460 }}>
        <h3>{editing ? "Edit endpoint" : "Add endpoint"}</h3>
        <label>Name</label>
        <input value={form.name as string}
          onChange={e => setForm({ ...form, name: e.target.value })} />
        <label>Type</label>
        <select value={form.type as string}
          onChange={e => setForm({ ...form, type: e.target.value })}>
          <option value="openai">OpenAI-compatible</option>
          <option value="asksage">AskSage</option>
        </select>
        {form.type === "asksage" &&
          <p style={{ fontSize: 12, color: "var(--muted)" }}>
            Non-streaming API — TTFT is unavailable; the E2E latency budget applies.</p>}
        <label>Base URL</label>
        <input value={form.base_url as string} placeholder="https://host:8000/v1"
          onChange={e => setForm({ ...form, base_url: e.target.value })} />
        <label>API key {editing ? "(leave blank to keep current)" : "(optional)"}</label>
        <input type="password" value={form.api_key as string}
          onChange={e => setForm({ ...form, api_key: e.target.value })} />
        <label>Default model</label>
        <input value={form.default_model as string}
          onChange={e => setForm({ ...form, default_model: e.target.value })} />
        <label>
          <input type="checkbox" checked={form.verify_tls as boolean}
            onChange={e => setForm({ ...form, verify_tls: e.target.checked })} />
          {" "}Verify TLS
        </label>
        <div style={{ marginTop: "0.8rem" }}>
          <button className="primary" onClick={save}>
            {editing ? "Save" : "Add endpoint"}</button>{" "}
          {editing && <button onClick={() => { setEditing(null); setForm(EMPTY); }}>
            Cancel</button>}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Wire the route**

In `App.tsx` replace the endpoints placeholder:

```tsx
import EndpointsPage from "./pages/EndpointsPage";
// ...
<Route path="/endpoints" element={<EndpointsPage />} />
```

- [ ] **Step 3: Verify build and behavior**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Then manual check: `DATA_DIR=/tmp/benchdata python -m bench.main` in one shell, `python -m tools.mockserver.app --port 9000` in another, `npm run dev` in a third; open the Endpoints page, add `http://localhost:9000/v1` (type openai), Test connection → expect "OK" with model `mock-model`.

- [ ] **Step 4: Commit**

```bash
git add frontend/src
git commit -m "feat: endpoints page with probe and key-replace flow"
```

---

### Task 15: New Test, Test, and History pages

**Files:**
- Create: `frontend/src/pages/NewTestPage.tsx`, `frontend/src/pages/TestPage.tsx`, `frontend/src/pages/HistoryPage.tsx`, `frontend/src/chart.ts`
- Modify: `frontend/src/App.tsx` (routes)

**Interfaces:**
- Consumes: everything in `api.ts`; `echarts`.
- Produces:
  - `chart.buildOption(steps: Step[], verdict: Verdict | null, streaming: boolean, inProgress: {concurrency: number, pct: number} | null): echarts.EChartsOption` — the hero chart used by both TestPage states and reused conceptually by the HTML export.
  - Pages wired to routes `/`, `/tests/:id`, `/history`.

- [ ] **Step 1: Implement `frontend/src/chart.ts`**

```typescript
import type { EChartsOption } from "echarts";
import { Step, Verdict } from "./api";

export function buildOption(steps: Step[], verdict: Verdict | null,
    streaming: boolean, inProgress: { concurrency: number; pct: number } | null
): EChartsOption {
  const lat = (s: Step) => streaming ? s.ttft_p95_ms : s.e2e_p95_ms;
  const series: EChartsOption["series"] = [
    { name: "throughput (tok/s)", type: "line", yAxisIndex: 0,
      symbolSize: 8, color: "#5ba3f5",
      data: steps.map(s => [s.concurrency, s.throughput_tps]),
      markArea: verdict ? { itemStyle: { color: "rgba(63,178,127,0.12)" },
        data: [[{ xAxis: verdict.sweet_zone[0] }, { xAxis: verdict.sweet_zone[1] }]] }
        : undefined,
      markLine: verdict ? { symbol: "none",
        lineStyle: { color: "#3fb27f", type: "dashed" },
        label: { formatter: `sweet spot: ${verdict.knee_concurrency}` },
        data: [{ xAxis: verdict.knee_concurrency }] } : undefined },
    { name: streaming ? "p95 TTFT (ms)" : "p95 E2E (ms)", type: "line",
      yAxisIndex: 1, color: "#d96f6f", lineStyle: { type: "dashed" },
      data: steps.map(s => [s.concurrency, lat(s)]) },
  ];
  if (inProgress) {
    series.push({ name: "measuring", type: "line", yAxisIndex: 0, data: [],
      markLine: { symbol: "none",
        lineStyle: { color: "#d9a13d", type: "dotted" },
        label: { color: "#d9a13d",
          formatter: `measuring ${inProgress.concurrency}… ${inProgress.pct}%` },
        data: [{ xAxis: inProgress.concurrency }] } });
  }
  return {
    backgroundColor: "transparent",
    tooltip: { trigger: "axis" },
    legend: { textStyle: { color: "#8b949e" } },
    grid: { left: 60, right: 70, top: 40, bottom: 40 },
    xAxis: { type: "log", logBase: 2, name: "concurrency",
      min: 1, axisLabel: { color: "#8b949e" } },
    yAxis: [
      { type: "value", name: "tok/s", axisLabel: { color: "#8b949e" } },
      { type: "value", name: "ms", axisLabel: { color: "#8b949e" } }],
    series,
  };
}
```

- [ ] **Step 2: Implement `frontend/src/pages/NewTestPage.tsx`**

```tsx
import { useEffect, useMemo, useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { api, Endpoint } from "../api";

const WORKLOADS: Record<string, { label: string; desc: string }> = {
  chat: { label: "Chat (balanced)", desc: "~500-token prompts, ~300-token answers" },
  long_context: { label: "Long context", desc: "~4,000-token prompts, ~200-token answers" },
  generation: { label: "Generation", desc: "~80-token prompts, ~1,000-token answers" },
};

export default function NewTestPage() {
  const nav = useNavigate();
  const [endpoints, setEndpoints] = useState<Endpoint[]>([]);
  const [epId, setEpId] = useState<number | null>(null);
  const [model, setModel] = useState("");
  const [workload, setWorkload] = useState("chat");
  const [budgetTtft, setBudgetTtft] = useState("");
  const [budgetE2e, setBudgetE2e] = useState("");
  const [showAdv, setShowAdv] = useState(false);
  const [maxC, setMaxC] = useState("128");
  const [dwell, setDwell] = useState("45");
  const [error, setError] = useState("");

  useEffect(() => { api.listEndpoints().then(eps => {
    setEndpoints(eps);
    if (eps.length && epId === null) {
      setEpId(eps[0].id); setModel(eps[0].default_model ?? "");
    }
  }); }, []);

  const ep = endpoints.find(e => e.id === epId);
  const streaming = ep ? ep.type === "openai" : true;

  const plan = useMemo(() => {
    const ceiling = parseInt(maxC) || 128;
    const stepList: number[] = [];
    for (let c = 1; c <= ceiling; c *= 2) stepList.push(c);
    const dwellS = parseInt(dwell) || 45;
    const mins = Math.round(stepList.length * (dwellS + 5) / 60);
    return { stepList, mins };
  }, [maxC, dwell]);

  async function start() {
    setError("");
    if (!epId) { setError("Add an endpoint first."); return; }
    if (!model) { setError("Model is required."); return; }
    try {
      const t = await api.startTest({
        endpoint_id: epId, model, workload,
        budget_ttft_ms: budgetTtft ? Number(budgetTtft) : null,
        budget_e2e_ms: budgetE2e ? Number(budgetE2e) : null,
        settings: { max_concurrency: parseInt(maxC) || 128,
                    dwell_s: parseInt(dwell) || 45 },
      });
      nav(`/tests/${t.id}`);
    } catch (e) { setError((e as Error).message); }
  }

  return (
    <div style={{ display: "flex", gap: "1.5rem", alignItems: "flex-start" }}>
      <div className="card" style={{ flex: 3 }}>
        <h2>New Test</h2>
        <label>Endpoint</label>
        <select value={epId ?? ""} onChange={e => {
          const id = Number(e.target.value); setEpId(id);
          const sel = endpoints.find(x => x.id === id);
          if (sel?.default_model) setModel(sel.default_model);
        }}>
          {endpoints.map(e => <option key={e.id} value={e.id}>{e.name}</option>)}
        </select>
        {!endpoints.length &&
          <p><Link to="/endpoints">Add an endpoint first →</Link></p>}
        <label>Model</label>
        <input value={model} onChange={e => setModel(e.target.value)} />
        <label>Workload</label>
        <select value={workload} onChange={e => setWorkload(e.target.value)}>
          {Object.entries(WORKLOADS).map(([k, v]) =>
            <option key={k} value={k}>{v.label}</option>)}
        </select>
        <label>p95 TTFT budget (ms, optional)</label>
        <input value={budgetTtft} disabled={!streaming}
          placeholder={streaming ? "e.g. 1000" : "N/A — non-streaming endpoint"}
          onChange={e => setBudgetTtft(e.target.value)} />
        <label>p95 E2E budget (ms, optional)</label>
        <input value={budgetE2e} placeholder="e.g. 8000"
          onChange={e => setBudgetE2e(e.target.value)} />
        <p><a href="#" onClick={e => { e.preventDefault(); setShowAdv(!showAdv); }}>
          {showAdv ? "▾" : "▸"} Advanced</a></p>
        {showAdv && <>
          <label>Max concurrency ceiling</label>
          <input value={maxC} onChange={e => setMaxC(e.target.value)} />
          <label>Step dwell (seconds)</label>
          <input value={dwell} onChange={e => setDwell(e.target.value)} />
        </>}
      </div>

      <div className="card" style={{ flex: 2, borderStyle: "dashed" }}>
        <h3>Test plan</h3>
        <p>Will sweep concurrency <b>{plan.stepList.join(" → ")}</b>,
          ~{dwell} s per step.</p>
        <p>Workload: {WORKLOADS[workload].desc}.</p>
        <p>Estimated total: <b>~{plan.mins} min</b> worst case — stops early
          once throughput flattens.</p>
        {!streaming && <p className="badge">non-streaming: E2E latency only</p>}
        {error && <p style={{ color: "var(--danger)" }}>{error}</p>}
        <button className="primary" style={{ width: "100%", marginTop: 8 }}
          onClick={start}>▶ Find sweet spot</button>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Implement `frontend/src/pages/TestPage.tsx`**

```tsx
import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import * as echarts from "echarts";
import { api, BenchTest, Step, wsUrl } from "../api";
import { buildOption } from "../chart";

interface Tick { concurrency: number; requests_done: number; step_pct: number;
  tps_now: number; p95_latency_now_ms: number | null; errors: number;
  elapsed_s: number; }

export default function TestPage() {
  const { id } = useParams();
  const nav = useNavigate();
  const testId = Number(id);
  const [test, setTest] = useState<BenchTest | null>(null);
  const [steps, setSteps] = useState<Step[]>([]);
  const [tick, setTick] = useState<Tick | null>(null);
  const chartRef = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let poll: number | undefined;
    api.getTest(testId).then(t => {
      setTest(t); setSteps(t.steps ?? []);
      if (t.status !== "running") return;
      ws = new WebSocket(wsUrl(testId));
      ws.onmessage = ev => {
        const msg = JSON.parse(ev.data);
        if (msg.type === "snapshot") { setSteps(msg.data.steps); }
        if (msg.type === "tick") setTick(msg.data);
        if (msg.type === "step") setSteps(s => [...s, msg.data]);
        if (msg.type === "status") api.getTest(testId).then(t2 => {
          setTest(t2); setSteps(t2.steps ?? []); setTick(null); });
      };
      ws.onclose = () => {           // fallback poll while still running
        poll = window.setInterval(async () => {
          const t2 = await api.getTest(testId);
          setTest(t2); setSteps(t2.steps ?? []);
          if (t2.status !== "running" && poll) clearInterval(poll);
        }, 5000);
      };
    });
    return () => { ws?.close(); if (poll) clearInterval(poll); };
  }, [testId]);

  useEffect(() => {
    if (!chartRef.current || !test) return;
    chart.current ??= echarts.init(chartRef.current);
    const streaming = test.endpoint_type === "openai";
    chart.current.setOption(buildOption(steps, test.verdict, streaming,
      tick ? { concurrency: tick.concurrency, pct: tick.step_pct } : null), true);
  }, [steps, test, tick]);

  if (!test) return <p>Loading…</p>;
  const running = test.status === "running";
  const v = test.verdict;
  const latLabel = v?.latency_metric === "e2e" ? "p95 E2E" : "p95 TTFT";

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between",
                    alignItems: "center" }}>
        <div>
          <b>{test.endpoint_name} / {test.model}</b> · {test.workload} workload
          {" "}<span className="badge">{test.status.toUpperCase()}
            {running && tick ? ` — step ${tick.concurrency}` : ""}</span>
          {Object.keys(test.flags).map(f =>
            <span key={f} className="badge" style={{ marginLeft: 6 }}>{f}</span>)}
        </div>
        {running &&
          <button className="danger" onClick={() => api.stopTest(testId)}>
            ■ Stop</button>}
        {!running && <span>
          <a href={`/api/tests/${testId}/export.html`}>Export HTML</a>{" · "}
          <a href={`/api/tests/${testId}/export.csv`}>Export CSV</a>{" · "}
          <button className="danger" onClick={async () => {
            if (confirm("Delete this test and all its data?")) {
              await api.deleteTest(testId); nav("/history");
            }}}>Delete</button></span>}
      </div>

      <div className="card" style={{ marginTop: "1rem" }}>
        <div ref={chartRef} style={{ width: "100%", height: 420 }} />
        {!running && (v ? (
          <p className="metric">Sweet spot <b>{v.knee_concurrency}</b> concurrent
            · <b>{Math.round(v.throughput_tps)}</b> tok/s
            · {latLabel} <b>{Math.round(v.p95_latency_ms ?? 0)}</b> ms
            {v.budget && (v.budget.met
              ? <> · budgets hold to <b>{v.budget.max_concurrency}</b> concurrent
                  (limited by {v.budget.limited_by})</>
              : <> · <b style={{ color: "var(--danger)" }}>
                  budget not met at any tested concurrency</b></>)}
          </p>) : (
          <p style={{ color: "var(--muted)" }}>
            No verdict: {test.error ?? "fewer than 3 completed steps, or the load
            generator was saturated."}</p>))}
      </div>

      {running && tick && (
        <div style={{ display: "flex", gap: 12, marginTop: 12 }}>
          {[["Current step", `${tick.concurrency} × · ${tick.requests_done} reqs`],
            ["Throughput now", `${tick.tps_now} tok/s`],
            ["p95 latency now", tick.p95_latency_now_ms == null ? "—"
              : `${tick.p95_latency_now_ms} ms`],
            ["Errors", String(tick.errors)]].map(([k, val]) => (
            <div className="card metric" style={{ flex: 1 }} key={k}>
              <label>{k}</label><b>{val}</b></div>))}
        </div>)}

      {steps.length > 0 && (
        <table style={{ marginTop: "1.25rem" }} className="metric">
          <thead><tr><th>Concurrency</th><th>Requests</th><th>tok/s</th>
            <th>TTFT p50</th><th>TTFT p95</th><th>E2E p50</th><th>E2E p95</th>
            <th>Errors</th></tr></thead>
          <tbody>{steps.map(s => (
            <tr key={s.concurrency}>
              <td>{s.concurrency}</td><td>{s.requests_completed}</td>
              <td>{Math.round(s.throughput_tps ?? 0)}</td>
              <td>{s.ttft_p50_ms ?? "N/A"}</td><td>{s.ttft_p95_ms ?? "N/A"}</td>
              <td>{s.e2e_p50_ms ?? "—"}</td><td>{s.e2e_p95_ms ?? "—"}</td>
              <td>{s.error_count}</td></tr>))}
          </tbody>
        </table>)}
    </div>
  );
}
```

- [ ] **Step 4: Implement `frontend/src/pages/HistoryPage.tsx`**

```tsx
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, BenchTest } from "../api";

export default function HistoryPage() {
  const [tests, setTests] = useState<BenchTest[]>([]);
  const [endpoint, setEndpoint] = useState("");
  useEffect(() => { api.listTests().then(setTests); }, []);

  const names = [...new Set(tests.map(t => t.endpoint_name))];
  const rows = endpoint ? tests.filter(t => t.endpoint_name === endpoint) : tests;

  return (
    <div>
      <h2>History</h2>
      <label>Endpoint filter</label>
      <select value={endpoint} onChange={e => setEndpoint(e.target.value)}>
        <option value="">All</option>
        {names.map(n => <option key={n} value={n}>{n}</option>)}
      </select>
      <table style={{ marginTop: "1rem" }}>
        <thead><tr><th>Endpoint / model</th><th>Workload</th><th>Status</th>
          <th>Sweet spot</th><th>tok/s</th><th>p95 latency</th><th>Started</th>
        </tr></thead>
        <tbody>{rows.map(t => (
          <tr key={t.id}>
            <td><Link to={`/tests/${t.id}`}>{t.endpoint_name} / {t.model}</Link></td>
            <td>{t.workload}</td><td>{t.status}</td>
            <td className="metric">{t.verdict?.knee_concurrency ?? "—"}</td>
            <td className="metric">{t.verdict
              ? Math.round(t.verdict.throughput_tps) : "—"}</td>
            <td className="metric">{t.verdict?.p95_latency_ms != null
              ? `${Math.round(t.verdict.p95_latency_ms)} ms` : "—"}</td>
            <td>{t.started_at}</td></tr>))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 5: Wire routes in `App.tsx`**

```tsx
import NewTestPage from "./pages/NewTestPage";
import TestPage from "./pages/TestPage";
import HistoryPage from "./pages/HistoryPage";
// ...
<Route path="/" element={<NewTestPage />} />
<Route path="/tests/:id" element={<TestPage />} />
<Route path="/history" element={<HistoryPage />} />
```

- [ ] **Step 6: Verify end-to-end against the mock**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Then with backend (`DATA_DIR=/tmp/benchdata python -m bench.main`) and mock (`python -m tools.mockserver.app --port 9000`) running, `npm run dev`: add the mock endpoint, start a test with Advanced → dwell 2 s, watch the curve build, confirm the verdict line, per-step table, exports download, and the run appears in History.

- [ ] **Step 7: Commit**

```bash
git add frontend/src
git commit -m "feat: new-test, live/finished test, and history pages"
```

---

### Task 16: Playwright smoke test

**Files:**
- Create: `frontend/e2e/smoke.spec.ts`, `frontend/playwright.config.ts`

**Interfaces:**
- Consumes: the whole running app + mock server.
- Produces: `npm run e2e` script proving the spec's UI acceptance path: add endpoint → start test → chart gains a point → verdict renders.

- [ ] **Step 1: Install and configure**

```bash
cd frontend
npm install -D @playwright/test
npx playwright install chromium
```

`frontend/playwright.config.ts`:

```typescript
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  timeout: 120_000,
  use: { baseURL: "http://localhost:8080" },
  webServer: [
    { command: "python -m tools.mockserver.app --port 9000 --ttft-ms 10 --tps 2000 --output-tokens 10",
      cwd: "..", port: 9000, reuseExistingServer: true },
    { command: "sh -c 'rm -rf /tmp/bench-e2e && DATA_DIR=/tmp/bench-e2e PORT=8080 python -m bench.main'",
      cwd: "..", port: 8080, reuseExistingServer: false },
  ],
});
```

Add to `frontend/package.json` scripts: `"e2e": "npm run build && playwright test"` (the backend serves `frontend/dist`, so the build must run first).

- [ ] **Step 2: Write `frontend/e2e/smoke.spec.ts`**

```typescript
import { expect, test } from "@playwright/test";

test("add endpoint, run sweep, see verdict", async ({ page }) => {
  await page.goto("/endpoints");
  await page.getByLabel(/name/i).fill("mock");
  await page.getByLabel(/base url/i).fill("http://localhost:9000/v1");
  await page.getByRole("button", { name: /add endpoint/i }).click();
  await expect(page.getByRole("cell", { name: "mock" }).first()).toBeVisible();

  await page.goto("/");
  await page.getByLabel(/model/i).fill("mock-model");
  await page.getByText(/advanced/i).click();
  await page.getByLabel(/max concurrency/i).fill("8");
  await page.getByLabel(/step dwell/i).fill("1");
  await page.getByRole("button", { name: /find sweet spot/i }).click();

  await expect(page).toHaveURL(/\/tests\/\d+/);
  await expect(page.getByText(/RUNNING/)).toBeVisible();
  await expect(page.getByText(/sweet spot/i)).toBeVisible({ timeout: 90_000 });
  await expect(page.locator("table tbody tr").first()).toBeVisible();

  await page.goto("/history");
  await expect(page.getByRole("link", { name: /mock \/ mock-model/i }))
    .toBeVisible();
});
```

Note: the labels used by `getByLabel` require `htmlFor`/`id` pairs — add `id` attributes to the inputs in `EndpointsPage.tsx`/`NewTestPage.tsx` and `htmlFor` on their labels if the selectors fail; adjust selectors to the actual DOM rather than weakening assertions.

- [ ] **Step 3: Run it**

Run: `cd frontend && npm run e2e`
Expected: 1 passed

- [ ] **Step 4: Commit**

```bash
git add frontend/e2e frontend/playwright.config.ts frontend/package.json frontend/package-lock.json
git commit -m "test: playwright smoke test for the full sweep flow"
```

---

### Task 17: Container, SIGTERM, and final verification

**Files:**
- Create: `Dockerfile` (replace existing), `.dockerignore` (replace), `tests/e2e_container.sh`
- Modify: `docker-compose.yml` (replace), `README.md` (replace)

**Interfaces:**
- Consumes: everything.
- Produces: the shippable image; `docker run -p 8080:8080 -v bench-data:/data inference-benchmark`.

- [ ] **Step 1: Write `Dockerfile`**

```dockerfile
# Stage 1: frontend
FROM node:22-slim AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

# Stage 2: backend
FROM python:3.12-slim
RUN useradd -m bench
WORKDIR /app
COPY pyproject.toml ./
COPY bench ./bench
RUN pip install --no-cache-dir .
COPY --from=frontend /build/dist ./frontend/dist
USER bench
ENV DATA_DIR=/data PORT=8080
EXPOSE 8080
CMD ["python", "-m", "bench.main"]
```

- [ ] **Step 2: Write `.dockerignore`**

```
frontend/node_modules
frontend/dist
frontend/e2e
tests
tools
docs
.git
.superpowers
**/__pycache__
*.pyc
```

- [ ] **Step 3: Replace `docker-compose.yml`**

```yaml
services:
  benchmark:
    build: .
    ports: ["8080:8080"]
    volumes: ["bench-data:/data"]
volumes:
  bench-data:
```

- [ ] **Step 4: Write `tests/e2e_container.sh`**

```bash
#!/usr/bin/env bash
# Container e2e: build, run against the mock, drive one sweep via REST.
set -euo pipefail
docker build -t inference-benchmark:test .
python -m tools.mockserver.app --port 9000 --ttft-ms 10 --tps 2000 --output-tokens 10 &
MOCK_PID=$!
docker run -d --rm --name bench-e2e -p 18080:8080 \
  --add-host host.docker.internal:host-gateway inference-benchmark:test
trap 'kill $MOCK_PID; docker stop bench-e2e >/dev/null 2>&1 || true' EXIT
sleep 3
curl -sf http://localhost:18080/healthz | grep '"ok"'
EP=$(curl -sf -X POST http://localhost:18080/api/endpoints \
  -H 'Content-Type: application/json' \
  -d '{"name":"mock","type":"openai","base_url":"http://host.docker.internal:9000/v1"}' \
  | python -c 'import json,sys; print(json.load(sys.stdin)["id"])')
TID=$(curl -sf -X POST http://localhost:18080/api/tests \
  -H 'Content-Type: application/json' \
  -d "{\"endpoint_id\":$EP,\"model\":\"mock-model\",\"workload\":\"chat\",
       \"settings\":{\"dwell_s\":1,\"min_requests\":3,\"max_concurrency\":8}}" \
  | python -c 'import json,sys; print(json.load(sys.stdin)["id"])')
for i in $(seq 1 90); do
  STATUS=$(curl -sf http://localhost:18080/api/tests/$TID \
    | python -c 'import json,sys; print(json.load(sys.stdin)["status"])')
  [ "$STATUS" != "running" ] && break
  sleep 1
done
[ "$STATUS" = "completed" ] || { echo "FAIL: status=$STATUS"; exit 1; }
curl -sf http://localhost:18080/api/tests/$TID | grep -q knee_concurrency
curl -sf http://localhost:18080/api/tests/$TID/export.html | grep -q echarts
echo "PASS"
```

Run: `chmod +x tests/e2e_container.sh && ./tests/e2e_container.sh`
Expected: `PASS`

- [ ] **Step 5: Replace `README.md`**

```markdown
# Inference Benchmark

Finds the sweet spot between concurrency, throughput (tokens/sec), and latency
for an LLM inference endpoint (OpenAI-compatible or AskSage).

## Run

    docker run -p 8080:8080 -v bench-data:/data inference-benchmark

Open http://localhost:8080 — add an endpoint, pick a model and workload, hit
**Find sweet spot**. The tool sweeps concurrency 1 → 2 → 4 → …, stops once
throughput flattens, and reports the knee, the sweet zone, and (if you set
latency budgets) the max concurrency that stays within them.

## Development

    pip install -e ".[dev]" && pytest          # backend
    cd frontend && npm install && npm run dev  # frontend (proxies to :8080)
    python -m tools.mockserver.app --port 9000 # fake inference service

Spec: docs/superpowers/specs/2026-08-02-inference-benchmark-simplified-design.md
```

- [ ] **Step 6: Full verification**

Run: `pytest -q && cd frontend && npx tsc --noEmit && npm run build && npm run e2e && cd .. && ./tests/e2e_container.sh`
Expected: everything green.

- [ ] **Step 7: Commit**

```bash
git add Dockerfile .dockerignore docker-compose.yml README.md tests/e2e_container.sh
git commit -m "feat: container build, compose, e2e script, README"
```

---

## Spec coverage map

| Spec section | Task(s) |
|---|---|
| §1 product shape / cut list | whole plan (no cut feature has a task) |
| §2.1 New Test page | 15 |
| §2.2 Test page live + finished | 15 (chart 15.1, states 15.3) |
| §2.3 History | 15 |
| §2.4 Endpoints + probe | 10, 14 |
| §3.1 execution / §3.2 early stop | 9 |
| §3.3 workload presets | 5 |
| §3.4 metrics + verdict | 3, 4 |
| §3.5 honesty guards | 9 (saturation, no retries), 3 (estimated tokens) |
| §4 adapters | 6, 7 |
| §5 data model | 2 |
| §6 HTTP API + WS | 10, 11, 12 |
| §7 error handling (fail fast, restart recovery, SIGTERM) | 9, 10 (startup hook), 11, 17 |
| §8 security | 2 (crypto), 10 (write-only keys), 17 (non-root) |
| §9 testing strategy | 8 (mock), unit tests throughout, 9 (integration), 16 (Playwright), 17 (container e2e) |
| §10 container/repo layout | 1, 17 |
| §11 acceptance criteria | 9 (AC2), 4+15 (AC3), 7 (AC4), 11 (AC5), 12+17 (AC6), 16+17 (AC1, AC7) |








