from collections import defaultdict

from bench.engine.metrics import percentile


def _percentile(values: list[float], p: float) -> float | None:
    return percentile(values, p) if values else None


def analyze_requests(requests: list[dict], prompt_texts: dict[str, str] | None = None) -> dict:
    """Aggregate ground-truth request rows by prompt and concurrency."""
    groups: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for request in requests:
        groups[(request["prompt_id"], request["concurrency"])].append(request)

    cells = []
    for (prompt_id, concurrency), group in sorted(groups.items(),
                                                   key=lambda item: (item[0][0], item[0][1])):
        successful = [request for request in group if request.get("error_class") is None]
        ttfts = [float(request["ttft_ms"]) for request in successful
                 if request.get("ttft_ms") is not None]
        e2es = [float(request["e2e_ms"]) for request in successful
                if request.get("e2e_ms") is not None]
        prompt_tokens = [float(request["prompt_tokens"]) for request in successful
                         if request.get("prompt_tokens") is not None]
        output_tokens = [float(request["output_tokens"]) for request in successful
                         if request.get("output_tokens") is not None]
        output_rates = []
        rates_estimated = False
        for request in successful:
            output = request.get("output_tokens")
            e2e = request.get("e2e_ms")
            if output is None or e2e is None or e2e <= 0:
                continue
            ttft = request.get("ttft_ms")
            if ttft is not None and e2e > ttft:
                generation_ms = e2e - ttft
            else:
                generation_ms = e2e
                rates_estimated = True
            output_rates.append(float(output) / (generation_ms / 1000.0))

        cells.append({
            "prompt_id": prompt_id,
            "concurrency": concurrency,
            "request_count": len(group),
            "success_count": len(successful),
            "error_count": len(group) - len(successful),
            "ttft_p50_ms": _percentile(ttfts, 50),
            "ttft_p95_ms": _percentile(ttfts, 95),
            "e2e_p50_ms": _percentile(e2es, 50),
            "e2e_p95_ms": _percentile(e2es, 95),
            "prompt_tokens_p50": _percentile(prompt_tokens, 50),
            "output_tokens_p50": _percentile(output_tokens, 50),
            "output_rate_tps_p50": _percentile(output_rates, 50),
            "output_rate_estimated": rates_estimated,
        })

    prompt_ids = sorted({cell["prompt_id"] for cell in cells})
    return {
        "prompts": prompt_ids,
        "concurrencies": sorted({cell["concurrency"] for cell in cells}),
        "cells": cells,
        "prompt_texts": {prompt_id: prompt_texts[prompt_id]
                         for prompt_id in prompt_ids
                         if prompt_texts and prompt_id in prompt_texts},
    }
