# Inference Benchmark — Simplified Design

**Version:** 2.0 · **Date:** 2026-08-02 · **Status:** Approved design

Supersedes `2026-08-01-inference-benchmark-design.md` and
`2026-08-01-inference-benchmark-web-ui-design.md`. The build from those specs failed in
practice: choosing a test was unintuitive and the results didn't answer the user's
question. This design keeps the deployment story, the adapter isolation, the measurement
honesty rules, and the visualization style — and cuts everything else down to one job:

> **Find the sweet spot between concurrency, throughput (tokens/sec), and latency for an
> inference endpoint.**

The existing `bench/` and `frontend/` code is replaced, not refactored (fresh start).

---

## 1. Product shape

There is exactly **one kind of test**: an automatic concurrency sweep. The user picks an
endpoint, a model, a workload preset, and optionally latency budgets, then hits **Start**.
The tool steps concurrency upward (1, 2, 4, 8, …), measures throughput and latency at
each step, stops as soon as it has the answer, and renders a verdict:

> Sweet spot **32** concurrent · **2,340 tok/s** · p95 TTFT **820 ms** ·
> budgets hold to **40** concurrent (limited by E2E)

No modes, no workload grids, no weight maps, no batches, no compare view.

### Explicitly cut from v1 (decisions, not omissions)

Fixed-concurrency and arrival-rate modes; batch runs; the Compare view; custom prompt
packs; vision prompts; saved presets; the 4×4 focus/tier workload grid; run archiving;
forecasting beyond the verdict; HTTP Basic auth; the separate runner process. The adapter
interface remains the extension point if any of these return.

---

## 2. UI

Single-page app (Vite + React + TypeScript + ECharts), served as static files by the
backend. Slim top nav: **New Test · History · Endpoints**. The browser talks only to this
app's own API; API keys never reach the browser. The desktop content shell expands to
1400 px for data-heavy views. Overflowing history tables keep the Actions column pinned
to the right so row controls remain accessible without horizontal scrolling.

### 2.1 New Test (`/`)

Two panels side by side:

