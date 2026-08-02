# Inference Benchmark — Specification Guide

**Version:** 1.0 · **Date:** 2026-08-01 · **Status:** Approved design

A companion document, `2026-08-01-inference-benchmark-web-ui-design.md`, specifies the web
interface in detail. Implement both together; this document is authoritative for behavior,
the companion for the UI.

---

## 1. Overview

Inference Benchmark is a self-contained, containerized tool for measuring the performance of
on-prem LLM inference services that expose **OpenAI-compatible** or **AskSage-compatible**
HTTP APIs. It generates controlled load from a bundled, versioned prompt dataset, measures
latency and throughput with per-request precision, stores every run in a persistent history,
and serves a web interface for configuring tests, watching runs live, and producing reports,
comparisons, and capacity forecasts.

### Goals

- Measure tokens/second, time-to-first-token (TTFT), inter-token latency, end-to-end
  latency, prompt-processing (prefill) rate, requests/second, and error rates.
- Characterize throughput at various **concurrency levels**, **difficulty tiers** (context /
  output size), and **focus areas** (large text analysis, large code analysis, raw
  generation speed, conversational mix).
- Support performance forecasting: find the saturation point of a service and estimate
  capacity at a latency SLO.
- Persist all runs; compare runs across time, services, and configurations.
- Deploy as **one container**: `docker run -p 8080:8080 -v bench-data:/data inference-benchmark`.

### Non-goals (v1)

- Output *quality* evaluation (correctness scoring of model answers).
- Distributed multi-machine load generation.
- Scheduled/recurring runs (future work, §17).
- More than one active run/batch at a time.

### Hard requirements

- **R1 — Server-side origination.** Every request to an inference engine is initiated by the
  backend inside the container. The browser communicates only with this app's own REST and
  WebSocket API. API keys are never sent to the browser (write-only from the UI).
- **R2 — Measurement integrity.** The load generator runs in a separate OS process from the
  web server, monitors its own saturation (§8.7), and flags any run where the client may
  have been the bottleneck.
- **R3 — Reproducibility.** A run's full configuration, prompt-dataset version, and seed are
  frozen into the run record. Re-running a config reproduces the same prompt sequence.
- **R4 — Ground truth.** One database row per request with raw timings and token counts.
  Every chart and aggregate must be derivable from these rows.

---

## 2. Glossary and metric definitions

All timestamps are taken with a monotonic clock (`time.perf_counter_ns()`). Wall-clock time
(`time.time()`) is recorded once per request for display only, never used in arithmetic.

Per-request timestamps recorded by the runner:

| Symbol | Moment |
|---|---|
| `t_send` | immediately before the HTTP request is handed to the transport |
| `t_first_chunk` | first SSE chunk of any kind received (streaming only) |
| `t_first_token` | first chunk containing non-empty content (streaming only) |
| `t_last_token` | last chunk containing content |
| `t_done` | response fully received and closed |

Derived per-request metrics:

| Metric | Definition | Requires streaming |
|---|---|---|
| **TTFT** | `t_first_token − t_send` | yes |
| **E2E latency** | `t_done − t_send` | no |
| **Generation TPS** | `output_tokens ÷ (t_last_token − t_first_token)`; undefined if `output_tokens < 2` | yes |
| **TPOT** (time per output token) | `(t_done − t_first_token) ÷ (output_tokens − 1)` | yes |
| **ITL** (inter-token latency) | deltas between successive content chunks; store mean, p50, p99 per request | yes |
| **Prompt processing rate** | `prompt_tokens ÷ TTFT` — a **lower bound** (TTFT includes queueing + network). Label it as such everywhere it appears. | yes |
| **Estimated TPS** (non-streaming) | `output_tokens ÷ E2E latency` — labeled "estimated" | no |

Aggregate metrics (computed over the steady-state phase by default, per run and per
1-second bucket):

- **System throughput**: total output tokens completed per second across all requests —
  the headline capacity number. Also: total prompt tokens processed per second.
