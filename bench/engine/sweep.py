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


async def _run_step(conn, test_id, adapter, model, cycler, concurrency, config,
                    streaming, publish, stop_event):
    results = []
    done = asyncio.Event()
    started_wall = now_wall()
    started = time.perf_counter()

    async def worker():
        while not done.is_set() and not stop_event.is_set():
            prompt = cycler.next()
            result = await adapter.execute(prompt["text"], model, prompt["max_tokens"],
                                           config.temperature)
            result.prompt_id = prompt["id"]
            results.append(result)
            db.insert_request(conn, result.to_row(test_id, concurrency))
            elapsed = time.perf_counter() - started
            if elapsed >= config.dwell_s and len(results) >= config.min_requests:
                done.set()

    async def ticker():
        while not done.is_set() and not stop_event.is_set():
            await asyncio.sleep(1.0)
            ok = [result for result in results if result.ok]
            ttfts = [result.ttft_ms for result in ok if result.ttft_ms is not None]
            e2es = [result.e2e_ms for result in ok if result.e2e_ms is not None]
            latencies = ttfts if streaming else e2es
            elapsed = time.perf_counter() - started
            time_fraction = elapsed / config.dwell_s if config.dwell_s else 1.0
            request_fraction = len(results) / config.min_requests if config.min_requests else 1.0
            publish("tick", {
                "concurrency": concurrency,
                "requests_done": len(results),
                "step_pct": min(99, int(100 * min(time_fraction, request_fraction))),
                "tps_now": round(sum(result.output_tokens or 0 for result in ok) / elapsed, 1)
                if elapsed > 0 else 0,
                "p95_latency_now_ms": round(metrics.percentile(latencies, 95), 1)
                if latencies else None,
                "p95_ttft_now_ms": round(metrics.percentile(ttfts, 95), 1)
                if ttfts else None,
                "p95_e2e_now_ms": round(metrics.percentile(e2es, 95), 1)
                if e2es else None,
                "errors": len(results) - len(ok),
                "elapsed_s": int(elapsed),
            })

    ticker_task = asyncio.create_task(ticker())
    workers = [asyncio.create_task(worker()) for _ in range(concurrency)]
    await asyncio.gather(*workers)
    ticker_task.cancel()
    duration = time.perf_counter() - started
    return metrics.aggregate_step(concurrency, results, duration, started_wall)


def _validate_config(config: SweepConfig) -> None:
    if config.max_concurrency < 1:
        raise ValueError("max_concurrency must be at least 1")
    if config.dwell_s < 0 or config.min_requests < 1 or config.warmup_requests < 0:
        raise ValueError("invalid sweep timing or request count")


def _budget_specs(config: SweepConfig, streaming: bool) -> list[tuple[str, str, float]]:
    specs = []
    if streaming and config.budget_ttft_ms is not None:
        specs.append(("ttft", "ttft_p95_ms", config.budget_ttft_ms))
    if config.budget_e2e_ms is not None:
        specs.append(("e2e", "e2e_p95_ms", config.budget_e2e_ms))
    return specs


def _exceeded_budgets(step: dict, specs: list[tuple[str, str, float]]) -> list[str]:
    return [name for name, field, limit in specs
            if step.get(field) is not None and step[field] > limit]