- **Left — the form** (four fields + advanced disclosure):
  - Endpoint (dropdown of saved endpoints, with "add new" linking to Endpoints).
  - Model (dropdown filled by the endpoint's last probe; free-text fallback).
  - Workload preset: **Chat** (default) / **Long context** / **Generation** (§3.3).
  - Latency budgets, both optional: **p95 TTFT (ms)** and **p95 E2E (ms)**. The TTFT
    field is disabled with an explanatory note for non-streaming (AskSage) endpoints.
    Placeholder values are examples, not implicit defaults. With both fields blank, the
    adaptive guard is 5× concurrency-1 p95 latency (TTFT for streaming, E2E otherwise).
  - *Advanced* (collapsed): max concurrency ceiling (default 128), step dwell seconds
    (default 45), request timeout (default 180 s), temperature (default 0.0).
- **Right — the test plan panel**: restates the run in plain English and updates live as
  fields change: "Will sweep concurrency 1 → 2 → 4 → … → 64, ~45 s per step. Workload:
  ~500-token chat prompts, ~300-token answers. Estimated total: ~8 minutes. Stops early
  once throughput flattens." When a latency budget is set, this copy instead explains
  that the sweep continues while budgets hold and adds one midpoint refinement after the
  first crossing. Crossing the adaptive guard also adds a midpoint between the last
  under-guard and first over-guard concurrency. Validation errors surface here. The **Start** button lives
  in this panel; starting navigates to `/tests/:id`. A second start while a test is
  active returns 409 and the UI links to the running test.

### 2.2 Test page (`/tests/:id`) — one page, two states

**Running state:**

- Header: endpoint / model, workload, status chip ("RUNNING — step 16 concurrent"),
  danger-styled **Stop** button.
- **Hero chart** (the centerpiece, full width): x = concurrency (log2), left y =
  throughput (output tok/s, solid line + measured points), right y = p95 latency. E2E is
  always plotted and TTFT is plotted alongside it for streaming endpoints so users can
  see prefill/queueing and total response time independently. The left throughput axis
  uses blue titles, labels, ticks, and spine; the right millisecond latency axis uses amber,
  providing both positional and color differentiation. The curves gain one point
  per completed step. A stationary progress strip above the chart is always visible while
  the test is running: it shows an indeterminate preparation/warm-up state before the first
  measurement tick, then the current concurrency and percent complete. A label-free dotted
  chart marker identifies that concurrency without colliding with the y-axis at the first step.
  Horizontal labeled lines show every configured latency budget, or the calculated adaptive
  latency guard when no explicit budget was configured. Threshold lines and labels do not
  animate; one-second progress ticks update the status strip without rebuilding the chart.
- Stat tiles below the chart, updated ~1/s over WebSocket: current step (concurrency +
  requests done), throughput now, p95 TTFT now (streaming only), p95 E2E now, errors.
- Footer line: completed steps, elapsed, estimated remaining ("stops early when the curve
  flattens").

**Finished state (same page, same chart):**

- The **sweet zone** (knee ± one step) is shaded directly on the hero chart; the knee has
  a vertical marker. If budgets were set, the interpolated budget-crossing point is
  marked on the latency line.
- Verdict caption line directly under the chart, plain English with the numbers bold
  (§3.4). Suppressed with an explanatory sentence when fewer than 3 steps completed or
  the run is flagged client-saturated.
- **Per-step table**: concurrency, requests, throughput tok/s, TTFT p50/p95, E2E p50/p95,
  error count. Non-streaming endpoints show TTFT columns as "N/A".
- **Prompt analysis**: an interactive prompt × concurrency heatmap switchable between
  median/p95 E2E, median/p95 TTFT, and median output rate. Its default color mode shows
  degradation relative to each prompt's lowest-concurrency measurement so intrinsic answer
  length does not obscure load sensitivity; an absolute-value mode remains available. Clicking
  a heatmap cell selects the concurrency for the detailed per-prompt table, and low-sample p95
  views display a confidence warning. The table includes the prompt task, request/error counts,
  TTFT/E2E p50/p95, median token
  counts, and median output rate. Non-streaming output rate is explicitly marked as an
  E2E-based approximation. Each prompt-table row expands to show the original bundled
  prompt text (without the per-request cache-buster prefix). This analysis is derived
  from stored request rows and bundled workload data, and works for existing history
  without storing request prompt copies or model response text.
- Flags render as badges with one-sentence tooltips: `client-saturated`,
  `tokens-estimated`, `stopped early`, `non-streaming`.
- Actions: **Export HTML** (self-contained file, charts inlined, no network needed),
  **Export CSV** (per-request rows), Delete.

A stopped test keeps its completed steps and still gets a verdict if ≥ 3 steps finished.

### 2.3 History (`/history`)

Sortable, filterable table (endpoint, model, date range): status dot, endpoint / model, workload,
configured p95 TTFT and E2E budgets, verdict summary (sweet-spot concurrency · tok/s ·
p95 latency), started, duration. Unset budgets display as an em dash. Row
click opens `/tests/:id`. Every data-column header toggles ascending/descending order;
the default is newest first. Row action: Delete (confirm; cascades to steps and requests,
then removes the row without a page reload). Running tests must be stopped before deletion.

### 2.4 Endpoints (`/endpoints`)

Table of saved endpoints; add/edit in a side drawer:

| Field | Notes |
|---|---|
| `name` | unique display name |
| `type` | `openai` \| `asksage` |
| `base_url` | e.g. `https://vllm.internal:8000/v1` or AskSage server-API root |
| `api_key` | write-only; stored encrypted; "replace key" affordance when set |
| `default_model` | selectable from discovered models, with free-text fallback |
| `verify_tls` | default true; per-endpoint only |

**Fetch available models** uses the unsaved form connection settings and performs only
model discovery, allowing a default to be selected before the endpoint is created. While
editing, a blank key reuses the encrypted stored key. **Test connection** runs the full
adapter probe server-side after save: auth check, model list (where supported), 1-token
completion, and streaming probe. It renders reachability, latency, detected models, and
streaming support, and stores `supports_streaming`.
AskSage endpoints show a permanent note: "Non-streaming API — TTFT is unavailable; the
E2E latency budget applies."

---

## 3. The sweep engine

### 3.1 Execution

- Runs as an **asyncio task inside the FastAPI process** (one user, one active test —
  process isolation was cut). httpx for all outbound calls.
- **Warmup**: 3 sequential requests once at sweep start, discarded from all metrics.
- **Steps**: concurrency 1, 2, 4, 8, … doubling up to the ceiling. Each step runs
  closed-loop — N workers, each: draw next prompt, send, await completion, repeat.
- **Dwell**: a step ends only after ≥ 45 s (configurable) *and* ≥ 20 completed requests.
- Between steps: drain in-flight requests, then proceed (no cooldown needed at this
  scale).

### 3.2 Early stop

After each completed step, the stop policy depends on whether the user supplied an
explicit TTFT and/or E2E budget:

1. Step error rate > 10% always stops the sweep (the verdict names the failure:
   "errors spiked at 64
   concurrent").
2. **With no explicit latency budget**, stop when throughput gain vs the previous step
   is < 10% for two consecutive steps, or p95 latency (TTFT if streaming, else E2E)
   exceeds **5×** the concurrency-1 baseline. A 5× guard crossing runs the same single
   arithmetic-midpoint refinement used for an explicit budget crossing, and over-guard
   measurements are excluded from sweet-spot selection.
3. **With any explicit latency budget**, the explicit budget takes precedence over the
   flattening and 5× guards. Continue doubling while every applicable budget holds.
   On the first step that exceeds a budget, run exactly one final refinement step at the
   arithmetic midpoint concurrency between the exceeded step and the preceding step
   (when an integer midpoint exists), then stop. Both bracketing steps and the refinement
   step remain in the results.
4. Concurrency ceiling reached, or the user hits Stop.

The exact terminal reason and any refinement concurrency are persisted with the test,
not only emitted as transient live events.

### 3.3 Workload presets

Fixed, bundled prompt sets (~20 prompts each), generated deterministically by a repo
script from committed public-domain/permissively-licensed corpus text and shipped in the
image. Prompts cycle in a seeded shuffle. A **cache-buster** unique prefix
(`[req {uuid4}] `) is always prepended so prefix-caching servers can't reuse prefill.

| Preset | Prompt tokens | Output target | Stresses |
|---|---|---|---|
| **Chat** (default) | ~500 | ~300 (`max_tokens` 400) | balanced, chat-like |
| **Long context** | ~4,000 | ~200 (`max_tokens` 256) | prefill |
| **Generation** | ~80 | ~1,000 (`max_tokens` 1024) | decode |

Generation prompts carry explicit length instructions so they fill their budget.

### 3.4 Metrics and verdict

Per request (monotonic clock): TTFT (`t_first_token − t_send`, streaming only), E2E
latency, output tokens/s, prompt/output token counts — from the API `usage` object when
present, else tiktoken (`o200k_base`) with a `tokens_estimated` flag badged in the UI.

Per step: total output tok/s (throughput), p50/p95 TTFT, p50/p95 E2E, error rate.

**Verdict**, computed at finish from the per-step aggregates:

- **Knee**: the last step whose throughput gain over the previous step was ≥ 10%. When
  budgets are set, over-budget measurements locate the boundary but are excluded from
  sweet-spot selection; the knee and sweet zone are selected from measurements satisfying
  every applicable budget.
- **Sweet spot line**: knee concurrency, its throughput, its p95 latency.
- **Budget line** (when budgets set): the max concurrency at which every set budget still
  holds, linearly interpolated between the two bracketing steps; when both budgets are
  set, the binding one is named ("limited by E2E"). Never extrapolated beyond the last
  measured step. If the ceiling is reached without a crossing, the UI says "budget held
  through highest tested concurrency" rather than implying that the last point is a
  discovered limit.
- Verdict suppressed (with an explanatory sentence) if < 3 completed steps or the run is
  flagged `client_saturated`.

### 3.5 Honesty guards

- **Client-saturation self-check**: once per second the engine samples event-loop lag
  (100 ms scheduled-callback overshoot) and process CPU (psutil). Loop lag > 100 ms or
  CPU > 90% for 5 consecutive samples flags the test `client_saturated`; the UI banners
  it and the verdict is suppressed.
- No silent retries, ever. Failures are recorded and counted.
- Non-streaming (AskSage) runs: throughput is computed from E2E and labeled "estimated";
  TTFT shown as N/A; all TTFT-based logic falls back to E2E.

---

## 4. Adapters

One small interface, one file per dialect (`bench/adapters/`): `probe()`,
`list_models()`, `execute(prompt, params, timing) -> RequestResult`. Wire-format
knowledge lives only here.

- **OpenAI-compatible** (`openai.py`): `POST {base_url}/chat/completions`, Bearer auth,
  streaming with `stream_options: {include_usage: true}` (tolerate servers that omit
  usage — fall back to tiktoken). Model list from `GET {base_url}/models`. Covers vLLM,
  Ollama, TGI, llama.cpp, LM Studio, etc.
- **AskSage** (`asksage.py`): `POST {base_url}/query` with `x-access-tokens` header, body
  `{message, model, temperature, dataset: "none", live: 0}`; output text from the
  response's `message` field; HTTP 200 with body `status ≠ 200` is an error. Model list
  via `POST {base_url}/get-models` where available, else free-text model entry.
  Non-streaming: §3.5 fallbacks apply.

---

## 5. Data model

SQLite at `$DATA_DIR/benchmark.db`, WAL mode, `PRAGMA user_version` migrations.

- **endpoints** — id, name, type, base_url, api_key_encrypted, default_model,
  verify_tls, supports_streaming, created_at.
- **tests** — id, endpoint_id, model, workload, budget_ttft_ms?, budget_e2e_ms?,
  settings_json (frozen advanced settings + seed), status
  (`running|completed|stopped|failed`), flags_json (`client_saturated`,
  `tokens_estimated`, `stopped_early`), verdict_json (knee, sweet zone, budget crossing,
  headline numbers), started_at, finished_at.
- **steps** — test_id, concurrency, requests_completed, throughput_tps, ttft_p50_ms?,
  ttft_p95_ms?, e2e_p50_ms, e2e_p95_ms, error_count, started_at, duration_s. Primary key
  (test_id, concurrency). The chart and table render from this table.
- **requests** — id, test_id, concurrency, prompt_id, t_send_wall, ttft_ms?, e2e_ms,
  prompt_tokens, output_tokens, tokens_estimated, error_class?, error_detail?. Ground
  truth for CSV export and re-aggregation. Indexed on (test_id, concurrency).

Test deletion cascades to steps and requests.

---

## 6. HTTP API

All under `/api`, JSON; errors as `{"error": {"code", "message"}}`.

| Method & path | Purpose |
|---|---|
| `GET/POST /api/endpoints`, `PUT/DELETE /api/endpoints/{id}` | CRUD; api_key write-only (`has_api_key` boolean returned) |
| `POST /api/endpoints/{id}/probe` | connection test (§2.4) |
| `POST /api/tests` | start a sweep; **409** if one is active |
| `GET /api/tests?endpoint&model&from&to` | history |
| `GET /api/tests/{id}` | config + steps + verdict + flags |
| `GET /api/tests/{id}/prompt-analysis` | per-prompt/per-concurrency request aggregates for the heatmap and table |
| `POST /api/tests/{id}/stop` | graceful stop, keep completed steps |
| `DELETE /api/tests/{id}` | cascade delete |
| `GET /api/tests/{id}/export.html` / `export.csv` | §2.2 |
| `WS /ws/tests/{id}` | live events (below); replays state on connect |
| `GET /healthz` | `{status, active_test_id?, db_ok}` |

WebSocket messages:

```json
{"type": "tick",   "data": {"concurrency": 16, "requests_done": 43, "step_pct": 67, "tps_now": 1890, "p95_latency_now_ms": 410, "errors": 0, "elapsed_s": 220, "eta_s": 240}}
{"type": "step",   "data": { /* a finished steps row */ }}
{"type": "flag",   "data": {"flag": "client_saturated"}}
{"type": "status", "data": {"status": "completed", "verdict": { /* verdict_json */ }}}
```

On `status` terminal messages the page re-renders into the finished state without a
refresh. If the socket drops, poll `GET /api/tests/{id}` every 5 s.

---

## 7. Error handling

- Request `error_class`: `timeout`, `connect`, `http`, `bad_response`. Counted into the
  step's error rate; > 10% triggers early stop (§3.2).
- **Fail fast**: if the first step's first requests all fail (auth, DNS, bad URL), the
  test fails immediately with the probe-style error message — no long wait to learn the
  key was wrong.
- Server restart with a test `running` → marked `stopped` at startup; completed steps
  kept; verdict computed if ≥ 3 steps.
- SIGTERM (container stop) stops an active test via the normal path within Docker's grace
  period.

---

## 8. Security

- API keys encrypted at rest (Fernet); key material from `SECRET_KEY` env var or a
  generated `$DATA_DIR/.secret` (0600). Keys are write-only through the API and never
  appear in exports or logs.
- `verify_tls=false` applies per endpoint only.
- Container runs as a non-root user; only `$DATA_DIR` is writable.

---

## 9. Testing strategy

1. **Mock inference server** (`tools/mockserver/`): small FastAPI app speaking both
   dialects with configurable TTFT, token rate, output length, and error rate (CLI flags
   and per-request prompt directives). CI fixture and self-validation tool.
2. **Unit**: knee detection and budget interpolation against synthetic curves; percentile
   math against hand-computed fixtures; both adapters' parsing against recorded wire
   fixtures (usage present/absent, malformed stream, AskSage body-status error); seeded
   shuffle reproducibility; encryption round-trip.
3. **Integration**: a real 3-step sweep against the mock server, asserting measured
   TTFT/TPS and the verdict land within ±10% of the mock's configured behavior — the
   guard against "results were useless" recurring. Early-stop rules exercised by shaping
   the mock (flat throughput, error injection).
4. **E2E (CI)**: container build, sweep via REST, HTML export renders.
5. **Frontend**: one Playwright smoke test — add endpoint → start test → chart gains a
   point → verdict renders.

---

## 10. Container and repo layout

Multi-stage Dockerfile: `node:22-slim` builds the SPA; `python:3.12-slim` runs the
backend with the SPA and bundled workloads copied in; non-root user. Env vars: `PORT`
(8080), `DATA_DIR` (`/data`), `SECRET_KEY` (optional), `LOG_LEVEL` (`info`).

Run: `docker run -p 8080:8080 -v bench-data:/data inference-benchmark`

```
bench/
  api/          # FastAPI routers, websocket, static serving
  engine/       # sweep loop, early stop, metrics, verdict
  adapters/     # base.py, openai.py, asksage.py
  storage/      # sqlite, migrations
  reports/      # HTML/CSV export
  data/         # bundled workload prompts (committed, generated by tools/build_workloads.py)
frontend/       # Vite + React + TS SPA (4 pages)
tools/
  build_workloads.py
  corpus/
  mockserver/
tests/
Dockerfile
```

---

## 11. Acceptance criteria

1. `docker run` with a volume yields the UI; history survives container recreation.
2. Against the mock at TTFT 250 ms / 40 tok/s per request, a sweep completes, stops
   early once flat, and the verdict's throughput and latency are within ±10% of the
   mock's configured behavior.
3. Setting a p95 E2E or TTFT budget makes the sweep continue while the budget holds. The
   first exceeded doubling step is followed by one midpoint refinement measurement, and
   the budget line is interpolated from the refined measurements.
4. With both budget fields blank, crossing the displayed adaptive 5× latency guard also
   produces one midpoint refinement and cannot select an over-guard step as the sweet spot.
5. An AskSage-endpoint sweep completes with TTFT shown as N/A and the E2E budget
   applied.
6. Stopping mid-sweep keeps completed steps and renders a verdict when ≥ 3 steps
   finished; restarting the container mid-sweep yields `stopped` with steps intact.
7. The HTML export opens from disk with no network access and renders the hero chart.
8. Streaming results plot p95 TTFT and p95 E2E simultaneously in the live UI and offline
   HTML export.
9. All test suites in §9 pass in CI.