- **Requests/sec** completed; **in-flight concurrency** (sampled each second).
- **Error rate** by error class (§13).
- p50 / p90 / p95 / p99 for every per-request metric, using linear interpolation between
  order statistics (numpy default percentile method).

Token counts come from the API's `usage` object when present. Otherwise count client-side
with `tiktoken` (`o200k_base`) and set `tokens_estimated = true` on the request row; the UI
badges these values.

---

## 3. Architecture

One container, one image. Three logical components:

```
┌─────────────────────────────────────────────────────────────┐
│  Container                                                  │
│                                                             │
│  ┌──────────────┐   spawn / stdout+socket   ┌────────────┐  │
│  │  Web layer    │──────────────────────────▶│  Runner    │  │
│  │  FastAPI      │◀──────────────────────────│  process   │  │
│  │  + static SPA │   progress events         │  (asyncio  │  │
│  └──────┬───────┘                            │  + httpx)  │  │
│         │                                    └─────┬──────┘  │
│         │              ┌──────────────┐            │         │
│         └─────────────▶│ SQLite /data │◀───────────┘         │
│                        └──────────────┘                      │
└─────────────────────────────────────────────────────────────┘
         ▲  REST + WebSocket                 │ HTTPS
         │                                   ▼
      Browser                     Inference services (OpenAI / AskSage APIs)
```

- **Web layer** — FastAPI app. Serves the REST API (§10), the WebSocket for live run
  updates, and the built React SPA as static files. Owns the SQLite connection for reads
  and all non-run writes. Enforces the single-active-run rule.
- **Runner** — a separate OS process spawned per run (`multiprocessing.Process` with the
  `spawn` start method, or `subprocess` invoking `python -m bench.runner --run-id N`).
  Executes the load schedule with asyncio + httpx, writes request rows and time-series
  buckets to SQLite (WAL mode makes concurrent writer + readers safe), and streams progress
  events to the web layer over a local socket. The runner is an importable package
  (`bench.engine`) with a thin CLI entry point, so headless/CI use is possible.
- **Store** — SQLite at `$DATA_DIR/benchmark.db` (§9), WAL mode, plus `$DATA_DIR/exports/`
  for generated report files and `$DATA_DIR/packs/` for uploaded prompt packs.

Backend: Python 3.12+, FastAPI, httpx, tiktoken, numpy, psutil. Frontend: Vite + React +
TypeScript + ECharts (see UI spec). No Redis, no Celery, no external services.

---

## 4. Services (targets under test)

A **service** is a saved endpoint definition:

| Field | Notes |
|---|---|
| `name` | display name, unique |
| `adapter` | `openai` \| `asksage` |
| `base_url` | e.g. `https://vllm.internal:8000/v1` or `https://asksage.internal/server` |
| `api_key` | stored encrypted (§14); write-only via API |
| `default_model` | string |
| `verify_tls` | bool, default true (self-signed certs are common on-prem) |
| `extra_headers` | optional JSON map merged into every request |

**Test connection** (`POST /api/services/{id}/test`) performs, server-side: an auth check,
a model list fetch where supported, a 1-token completion probe, and a streaming probe. It
returns `{reachable, auth_ok, models[], supports_streaming, latency_ms, error?}` and stores
`supports_streaming` on the service record.

---

## 5. Adapters

All adapters implement one interface:

```python
class InferenceAdapter(Protocol):
    capabilities: AdapterCapabilities  # streaming, vision, usage_reporting, model_listing

    async def probe(self) -> ProbeResult
    async def list_models(self) -> list[str]
    def build_request(self, prompt: PromptInstance, params: SamplingParams) -> PreparedRequest
    async def execute(self, prepared: PreparedRequest, timing: TimingRecorder) -> RequestResult
```

`RequestResult` carries: output text, prompt/output token counts (+ estimated flag), all
timestamps from §2, and an error classification on failure. Wire-format knowledge lives
**only** inside adapter modules so deployment variants are a one-file fix.

