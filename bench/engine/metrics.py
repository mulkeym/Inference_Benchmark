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
        "throughput_tps": out_tokens / duration_s if duration_s > 0 else None,
        "ttft_p50_ms": percentile(ttfts, 50) if ttfts else None,
        "ttft_p95_ms": percentile(ttfts, 95) if ttfts else None,
        "e2e_p50_ms": percentile(e2es, 50) if e2es else None,
        "e2e_p95_ms": percentile(e2es, 95) if e2es else None,
        "error_count": len(results) - len(ok),
        "started_at": started_at,
        "duration_s": duration_s,
    }
