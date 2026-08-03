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


def create_endpoint(conn: sqlite3.Connection, data: dict) -> dict:
    cur = conn.execute(
        """INSERT INTO endpoints(name,type,base_url,api_key_encrypted,default_model,verify_tls)
           VALUES(:name,:type,:base_url,:api_key_encrypted,:default_model,:verify_tls)""",
        {"api_key_encrypted": None, "default_model": None, "verify_tls": 1, **data},
    )
    conn.commit()
    return get_endpoint(conn, cur.lastrowid)


def get_endpoint(conn, endpoint_id: int) -> dict | None:
    return _row(conn.execute("SELECT * FROM endpoints WHERE id=?", (endpoint_id,)).fetchone())


def list_endpoints(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM endpoints ORDER BY name")]


def update_endpoint(conn, endpoint_id: int, data: dict) -> dict:
    if data:
        allowed = {"name", "base_url", "api_key_encrypted", "default_model",
                   "verify_tls", "supports_streaming"}
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(f"unknown endpoint fields: {sorted(unknown)}")
        cols = ", ".join(f"{key}=:{key}" for key in data)
        conn.execute(f"UPDATE endpoints SET {cols} WHERE id=:id", {**data, "id": endpoint_id})
        conn.commit()
    return get_endpoint(conn, endpoint_id)


def delete_endpoint(conn, endpoint_id: int) -> None:
    conn.execute("DELETE FROM endpoints WHERE id=?", (endpoint_id,))
    conn.commit()


def create_test(conn, data: dict) -> dict:
    params = {"budget_ttft_ms": None, "budget_e2e_ms": None, **data}
    params["settings_json"] = json.dumps(data.get("settings", {}))
    cur = conn.execute(
        """INSERT INTO tests(endpoint_id,model,workload,budget_ttft_ms,budget_e2e_ms,settings_json)
           VALUES(:endpoint_id,:model,:workload,:budget_ttft_ms,:budget_e2e_ms,:settings_json)""",
        params,
    )
    conn.commit()
    return get_test(conn, cur.lastrowid)


def get_test(conn, test_id: int) -> dict | None:
    row = _row(conn.execute("SELECT * FROM tests WHERE id=?", (test_id,)).fetchone())
    if row is None:
        return None
    row["settings"] = json.loads(row.pop("settings_json") or "{}")
    row["flags"] = json.loads(row.pop("flags_json") or "{}")
    raw_verdict = row.pop("verdict_json")
    row["verdict"] = json.loads(raw_verdict) if raw_verdict else None
    return row


def list_tests(conn, endpoint_id: int | None = None, model: str | None = None) -> list[dict]:
    query, args, clauses = "SELECT id FROM tests", [], []
    if endpoint_id is not None:
        clauses.append("endpoint_id=?")
        args.append(endpoint_id)
    if model:
        clauses.append("model=?")
        args.append(model)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY started_at DESC, id DESC"
    return [get_test(conn, r["id"]) for r in conn.execute(query, args)]


def finish_test(conn, test_id: int, status: str, verdict: dict | None, flags: dict) -> None:
    conn.execute(
        """UPDATE tests SET status=?, verdict_json=?, flags_json=?,
           finished_at=datetime('now') WHERE id=?""",
        (status, json.dumps(verdict) if verdict else None, json.dumps(flags), test_id),
    )
    conn.commit()


def set_flag(conn, test_id: int, flag: str) -> None:
    test = get_test(conn, test_id)
    flags = {**test["flags"], flag: True}
    conn.execute("UPDATE tests SET flags_json=? WHERE id=?", (json.dumps(flags), test_id))
    conn.commit()


def delete_test(conn, test_id: int) -> None:
    conn.execute("DELETE FROM tests WHERE id=?", (test_id,))
    conn.commit()


def mark_running_tests_stopped(conn) -> list[int]:
    ids = [r["id"] for r in conn.execute("SELECT id FROM tests WHERE status='running'")]
    conn.execute("UPDATE tests SET status='stopped', finished_at=datetime('now') WHERE status='running'")
    conn.commit()
    return ids


def active_test_id(conn) -> int | None:
    row = conn.execute("SELECT id FROM tests WHERE status='running' LIMIT 1").fetchone()
    return row["id"] if row else None


STEP_COLS = ("test_id", "concurrency", "requests_completed", "throughput_tps",
             "ttft_p50_ms", "ttft_p95_ms", "e2e_p50_ms", "e2e_p95_ms",
             "error_count", "started_at", "duration_s")


def insert_step(conn, step: dict) -> None:
    cols = ",".join(STEP_COLS)
    placeholders = ",".join(f":{c}" for c in STEP_COLS)
    conn.execute(f"INSERT INTO steps({cols}) VALUES({placeholders})",
                 {c: step.get(c) for c in STEP_COLS})
    conn.commit()


def list_steps(conn, test_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM steps WHERE test_id=? ORDER BY concurrency", (test_id,))]


REQ_COLS = ("test_id", "concurrency", "prompt_id", "t_send_wall", "ttft_ms", "e2e_ms",
            "prompt_tokens", "output_tokens", "tokens_estimated", "error_class", "error_detail")


def insert_request(conn, request: dict) -> None:
    cols = ",".join(REQ_COLS)
    placeholders = ",".join(f":{c}" for c in REQ_COLS)
    conn.execute(f"INSERT INTO requests({cols}) VALUES({placeholders})",
                 {c: request.get(c) for c in REQ_COLS})
    conn.commit()


def list_requests(conn, test_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM requests WHERE test_id=? ORDER BY id", (test_id,))]