### 5.1 OpenAI adapter (`bench/adapters/openai.py`)

- Endpoint: `POST {base_url}/chat/completions` (base_url includes `/v1`).
- Auth: `Authorization: Bearer {api_key}`.
- Streaming mode (default when the service supports it): `"stream": true`,
  `"stream_options": {"include_usage": true}`. Parse SSE lines; a chunk is a *content
  chunk* when `choices[0].delta.content` is a non-empty string. Terminate on `data: [DONE]`.
  The final usage-bearing chunk supplies exact token counts. Tolerate servers that omit
  `stream_options` support (fall back to client-side counting).
- Non-streaming fallback: plain request; §2 non-streaming metrics apply.
- Vision: content-parts format, `{"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}`.
- Model listing: `GET {base_url}/models`.
- Pass-through sampling params: `temperature`, `max_tokens`, `top_p`, `seed`.

### 5.2 AskSage adapter (`bench/adapters/asksage.py`)

- Endpoint: `POST {base_url}/query` where `base_url` is the deployment's server-API root.
- Auth: `x-access-tokens: {api_key}` header.
- Request body: `{"message": <prompt text>, "model": <model>, "temperature": <t>,
  "dataset": "none", "live": 0}`. `extra_headers`/service config may extend this.
- Response: JSON; the output text is the `message` field. Treat HTTP 200 with a `status`
  field ≠ 200 in the body as an error of class `http` (§13).
- Non-streaming: TTFT, ITL, generation TPS are **N/A**; estimated TPS and E2E latency per
  §2. Token counts are client-estimated unless the response carries usage fields (if
  present, prefer `teach_tokens`/usage-like fields; centralize this mapping in one
  function with a unit test so it is trivially adjustable per deployment).
- Model listing: `POST {base_url}/get-models` if available; on failure return an empty
  list and let the user type the model name.
- Vision: if the deployment's query endpoint accepts a file/image field, send base64
  content; otherwise return `unsupported_modality` (§13). Gate on a per-service capability
  flag set by the probe.

---

## 6. Prompt dataset

### 6.1 Structure

**4 focus areas × 4 difficulty tiers × 16 prompts = 256 text prompts**, plus a 16-prompt
vision supplement. Bundled in the image under `bench/data/dataset/` as JSON plus an
`images/` directory. The dataset carries a `dataset_version` string recorded on every run;
the Compare view refuses to hide a dataset-version mismatch (it shows a warning banner).

| Focus | Shape | Stresses |
|---|---|---|
| `text-analysis` | large prose chunk → short answer (summarize, extract, answer-from-doc) | prefill on prose |
| `code-analysis` | large code chunk → short answer (explain, find-bug, review) | prefill on code |
| `generation` | ~50–100 token instruction → long output | decode speed |
| `conversational` | medium context → medium answer, chat-style | mixed baseline |

| Tier | text/code-analysis prompt tokens | generation output target | conversational in / out |
|---|---|---|---|
| 1 | ~512 | ~256 | ~256 / ~256 |
| 2 | ~2,048 | ~512 | ~512 / ~384 |
| 3 | ~8,192 | ~1,024 | ~1,024 / ~512 |
| 4 | ~16,384 | ~2,048 | ~2,048 / ~768 |

Rules:

- Analysis prompts set `max_tokens` 128–256 so decode time doesn't pollute prefill
  measurement. Token budgets are ±10% targets, exact counts stored per prompt.
- Generation prompts are engineered to fill their `max_tokens` budget: explicit length
  instructions ("write an exhaustive, detailed…", "continue until you reach N items").
- Tier-4 analysis doubles as a context-window stress test; a rejected request due to
  context length is classed `context_exceeded`, excluded from latency aggregates, and
  reported separately.
- `code-analysis` sources span 4 languages: Python, TypeScript, C, Go (4 prompts per
  language per tier).

### 6.2 Prompt schema

