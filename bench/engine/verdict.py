GAIN_THRESHOLD = 0.10


def _knee_index(steps: list[dict]) -> int:
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
    return None


def compute_verdict(steps: list[dict], budget_ttft_ms: float | None,
                    budget_e2e_ms: float | None, streaming: bool,
                    flags: dict) -> dict | None:
    if len(steps) < 3 or flags.get("client_saturated"):
        return None
    steps = sorted(steps, key=lambda s: s["concurrency"])
    budget_specs = []
    if budget_ttft_ms is not None and streaming:
        budget_specs.append(("ttft", "ttft_p95_ms", budget_ttft_ms))
    if budget_e2e_ms is not None:
        budget_specs.append(("e2e", "e2e_p95_ms", budget_e2e_ms))

    # An explicit budget is an operational constraint, so an over-budget
    # measurement can locate the boundary but cannot itself be the sweet spot.
    guard_limit = flags.get("latency_guard_ms") if not budget_specs else None
    guard_field = "ttft_p95_ms" if flags.get("latency_guard_metric") == "ttft" else "e2e_p95_ms"
    eligible = [
        step for step in steps
        if all(step.get(field) is not None and step[field] <= limit
               for _, field, limit in budget_specs)
    ] if budget_specs else ([step for step in steps
                             if step.get(guard_field) is not None and step[guard_field] <= guard_limit]
                            if guard_limit is not None else steps)
    selection_steps = eligible or steps
    k = _knee_index(selection_steps)
    knee = selection_steps[k]
    lat_field = "ttft_p95_ms" if streaming else "e2e_p95_ms"
    verdict = {
        "knee_concurrency": knee["concurrency"],
        "sweet_zone": [selection_steps[max(0, k - 1)]["concurrency"],
                       selection_steps[min(len(selection_steps) - 1, k + 1)]["concurrency"]],
        "throughput_tps": knee["throughput_tps"],
        "p95_latency_ms": knee.get(lat_field),
        "latency_metric": "ttft" if streaming else "e2e",
        "budget": None,
        "guard": None,
    }
    if budget_specs:
        max_measured = steps[-1]["concurrency"]
        crossings = []
        for name, field, limit in budget_specs:
            crossing = interpolate_budget(steps, field, limit)
            if crossing is not None:
                crossings.append((name, crossing, limit))
        met = all(steps[0].get(field) is not None and steps[0][field] <= limit
                  for _, field, limit in budget_specs)
        if crossings:
            limited_by, max_c, limit_ms = min(crossings, key=lambda result: result[1])
            verdict["budget"] = {
                "max_concurrency": max_c,
                "limited_by": limited_by,
                "limit_ms": limit_ms,
                "met": met,
                "crossed": True,
            }
        else:
            verdict["budget"] = {
                "max_concurrency": max_measured,
                "limited_by": None,
                "limit_ms": None,
                "met": met,
                "crossed": False,
            }
    elif guard_limit is not None:
        crossing = interpolate_budget(steps, guard_field, guard_limit)
        verdict["guard"] = {
            "metric": flags.get("latency_guard_metric"),
            "limit_ms": guard_limit,
            "max_concurrency": crossing if crossing is not None else max(s["concurrency"] for s in steps),
            "crossed": crossing is not None,
        }
    return verdict