async def run_sweep(conn, test_id: int, adapter, model: str, streaming: bool,
                    config: SweepConfig, publish: Callable[[str, dict], None],
                    stop_event: asyncio.Event) -> None:
    flags: dict = {}
    monitor = SaturationMonitor()
    try:
        _validate_config(config)
        cycler = PromptCycler(load_prompts(config.workload), config.seed)
        monitor.start()

        for _ in range(config.warmup_requests):
            prompt = cycler.next()
            await adapter.execute(prompt["text"], model, prompt["max_tokens"], config.temperature)

        steps: list[dict] = []
        budgets = _budget_specs(config, streaming)
        baseline_p95 = None
        flat_count = 0

        async def measure_midpoint(lower: int, upper: int) -> None:
            midpoint = (lower + upper) // 2
            if not lower < midpoint < upper or stop_event.is_set():
                return
            refinement = await _run_step(
                conn, test_id, adapter, model, cycler, midpoint,
                config, streaming, publish, stop_event)
            db.insert_step(conn, {**refinement, "test_id": test_id})
            steps.append(refinement)
            publish("step", refinement)
            flags["refinement_concurrency"] = midpoint

        concurrency = 1
        while concurrency <= config.max_concurrency and not stop_event.is_set():
            step = await _run_step(conn, test_id, adapter, model, cycler, concurrency,
                                   config, streaming, publish, stop_event)
            if stop_event.is_set() and step["requests_completed"] == 0:
                break
            db.insert_step(conn, {**step, "test_id": test_id})
            steps.append(step)
            publish("step", step)

            if monitor.saturated and not flags.get("client_saturated"):
                flags["client_saturated"] = True
                db.set_flag(conn, test_id, "client_saturated")
                publish("flag", {"flag": "client_saturated"})

            if concurrency == 1 and step["requests_completed"] == 0:
                requests = db.list_requests(conn, test_id)
                detail = next((r["error_detail"] for r in reversed(requests) if r["error_detail"]),
                              "all requests failed")
                conn.execute("UPDATE tests SET error=? WHERE id=?", (detail, test_id))
                conn.commit()
                db.finish_test(conn, test_id, "failed", None, flags)
                publish("status", {"status": "failed", "verdict": None})
                return

            latency_key = "ttft_p95_ms" if streaming else "e2e_p95_ms"
            if concurrency == 1:
                baseline_p95 = step.get(latency_key)
                if not budgets and baseline_p95 is not None:
                    flags["latency_guard_metric"] = "ttft" if streaming else "e2e"
                    flags["latency_guard_ms"] = baseline_p95 * LATENCY_BLOWUP

            stop_reason = None
            total = step["requests_completed"] + step["error_count"]
            if total and step["error_count"] / total > ERROR_RATE_LIMIT:
                stop_reason = "error_rate"
            exceeded = _exceeded_budgets(step, budgets)
            if not stop_reason and budgets and exceeded:
                stop_reason = "budget_exceeded"
                flags["budget_metrics_exceeded"] = ",".join(exceeded)

                # The doubling sweep brackets the boundary coarsely. Measure
                # one arithmetic midpoint to give the verdict and chart a
                # materially better local estimate of the budget crossing.
                if len(steps) >= 2:
                    lower = steps[-2]["concurrency"]
                    await measure_midpoint(lower, concurrency)

            # Explicit latency budgets take precedence over the generic curve
            # guards. While the budgets hold, continue toward the ceiling even
            # if throughput flattens or latency grows beyond 5x baseline.
            p95 = step.get(latency_key)
            guard_ms = flags.get("latency_guard_ms")
            if (not budgets and not stop_reason and guard_ms is not None and
                    p95 is not None and p95 > guard_ms):
                stop_reason = "latency_blowup"
                flags["latency_guard_crossed_at"] = concurrency
                if len(steps) >= 2:
                    await measure_midpoint(steps[-2]["concurrency"], concurrency)

            if not budgets and not stop_reason and len(steps) >= 2:
                previous, current = steps[-2]["throughput_tps"], step["throughput_tps"]
                if previous and current is not None and (current - previous) / previous < GAIN_THRESHOLD:
                    flat_count += 1
                else:
                    flat_count = 0
                if flat_count >= 2:
                    stop_reason = stop_reason or "flat_throughput"
            if stop_reason:
                flags["stopped_early"] = True
                flags["stop_reason"] = stop_reason
                publish("flag", {"flag": "stopped_early", "reason": stop_reason})
                break
            concurrency *= 2

        status = "stopped" if stop_event.is_set() else "completed"
        if stop_event.is_set():
            flags["stop_reason"] = "user_stop"
        request_rows = db.list_requests(conn, test_id)
        if any(row["tokens_estimated"] for row in request_rows):
            flags["tokens_estimated"] = True
        if not streaming:
            flags["non_streaming"] = True
        step_rows = db.list_steps(conn, test_id)
        verdict = compute_verdict(step_rows, config.budget_ttft_ms,
                                  config.budget_e2e_ms, streaming, flags)
        db.finish_test(conn, test_id, status, verdict, flags)
        publish("status", {"status": status, "verdict": verdict})
    except Exception as exc:
        conn.execute("UPDATE tests SET error=? WHERE id=?", (str(exc)[:500], test_id))
        conn.commit()
        db.finish_test(conn, test_id, "failed", None, flags)
        publish("status", {"status": "failed", "verdict": None})
    finally:
        monitor.stop()
        close = getattr(adapter, "aclose", None)
        if close:
            await close()