```json
{
  "id": "text-analysis-t3-07",
  "focus": "text-analysis",
  "tier": 3,
  "language": null,
  "messages": [{"role": "user", "content": "..."}],
  "image": null,
  "expected_prompt_tokens": 8112,
  "max_tokens": 256,
  "dataset_version": "1.0.0"
}
```

Vision prompts set `"image": "images/vision-04.png"` and use content-parts when the
adapter supports vision.

### 6.3 Dataset build

A repo script, `tools/build_dataset.py`, generates the dataset deterministically:

- **Prose corpus**: public-domain texts committed under `tools/corpus/text/` (e.g.
  Project Gutenberg excerpts). The script slices and concatenates passages to hit each
  tier's token budget, then appends a task question from a template pool.
- **Code corpus**: permissively-licensed (MIT/BSD/Apache) source files committed under
  `tools/corpus/code/{python,typescript,c,go}/` with license headers preserved.
- **Vision images**: generated by the script with Pillow — rendered text paragraphs (OCR
  tasks), synthetic charts and diagrams (description tasks) at varied resolutions from
  512×512 to 2048×1536. No third-party images, no licensing risk.
- The script measures every prompt with tiktoken, writes `expected_prompt_tokens`, and
  emits `dataset.json` + `images/`. The generated output **is committed**; the container
  build does not regenerate it.

### 6.4 Custom prompt packs

Users may upload a JSON file matching the schema in §6.2 (validated server-side; images
may be embedded base64 or omitted). Packs are stored in `$DATA_DIR/packs/`, get their own
`pack_id` and version, and are selectable in the New Run form anywhere the bundled dataset
is. Runs record which pack they used. Pack size limit: 20 MB.

### 6.5 Selection, weighting, reproducibility

A run's **workload** is a weight map over cells, chosen in the UI as one of:

- a single cell (e.g. `code-analysis` tier 3) — the purest measurement;
- one focus across mixed tiers;
- a named profile — a saved weight map. Built-ins: `prefill-stress` (text+code analysis,
  tiers 3–4), `decode-stress` (generation, tiers 2–4), `mixed-chat` (conversational all
  tiers + 25% short generation), `code-review` (code-analysis all tiers).

The runner filters the dataset by the weight map, builds a weighted, seeded shuffle
(`seed` recorded on the run; default random, settable for exact repeats), and cycles
through it, reshuffling per cycle with `seed+cycle`. **Cache-buster** option (default on):
prepend `"[req {uuid4}] "` to every prompt so prefix-caching servers can't reuse prefill
across requests; the added tokens are counted in `expected_prompt_tokens` adjustments.
When off, the run row records that prefix caching may inflate prefill numbers and reports
show a caveat badge.

---

## 7. Test configuration

Fields of a run config (all persisted; saved presets are named copies):

| Field | Type / default | Notes |
|---|---|---|
| `service_id`, `model` | required | |
| `mode` | `fixed` \| `rate` \| `sweep` | §8 |
| `target_concurrency` | int 1–512 (mode `fixed`/`sweep`) | |
| `arrival_rate` | float req/s (mode `rate`) | |
| `max_in_flight` | int, default 1000 | open-loop safety cap; hitting it flags the run `overloaded` |
| `ramp_seconds` | int, default 30 | linear ramp 1 → target |
| `warmup_requests` | int, default 4 | sequential, discarded from all aggregates |
| `max_requests` | int, optional | stop condition (steady-state requests) |
| `max_duration_seconds` | int, default 300 | stop condition; first one hit wins; at least one must be set |
| `sweep_steps` | int list, default [1,2,4,8,16,32,64,…,target] | mode `sweep` |
| `step_min_seconds` | int, default 60 | sweep dwell per step |
| `step_min_requests` | int, default 32 | sweep: dwell extends until both minimums met |
| `step_cooldown_seconds` | int, default 10 | drain + idle between steps |
| `workload` | weight map (§6.5) | plus `pack_id` (default bundled dataset) |
| `include_vision` | bool, default false | |
| `cache_buster` | bool, default true | |
| `request_timeout_seconds` | int, default 180 | |
| `max_retries` | int, default 0 | failures are data, not noise |
| `temperature` | float, default 0.0 | |
| `max_tokens_override` | int, optional | overrides per-prompt values |
| `think_time_ms` | int, default 0 | per-worker delay between requests (closed-loop) |
| `slo_ttft_ms`, `slo_e2e_ms` | optional | used by forecasting (§11) |
| `label`, `notes` | strings | free text; what makes history useful |

