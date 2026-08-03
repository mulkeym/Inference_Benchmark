#!/usr/bin/env bash
set -euo pipefail
docker build -t inference-benchmark:test .
python3 -m tools.mockserver.app --port 9000 --ttft-ms 10 --tps 2000 --output-tokens 10 &
MOCK_PID=$!
docker run -d --rm --name bench-e2e -p 18080:8080 --add-host host.docker.internal:host-gateway inference-benchmark:test
trap 'kill $MOCK_PID; docker stop bench-e2e >/dev/null 2>&1 || true' EXIT
sleep 3
curl -sf http://localhost:18080/healthz | grep '"ok"'
EP=$(curl -sf -X POST http://localhost:18080/api/endpoints -H 'Content-Type: application/json' -d '{"name":"mock","type":"openai","base_url":"http://host.docker.internal:9000/v1"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
TID=$(curl -sf -X POST http://localhost:18080/api/tests -H 'Content-Type: application/json' -d "{\"endpoint_id\":$EP,\"model\":\"mock-model\",\"workload\":\"chat\",\"settings\":{\"dwell_s\":1,\"min_requests\":3,\"max_concurrency\":8}}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
for _ in $(seq 1 90); do STATUS=$(curl -sf http://localhost:18080/api/tests/$TID | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])'); [ "$STATUS" != "running" ] && break; sleep 1; done
[ "$STATUS" = "completed" ] || { echo "FAIL: status=$STATUS"; exit 1; }
curl -sf http://localhost:18080/api/tests/$TID | grep -q knee_concurrency
EXPORT_FILE=$(mktemp /tmp/inference-benchmark-export.XXXXXX.html)
curl -sf -o "$EXPORT_FILE" http://localhost:18080/api/tests/$TID/export.html
grep -q echarts "$EXPORT_FILE"
rm -f "$EXPORT_FILE"
echo PASS
