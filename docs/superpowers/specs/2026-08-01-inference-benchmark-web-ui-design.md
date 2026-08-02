# Inference Benchmark — Web Interface Specification

**Version:** 1.0 · **Date:** 2026-08-01 · Companion to
`2026-08-01-inference-benchmark-design.md` (the "main spec"). Section references (§) point
into the main spec unless prefixed "UI".

The interface is a single-page application: Vite + React + TypeScript, ECharts for all
charts, TanStack Query for REST state, native WebSocket for live runs, React Router for
navigation. It is built at container build time and served as static files by the backend.
The browser talks **only** to the app's own API (main spec R1).

---

## UI-1. Design language

The goal is a focused instrument panel, not a generic admin template. Codex must apply
these rules rather than shipping component-library defaults:

- **Theme**: dark by default with a light theme toggle (persisted in `localStorage`,
  respects `prefers-color-scheme` on first visit). Dark background: `#0e1116` (near-black
  with a slight cool cast), surfaces one step lighter `#161b22`, 1 px borders `#262d38`
  instead of drop shadows.
- **Accent**: one accent color for primary actions and the "current run" identity:
  `#4f9cf9`. Semantic colors: success `#3fb27f`, warning `#d9a13d`, danger `#e5534b`.
  Never use the accent for chart series (see UI-8 palette).
- **Typography**: a geometric sans for UI text (system stack is fine:
  `Inter, system-ui, sans-serif`); **tabular-numeral monospace** (`"JetBrains Mono",
  ui-monospace, monospace`) for every metric value, table number, and axis label so
  columns of numbers align. Base size 14 px; stat-tile values 28–32 px semibold; labels
  11 px uppercase, letter-spaced, muted.
- **Density**: this is a data tool — compact paddings, information-dense tables, no hero
  sections, no decorative illustration. Empty states are one line of text plus the single
  action that fixes them.
- **Units always visible**: every number carries its unit (`ms`, `tok/s`, `req/s`) in a
  muted smaller suffix. Durations < 10 s show as ms; ≥ 10 s as `12.4 s`. Token counts
  ≥ 10 000 show thousands separators.
- **Badges** (used everywhere flags appear): `estimated` (tokens counted client-side),
  `non-streaming`, `client-saturated`, `overloaded`, `cache-buster off`, `degraded`,
  `interrupted`, `stopped early`, `sweep`, `batch`. One shared Badge component, colored by
  severity (info/warning/danger), always with a tooltip explaining the flag in one
  sentence (text given in UI-9).

---

## UI-2. Application shell and navigation

- Left sidebar (collapsible to icons at < 1100 px): **Dashboard**, **New Run**,
  **History**, **Compare**, **Services**, **Prompt Packs**. Bottom of sidebar: theme
  toggle, app version, dataset version.
- Persistent **active-run bar**: whenever a run or batch is active, a slim bar is pinned
  to the top of every page showing status dot (pulsing), label, elapsed time, current
  system TPS, and a "View live →" link. Clicking navigates to the Live Run view. This is
  how a user who navigated away finds their way back.
- Routes: `/` (dashboard), `/runs/new`, `/runs/:id` (live or report by status),
  `/history`, `/compare?runs=`, `/services`, `/packs`, `/batches/:id`.
- All list tables share behavior: sortable columns, filter row, pagination (50/page),
  row click navigates, actions kebab on row hover.

---

## UI-3. Dashboard (`/`)

Purpose: orientation and fast resume.

- If a run is active: a large live summary card (mirrors the Live Run stat tiles,
  clickable through).
- **Recent runs** table (last 10): status dot, label, service → model, mode badge,
  headline result (steady-state system TPS + p99 TTFT), started time, duration.
- **Services strip**: one compact card per service — name, adapter type, streaming/vision
  capability icons, last-probe status dot, "last benchmarked N days ago".
- Empty state (fresh install): a three-step checklist — "1 Add a service → 2 Test
  connection → 3 Start your first run" — each step a link, steps checked off as completed.

---

## UI-4. Services (`/services`) and Prompt Packs (`/packs`)

**Services** — table of service definitions (§4): name, adapter, base URL, default model,
capability badges (`streaming`, `vision`), TLS-verify indicator, last probe result +
timestamp. Row actions: Edit, Test connection, Delete (confirm dialog; blocked with
explanation if the service has runs — offer archive-style soft delete instead).