---

## 8. Test execution

### 8.1 Run lifecycle

`pending → warmup → ramping → running → draining → finalizing → completed`
Terminal alternatives: `stopped` (manual, partial data kept), `failed` (runner error),
`interrupted` (found `running` at app startup after a crash/restart).

1. Web layer validates config, snapshots it into a new run row, spawns the runner.
2. **Warm-up**: `warmup_requests` sequential requests (tier-1 conversational prompts);
   recorded with `phase='warmup'`, excluded from every aggregate. Ensures the model is
   loaded and connections are established.
3. **Ramp**: concurrency rises linearly from 1 to target over `ramp_seconds`
   (`phase='ramp'`).
4. **Steady state** (`phase='steady'`): per mode, §8.2–8.4. Only steady-state rows feed
   default aggregates and forecasting.
5. **Drain**: on any stop condition, no new requests are issued; in-flight requests are
   given until `request_timeout_seconds` to finish (`phase='drain'`, kept but excluded
   from default aggregates).
6. **Finalize**: runner computes run-level aggregates, writes them, exits 0. Web layer
   marks the run `completed` and notifies WebSocket subscribers.

Every request row records its `phase`. The report UI can optionally include non-steady
phases, clearly labeled.

### 8.2 Fixed-concurrency mode (closed loop)

N workers; each worker draws the next prompt from the shared iterator, sends, awaits
completion, applies `think_time_ms`, repeats. Measures the service's behavior *at* a
given concurrency.

### 8.3 Arrival-rate mode (open loop)

A scheduler fires request starts at `arrival_rate`/sec — constant intervals by default,
optional Poisson jitter (`poisson: true`) — regardless of completions. Reveals queue
collapse that closed-loop testing hides. If in-flight count would exceed `max_in_flight`,
the request is recorded as `shed` (not sent), and the run is flagged `overloaded`.

### 8.4 Sweep mode

Runs §8.2 repeatedly across `sweep_steps`: warm-up once, then for each step — ramp over
`min(ramp_seconds, 10)`s, dwell until `step_min_seconds` **and** `step_min_requests` are
both met, drain, cool down `step_cooldown_seconds`, next step. Request rows record
`sweep_step`. Produces per-step aggregates and the throughput-vs-concurrency curve that
feeds forecasting (§11). A manual stop finalizes with completed steps intact.

### 8.5 Batch runs

A **batch** applies one run config to an ordered list of `(service_id, model)` targets,
executed sequentially (never in parallel — R2) with `step_cooldown_seconds` idle between
runs. Each target produces a normal run row linked by `batch_id`; when the batch
finishes, the UI lands on a pre-built comparison of its runs. One active batch counts as
the one active run.

### 8.6 Live progress

Every second the runner emits a progress event (bucket aggregates + counters) to the web
layer, which persists the bucket and broadcasts it to WebSocket subscribers (message
shapes in the UI spec). Failures stream as discrete events with error class and message.

### 8.7 Client-saturation self-check

Once per second the runner samples: event-loop lag (schedule a callback 100 ms out,
measure overshoot), process CPU % (psutil), and open-socket count. If loop lag > 100 ms
or CPU > 90% sustained for 5 consecutive samples, the run is flagged
`client_saturated_from={timestamp}` and the UI shows a warning banner on live view and
reports: measurements after that point may reflect the client, not the service.

---

## 9. Data model

SQLite, WAL mode, schema versioned with simple integer `PRAGMA user_version` migrations
applied at startup.

