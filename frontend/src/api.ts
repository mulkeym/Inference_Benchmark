export interface Endpoint { id: number; name: string; type: "openai" | "asksage";
  base_url: string; default_model: string | null; verify_tls: boolean;
  supports_streaming: number | null; has_api_key: boolean; }
export interface Step { concurrency: number; requests_completed: number;
  throughput_tps: number | null; ttft_p50_ms: number | null;
  ttft_p95_ms: number | null; e2e_p50_ms: number | null;
  e2e_p95_ms: number | null; error_count: number; duration_s: number; }
export interface Verdict { knee_concurrency: number; sweet_zone: [number, number];
  throughput_tps: number; p95_latency_ms: number | null;
  latency_metric: "ttft" | "e2e";
  budget: { max_concurrency: number; limited_by: string | null;
    limit_ms: number | null; met: boolean; crossed: boolean } | null;
  guard?: { max_concurrency: number; metric: "ttft" | "e2e";
    limit_ms: number; crossed: boolean } | null; }
export interface BenchTest { id: number; endpoint_id: number; endpoint_name: string;
  endpoint_type: string; supports_streaming: number | null; model: string;
  workload: string; status: string; budget_ttft_ms: number | null;
  budget_e2e_ms: number | null; flags: Record<string, boolean | string | number>;
  verdict: Verdict | null; started_at: string; finished_at: string | null;
  error: string | null; steps?: Step[]; settings: Record<string, unknown>; }
export interface ProbeResult { reachable: boolean; auth_ok: boolean;
  models: string[]; supports_streaming: boolean; latency_ms: number | null;
  error: string | null; }
export interface ModelListResult { models: string[]; }
export interface PromptCell { prompt_id: string; concurrency: number;
  request_count: number; success_count: number; error_count: number;
  ttft_p50_ms: number | null; ttft_p95_ms: number | null;
  e2e_p50_ms: number | null; e2e_p95_ms: number | null;
  prompt_tokens_p50: number | null; output_tokens_p50: number | null;
  output_rate_tps_p50: number | null; output_rate_estimated: boolean; }
export interface PromptAnalysis { prompts: string[]; concurrencies: number[];
  cells: PromptCell[]; prompt_texts: Record<string,string>; }

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" }, ...init });
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new Error(body?.error?.message ?? `HTTP ${response.status}`);
  return body as T;
}

export const api = {
  listEndpoints: () => request<Endpoint[]>("/api/endpoints"),
  createEndpoint: (data: object) => request<Endpoint>("/api/endpoints", {
    method: "POST", body: JSON.stringify(data) }),
  updateEndpoint: (id: number, data: object) => request<Endpoint>(`/api/endpoints/${id}`, {
    method: "PUT", body: JSON.stringify(data) }),
  deleteEndpoint: (id: number) => request<{ok: boolean}>(`/api/endpoints/${id}`, { method: "DELETE" }),
  probeEndpoint: (id: number) => request<ProbeResult>(`/api/endpoints/${id}/probe`, { method: "POST" }),
  fetchEndpointModels: (data: object) => request<ModelListResult>("/api/endpoints/models", {
    method: "POST", body: JSON.stringify(data) }),
  startTest: (data: object) => request<BenchTest>("/api/tests", { method: "POST", body: JSON.stringify(data) }),
  listTests: () => request<BenchTest[]>("/api/tests"),
  getTest: (id: number) => request<BenchTest>(`/api/tests/${id}`),
  getPromptAnalysis: (id: number) =>
    request<PromptAnalysis>(`/api/tests/${id}/prompt-analysis`),
  stopTest: (id: number) => request<{ok: boolean}>(`/api/tests/${id}/stop`, { method: "POST" }),
  deleteTest: (id: number) => request<{ok: boolean}>(`/api/tests/${id}`, { method: "DELETE" }),
};

export function wsUrl(testId: number): string {
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  return `${protocol}://${location.host}/ws/tests/${testId}`;
}
