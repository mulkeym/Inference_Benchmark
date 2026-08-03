# Inference Benchmark

[![CI](https://github.com/mulkeym/Inference_Benchmark/actions/workflows/ci.yml/badge.svg)](https://github.com/mulkeym/Inference_Benchmark/actions/workflows/ci.yml)

Finds the sweet spot between concurrency, throughput (tokens/sec), and latency
for an LLM inference endpoint (OpenAI-compatible or AskSage).

## Run

Pull the pre-built image published by CI on every push to `main`:

    docker run -p 8080:8080 -v bench-data:/data ghcr.io/mulkeym/inference_benchmark:latest

Or build it locally:

    docker build -t inference-benchmark .
    docker run -p 8080:8080 -v bench-data:/data inference-benchmark

Open http://localhost:8080 — add an endpoint, pick a model and workload, then hit
**Find sweet spot**. The tool sweeps concurrency 1 → 2 → 4 → …, stops once
throughput flattens, and reports the knee, sweet zone, and (if configured) the
maximum concurrency within latency budgets.

## Development

    pip install -e ".[dev]" && pytest
    cd frontend && npm install && npm run dev
    python -m tools.mockserver.app --port 9000

Spec: `docs/superpowers/specs/2026-08-02-inference-benchmark-simplified-design.md`