- **services** — id, name, adapter, base_url, api_key_encrypted, default_model,
  verify_tls, extra_headers_json, supports_streaming, supports_vision, created_at.
- **prompt_packs** — id, name, version, source (`bundled`|`uploaded`), path, prompt_count,
  created_at.
- **presets** — id, name, config_json, created_at.
- **batches** — id, label, config_json, targets_json, status, created_at.
- **runs** — id, batch_id?, service_id, model, config_json (frozen snapshot), seed,
  dataset_version, pack_id, status, phase_started_at timestamps, flags_json
  (`client_saturated`, `overloaded`, `cache_buster_off`, …), summary_json (run-level
  aggregates, written at finalize), started_at, finished_at, label, notes.
- **requests** — id, run_id, sweep_step?, phase, prompt_id, focus, tier, worker_id,
  t_send_wall, ttft_ms?, e2e_ms, gen_tps?, tpot_ms?, itl_mean_ms?, itl_p99_ms?,
  prompt_tokens, output_tokens, tokens_estimated, error_class?, error_detail?,
  http_status?. Indexed on (run_id, phase) and (run_id, sweep_step).
- **timeseries** — run_id, bucket_ts (1 s), sweep_step?, in_flight, started, completed,
  failed, output_tokens, prompt_tokens, ttft_p50_ms, ttft_p99_ms, e2e_p50_ms, e2e_p99_ms.
  Primary key (run_id, bucket_ts).

Run deletion cascades to requests and timeseries. An `archive` flag hides runs from the
default History list without deleting data.

---

## 10. HTTP API

All under `/api`, JSON. Errors: `{"error": {"code": str, "message": str}}` with proper
status codes. No auth by default; optional HTTP Basic via env (§14) covers everything
including the SPA and WebSocket.

| Method & path | Purpose |
|---|---|
| `GET/POST /api/services`, `GET/PUT/DELETE /api/services/{id}` | CRUD; api_key accepted on write, never returned (a boolean `has_api_key` is) |
| `POST /api/services/{id}/test` | connection probe (§4) |
| `GET /api/prompt-packs`, `POST /api/prompt-packs` (multipart), `DELETE …/{id}` | §6.4; bundled pack undeletable |
| `GET /api/prompts/summary?pack_id=` | cell counts + token stats for the workload picker |
| `GET/POST /api/presets`, `DELETE /api/presets/{id}` | saved configs |
| `POST /api/runs` | start a run; 409 if one is active |
| `GET /api/runs?service&model&status&label&archived&page` | history list |
| `GET /api/runs/{id}` | full run detail: config, flags, summary aggregates |
| `GET /api/runs/{id}/timeseries?step=` | bucket rows |
| `GET /api/runs/{id}/requests?phase&errors_only&page` | per-request rows |
| `POST /api/runs/{id}/stop` | graceful stop |
| `DELETE /api/runs/{id}`, `POST /api/runs/{id}/archive` | |
| `GET /api/runs/{id}/export.{json,csv,html}` | §12 |
| `POST /api/batches`, `GET /api/batches/{id}` | §8.5 |
| `GET /api/compare?runs=1,2,3` | aligned aggregates + per-step curves for 2–4 runs |
| `WS /ws/runs/{id}` | live events; also replays recent buckets on connect |
| `GET /healthz` | `{status, active_run_id?, db_ok}` — no auth |

---

## 11. Forecasting

Computed from **sweep runs** (per-step steady-state aggregates), shown in sweep reports
and Compare:

1. **Saturation curve**: measured system throughput (output tokens/s, and separately
   prompt tokens/s) vs concurrency step.
2. **Knee detection**: the smallest step where marginal throughput gain from the previous
   step falls below 10% per doubling of concurrency. Reported as "saturation begins at
   ~N concurrent".
3. **Max sustainable throughput**: mean of steady-state throughput across all steps at or
   beyond the knee (the plateau), with standard deviation.
