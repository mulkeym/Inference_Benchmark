import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir:"e2e", timeout:120_000, use:{baseURL:"http://localhost:8080"},
  webServer:[
    {command:"python3 -m tools.mockserver.app --port 9000 --ttft-ms 10 --tps 2000 --output-tokens 10",cwd:"..",port:9000,reuseExistingServer:true},
    {command:"sh -c 'rm -rf /tmp/bench-e2e && DATA_DIR=/tmp/bench-e2e PORT=8080 python3 -m bench.main'",cwd:"..",port:8080,reuseExistingServer:false},
  ],
});