Add/Edit is a side drawer, not a separate page:

- Fields per §4. API key input is password-type with "replace key" affordance when one
  exists (`has_api_key` true) — the current key is never shown.
- **Test connection** button inside the drawer: runs the §4 probe, renders the result
  inline — reachability, auth, latency, detected models (fills the model dropdown),
  streaming support. Failures show the error class and a remediation hint ("TLS verify
  failed — toggle 'Verify TLS' off if this service uses a self-signed certificate").
- AskSage services show a permanent info note: "Non-streaming API: TTFT and inter-token
  metrics will be reported as N/A (main spec §5.2)."

**Prompt Packs** — table: name, version, source (bundled/uploaded), prompt count, cell
coverage mini-grid (4×4 dots, filled where the pack has prompts). Upload button →
validation errors listed per prompt with line numbers. Bundled pack row is undeletable
and marked "built-in". Row click opens a read-only browser: cell grid → prompt list →
prompt preview with token counts.

---

## UI-5. New Run (`/runs/new`)

A single form page, two columns: configuration left (~60%), a live **summary panel**
right (~40%) that restates the run in plain English and updates as fields change — e.g.
"Fixed concurrency 64 against *vllm-prod / llama-3.3-70b*, prefill-stress workload,
ramp 30 s, run 5 min or 2 000 requests, timeout 180 s." The summary panel is also where
validation errors surface.

Form sections, in order:

1. **Target** — service select (with capability badges), model select (from probe result,
   free-text fallback). If a batch is intended: "+ Add another target" turns the run into
   a batch (§8.5), listing targets as removable chips, reorderable.
2. **Mode** — segmented control: Fixed concurrency / Arrival rate / Sweep. The visible
   fields below adapt:
   - Fixed: target concurrency (slider 1–512, log-scaled detents at powers of 2, plus
     numeric input).
   - Rate: requests/sec numeric, Poisson-jitter toggle, max-in-flight numeric.
   - Sweep: step list editor (default powers of 2 up to a chosen max; chips, add/remove),
     step dwell (min seconds, min requests), cooldown.
3. **Workload** — the picker for §6.5. A 4×4 grid (focus rows × tier columns); cells show
   prompt counts and are click-to-toggle; drag across cells to multi-select; per-focus and
   per-tier header toggles. Above the grid: profile chips (`prefill-stress`,
   `decode-stress`, `mixed-chat`, `code-review`, plus user-saved profiles) that set the
   grid; editing the grid switches the chip to "custom", with "save as profile…".
   Weight editing: selected cells get equal weight by default; an "advanced weights"
   disclosure reveals per-cell numeric weights. Also here: prompt pack select (default
   "Built-in dataset v1.0.0"), vision toggle (disabled with tooltip if the target lacks
   vision capability), cache-buster toggle (on; turning it off shows the prefix-caching
   caveat inline).
4. **Duration & shape** — ramp seconds, warm-up requests, max duration, max requests
   (at least one of the two stop conditions required), think time.
5. **Request settings** (collapsed disclosure) — timeout, retries, temperature,
   max-tokens override, SLO targets (TTFT ms / E2E ms) with note "used for forecasting".
6. **Label & notes** — label input (default: auto `"{service} {mode} {date}"`), notes
   textarea, "save as preset" checkbox + name.

Footer: **Start benchmark** (primary; disabled with reasons listed while invalid; 409
from the API surfaces "another run is active — view it"), Load preset select, Reset.

---

## UI-6. Live Run (`/runs/:id` while active)

Layout, top to bottom:

1. **Header**: label, service → model, mode badge, phase indicator (warm-up → ramp →
   steady → drain shown as a segmented progress strip with the current phase pulsing),
   elapsed / remaining estimate, and a danger-styled **Stop** button (confirm popover:
   "In-flight requests will finish and partial results are kept.").
2. **Stat tiles** (single row of 6): system TPS (output tok/s), prompt tok/s, requests
   in flight, req/s completed, p50 / p99 TTFT (one tile, both values), errors (count +
   rate, turns danger-colored when > 0). Tiles update once per second from WebSocket
   buckets; values use tabular numerals so they don't jitter horizontally. Non-streaming
   targets: TTFT tile shows "N/A · non-streaming".
3. **Charts** (two stacked, full width, sharing a synced time axis; 1 s buckets,
   window = full run so far):
   - *Throughput*: output tok/s line + prompt tok/s line; ramp and warm-up periods shaded
     with a muted band and labeled.
   - *Latency*: TTFT p50/p99 and E2E p50/p99 lines (solid p50, dashed p99); log-scale
     toggle. Sweep runs: vertical step-boundary markers with the step's concurrency
     labeled.
4. **Failures log**: reverse-chronological table (time, error class chip, HTTP status,
   prompt id, message truncated with expand). Hidden behind a zero-state ("No failures")
   until the first failure.
5. **Warning banners** (as conditions occur): `client_saturated` (danger, per §8.7 with
   the timestamp), `overloaded` (rate mode shed events), runner heartbeat lost.

WebSocket protocol (`/ws/runs/:id`): on connect the server replays the last 300 buckets,
then pushes messages:

```json
{"type": "bucket",  "data": {"ts": 1722550000, "in_flight": 64, "started": 12, "completed": 11, "failed": 0, "out_tps": 1240.5, "prompt_tps": 8900.2, "ttft_p50": 210, "ttft_p99": 480, "e2e_p50": 3400, "e2e_p99": 7100, "sweep_step": 32}}
{"type": "phase",   "data": {"phase": "steady", "sweep_step": 32}}
{"type": "failure", "data": {"ts": ..., "error_class": "timeout", "http_status": null, "prompt_id": "generation-t3-02", "detail": "..."}}
{"type": "flag",    "data": {"flag": "client_saturated", "ts": ...}}
{"type": "status",  "data": {"status": "completed", "run_id": 41}}
```

On `status: completed|stopped|failed`, the view swaps to the Run Report without a manual
refresh. If the WebSocket drops, show a reconnecting notice and poll `GET /api/runs/:id`
every 5 s as fallback.

---

## UI-7. Run Report (`/runs/:id` when finished)

The permanent record. Sections top to bottom; all charts default to **steady-state data
only**, with a global "include ramp/drain" toggle that re-renders everything and stamps
non-steady data with hatched shading.

1. **Summary header**: label (editable inline), badges (all applicable from UI-1), service
   → model, mode, dataset/pack version, seed, started/finished/duration, notes (editable),
   actions: Export HTML / CSV / JSON, Re-run (clones config into New Run form), Compare
   (adds to compare tray), Archive, Delete.
2. **Headline tiles**: steady-state system TPS, prompt tok/s, p50/p99 TTFT, p50/p99 E2E,
   req/s, error rate, total requests (steady/total).
3. **Metrics table**: rows = TTFT, E2E, generation TPS, TPOT, ITL, prompt-processing
   rate; columns = mean, p50, p90, p95, p99, min, max. Prompt-processing rate row carries
   an info icon: "lower bound — includes queueing and network (main spec §2)".
   Non-streaming runs render N/A rows with the non-streaming badge.
4. **Charts** (each in a card with title, unit, and a small "what this tells you" caption;
   axis titles mandatory):
   - Throughput over time (as Live view).
   - Latency percentiles over time (as Live view).
   - **TTFT histogram** and **E2E histogram** (40 buckets, count y-axis).
   - **Latency vs prompt tokens scatter**: x = prompt tokens, y = E2E ms, point color by
     focus (UI-8 palette), point shape by error (× for failures); makes prefill cost
     visible. Caption: "slope ≈ per-token prefill cost".
   - **Concurrency over time** (in-flight line vs target line).
   - **Error timeline** (stacked 1 s bars by error class), only if errors > 0.
5. **Per-cell breakdown table**: one row per focus×tier cell that had traffic — requests,
   mean prompt/output tokens, system-TPS share, p50/p99 TTFT, p50/p99 E2E, gen TPS,
   error count. Sortable. This is where "8K code analysis vs raw generation" is answered
   numerically.
6. **Sweep runs only — step results**: per-step table (concurrency, requests, out tok/s,
   prompt tok/s, p50/p99 TTFT, p50/p99 E2E, errors) and the **saturation chart**:
   x = concurrency (log2 axis), left y = output tok/s (line + measured points with error
   bars = ±1 σ across the step's buckets), right y = p99 TTFT ms (line, dashed). Knee
   annotated with a vertical marker + label. If SLO set: horizontal SLO line on the right
   axis and a marker at the interpolated SLO operating point.
7. **Sweep runs only — Forecast panel**: plain-language sentences with the numbers bold —
   "Saturation begins near **64** concurrent requests." / "Max sustainable throughput:
   **2 340 ± 90 tok/s**." / "At the p99 TTFT SLO of **2 000 ms**: ~**48** concurrent,
   **1 980 tok/s**, ≈ **41 000 requests/hour** (mean 174 output tok/req)." Each line
   footnoted with its bracketing measured steps (§11.5). Suppressed states render the
   §11.5 explanatory note instead.
8. **Excluded requests** table (`context_exceeded`, `unsupported_modality`, `shed`),
   collapsed by default (§13).

Batch report (`/batches/:id`): batch header (targets, status per run) + an embedded
Compare view (UI-8) of its runs, plus links to each individual run report.

---

## UI-8. Compare (`/compare?runs=a,b,c`)

Select 2–4 runs (from History checkboxes or the report "Compare" action, held in a small
floating tray). Layout:

1. **Config diff strip**: one column per run (color-keyed), rows for service, model, mode,
   concurrency, workload, dataset version, key flags. Cells that differ across runs are
   highlighted; a dataset-version mismatch shows the §6.1 warning banner.
2. **Delta table**: headline metrics as rows, one column per run; the first-selected run
   is baseline, other cells show value plus signed % delta, colored green when the
   direction is an improvement for that metric (higher TPS = green, higher latency = red)
   — direction-awareness per metric, not per sign.
3. **Overlaid charts**: latency-percentile-over-normalized-time (x = % of steady-state
   elapsed, so different durations align), TTFT histograms (outline style, translucent
   fills), and — when 2+ selected runs are sweeps — overlaid saturation curves, one color
   per run, knees marked. Runs are color-keyed consistently across every chart and table
   column by selection order.
4. Export comparison as self-contained HTML.

**Chart series palette** (UI-wide): series colors distinct from UI accent and from
semantic status colors; a fixed 6-color categorical ramp with adequate contrast on both
themes — `#5ba3f5, #f5a35b, #67c28f, #c77ddb, #e0c341, #d96f6f` — used in order for
run-comparison keys; focus-area colors fixed globally (text-analysis `#5ba3f5`,
code-analysis `#c77ddb`, generation `#f5a35b`, conversational `#67c28f`, vision
`#e0c341`) so a focus is the same color in every chart in the app. p50 solid / p99 dashed
everywhere. Tooltips always show exact values with units; crosshair synced across stacked
time charts.

---

## UI-9. History (`/history`), states, and copy

**History**: full-width table — status dot, label, badges, service → model, mode,
concurrency/rate, workload summary ("code-analysis T3" / "prefill-stress" / "custom"),
steady TPS, p99 TTFT, error rate, duration, started (relative + absolute on hover).
Filter bar: service, model, mode, status, label search, archived toggle, date range.
Checkboxes feed the compare tray. Row kebab: Re-run, Compare, Export, Archive, Delete
(confirm: "Deletes N request records permanently").

**Global states**:

- Loading: skeleton rows/tiles, never spinners on full pages.
- Errors from the API: toast with the server's `error.message`, plus inline retry where
  the failed content was.
- 409 on run start: modal linking to the active run.
- WebSocket reconnect notice (UI-6).

**Badge tooltip copy** (exact strings):

- `estimated` — "Token counts estimated client-side (tiktoken); the service did not
  report usage."
- `non-streaming` — "This API does not stream tokens; TTFT and inter-token metrics are
  unavailable."
- `client-saturated` — "The load generator hit its own limits at HH:MM:SS; later
  measurements may reflect the client, not the service."
- `overloaded` — "Arrival rate exceeded the in-flight cap; some requests were shed
  without being sent."
- `cache-buster off` — "Prompts were sent without unique prefixes; prefix caching may
  inflate prompt-processing results."
- `degraded` — "More than half of steady-state requests failed; treat results with
  caution."

**Accessibility**: all charts keyboard-focusable with data table fallback (ECharts aria
enabled); color never the sole signal (badges have text, deltas have signs); WCAG AA
contrast in both themes; `prefers-reduced-motion` disables the pulsing status dot and
live-chart animation.