4. **SLO operating point**: if `slo_ttft_ms`/`slo_e2e_ms` are set, linearly interpolate
   between adjacent steps to find the highest concurrency where p99 stays under the SLO;
   report that concurrency, its throughput, and derived **requests/hour capacity**
   (`throughput ÷ mean output tokens per request × 3600`).
5. **Honesty rules**: all figures are interpolation between measured points — never
   extrapolate beyond the largest tested concurrency; annotate each number with the two
   bracketing measured steps; suppress forecasting entirely (with an explanatory note) if
   the sweep has < 3 completed steps or the run is flagged `client_saturated`.

Non-sweep runs get no forecast section — the report links to "run a sweep to enable
forecasting".

---

## 12. Reports and exports

- **JSON export**: the full run — config, flags, summary, per-request rows, buckets.
- **CSV export**: per-request rows, one line each, headers matching §9 columns.
- **HTML report**: a single self-contained file (inlined ECharts bundle, inlined data, no
  network access needed) rendering the same content as the Run Report view: summary
  header, verdict badges, metric tables, all charts, per-cell breakdown, forecast section
  for sweeps. Generated server-side from a template; written to `$DATA_DIR/exports/` and
  streamed to the browser.
- Batch/Compare views export the comparison as HTML the same way.

---

## 13. Error taxonomy

Request `error_class` values: `timeout`, `connect` (DNS/TCP/TLS), `http` (non-2xx or
AskSage body-status error; `http_status` recorded), `malformed_stream` (SSE parse
failure), `context_exceeded` (detected from provider error message/code),
`unsupported_modality` (vision prompt to non-vision target), `shed` (§8.3), `cancelled`
(drain timeout at stop).

Handling rules:

- No silent retries; `max_retries > 0` retries only `connect` errors, and each attempt is
  recorded.
- `context_exceeded`, `unsupported_modality`, and `shed` are excluded from
  latency/throughput aggregates and error-*rate* alike (`shed` requests were never sent);
  they're reported in their own "excluded requests" table. `shed` volume additionally
  drives the `overloaded` flag (§8.3).
- Runner crash → web layer notices process exit ≠ 0 (or heartbeat loss > 10 s), marks the
  run `failed`, preserves partial data.
- App restart → any run in a non-terminal status is marked `interrupted` at startup.
- A run whose steady-state error rate exceeds 50% is stamped with a `degraded` verdict
  badge; aggregates still compute over successful requests.

---

## 14. Security

- API keys encrypted at rest (Fernet). Key material: `SECRET_KEY` env var if set,
  otherwise generated once into `$DATA_DIR/.secret` (mode 0600). Changing the secret
  invalidates stored keys; the UI then prompts for re-entry per service.
- Keys are write-only through the API (R1). Exports and logs never contain keys.
- Optional HTTP Basic auth: set `BASIC_AUTH_USER` + `BASIC_AUTH_PASS`; guards everything
  except `/healthz`.
- Per-service `verify_tls=false` only disables verification for that service's outbound
  calls, never globally. Uploaded prompt packs are schema-validated and size-capped; image
  fields are re-encoded via Pillow to strip anything non-image.
- Container runs as a non-root user; only `$DATA_DIR` is writable.

---

## 15. Testing strategy

Codex must implement tests alongside features, not after:

1. **Mock inference server** (`tools/mockserver/`) — a small FastAPI app speaking both
   adapter dialects with configurable TTFT, token rate, output length, jitter, error rate,
   and streaming on/off — settable per request via prompt directives (e.g. a prompt
   containing `@@ttft=250;tps=40@@`) and per server via CLI flags. It is both the CI
   fixture and a **self-validation tool**: point a real benchmark run at the mock with
   known parameters and confirm measured TTFT/TPS match within tolerance (±10%).
2. **Unit tests**: metric math (percentiles, bucket aggregation, TPOT/ITL) against
   hand-computed fixtures; knee detection and SLO interpolation against synthetic curves;
   seeded shuffle reproducibility; config validation; encryption round-trip; AskSage and
   OpenAI response parsing against recorded wire-format fixtures (including usage-chunk,
   no-usage, and malformed-stream cases).
3. **Integration**: adapter round-trips against the mock server; runner lifecycle
   (start → stop → drain → finalize); crash recovery (`kill -9` the runner mid-run,
   assert `failed` + partial data).
4. **End-to-end (CI)**: build the container, start it with the mock server, drive a
   10-second fixed-concurrency run and a 3-step sweep via the REST API, assert metrics
   within tolerance and the HTML export renders (contains expected chart data blobs).
5. **Frontend**: component tests for the workload picker and config form validation;
   one Playwright smoke test: create service → start run against mock → watch live view
   update → open report.

---

## 16. Container and deployment

- **Dockerfile**: multi-stage — stage 1 `node:22-slim` builds the SPA (`npm ci && npm run
  build`); stage 2 `python:3.12-slim` installs the backend, copies the SPA build and the
  bundled dataset, creates a non-root user. Target image < 400 MB.
- **Env vars**: `PORT` (default 8080), `DATA_DIR` (default `/data`), `SECRET_KEY`
  (optional), `BASIC_AUTH_USER`/`BASIC_AUTH_PASS` (optional), `LOG_LEVEL` (default
  `info`, JSON logs to stdout).
- **Run**: `docker run -p 8080:8080 -v bench-data:/data inference-benchmark`.
- Graceful shutdown: SIGTERM stops any active run via the normal drain path before exit
  (Docker's default 10 s grace period is honored; the drain is capped accordingly and
  interrupted requests are marked `cancelled`).
- `docker-compose.yml` provided for convenience (app + optional mock server profile), but
  never required.

### Repository layout

```
bench/                  # Python package
  api/                  # FastAPI routers, websocket, static serving
  engine/               # load generation, scheduling, phases, metrics
  adapters/             # openai.py, asksage.py, base.py
  storage/              # sqlite access, migrations, models
  reports/              # aggregates, forecasting, HTML export
  data/dataset/         # committed generated dataset (§6.3)
frontend/               # Vite + React + TS SPA (see UI spec)
tools/
  build_dataset.py
  corpus/
  mockserver/
tests/
Dockerfile
docker-compose.yml
```

### Suggested implementation order for Codex

1. Storage + schema + config models; 2. adapters + mock server (test-first);
3. engine: fixed-concurrency lifecycle end-to-end headless; 4. REST API + WebSocket;
5. frontend shell, Services, New Run, Live Run; 6. reports, aggregates, exports;
7. sweep + rate modes + forecasting; 8. batches + Compare; 9. vision + custom packs;
10. container hardening + e2e.

---

## 17. Future work (explicitly out of scope for v1)

- Scheduled/recurring runs with trend tracking.
- Distributed multi-worker load generation.
- Run queueing (multiple pending runs).
- Output-quality scoring.
- Additional adapters (Anthropic, Gemini, Triton-native, etc.) — the adapter interface
  (§5) is the extension point.

---

## 18. Acceptance criteria

1. `docker run` with a volume yields a working UI on the mapped port; history survives
   container recreation.
2. Against the mock server configured at TTFT 250 ms / 40 TPS, a 64-concurrency run
   reports steady-state p50 TTFT and mean generation TPS within ±10%.
3. A 512-concurrency run against the mock completes without the `client_saturated` flag
   on reference hardware (4 CPU cores).
4. A sweep run produces per-step curves and a forecast section with knee, plateau, and
   (when SLO set) SLO operating point.
5. An AskSage-adapter run completes with TTFT/ITL displayed as N/A and estimated TPS
   labeled as estimated.
6. A batch across two services lands on a comparison report with deltas.
7. Stopping a run mid-flight preserves partial data marked `stopped`; killing the runner
   process yields `failed` with partial data; restarting the container yields
   `interrupted`.
8. The HTML export opens from disk with no network access and renders all charts.
9. All CI suites in §15 pass in the container build pipeline.
