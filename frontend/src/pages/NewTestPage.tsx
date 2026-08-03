import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router";
import { api, type Endpoint } from "../api";

const WORKLOADS: Record<string,{label:string;desc:string}> = {
  chat:{label:"Chat (balanced)",desc:"~500-token prompts, ~300-token answers"},
  long_context:{label:"Long context",desc:"~4,000-token prompts, ~200-token answers"},
  generation:{label:"Generation",desc:"~80-token prompts, ~1,000-token answers"},
};

export default function NewTestPage() {
  const navigate = useNavigate();
  const [endpoints,setEndpoints] = useState<Endpoint[]>([]);
  const [epId,setEpId] = useState<number|null>(null);
  const [model,setModel] = useState(""); const [workload,setWorkload] = useState("chat");
  const [budgetTtft,setBudgetTtft] = useState(""); const [budgetE2e,setBudgetE2e] = useState("");
  const [advanced,setAdvanced] = useState(false); const [maxC,setMaxC] = useState("128");
  const [dwell,setDwell] = useState("45"); const [timeout,setTimeoutS] = useState("180");
  const [temperature,setTemperature] = useState("0"); const [error,setError] = useState("");
  useEffect(() => { api.listEndpoints().then(items => { setEndpoints(items);
    if (items.length) { setEpId(items[0].id); setModel(items[0].default_model ?? ""); }}).catch(e=>setError(e.message)); }, []);
  const endpoint = endpoints.find(item => item.id === epId);
  const streaming = endpoint ? (endpoint.supports_streaming == null ? endpoint.type === "openai" : Boolean(endpoint.supports_streaming)) : true;
  const hasBudget = Boolean(budgetE2e || (streaming && budgetTtft));
  const plan = useMemo(() => { const ceiling=Math.max(1,parseInt(maxC)||128); const steps:number[]=[];
    for(let c=1;c<=ceiling;c*=2) steps.push(c); return {steps,mins:Math.round(steps.length*((parseFloat(dwell)||45)+5)/60)}; }, [maxC,dwell]);
  async function start() {
    setError(""); if(!epId){setError("Add an endpoint first.");return;} if(!model){setError("Model is required.");return;}
    try { const test=await api.startTest({endpoint_id:epId,model,workload,
      budget_ttft_ms:budgetTtft?Number(budgetTtft):null,budget_e2e_ms:budgetE2e?Number(budgetE2e):null,
      settings:{max_concurrency:parseInt(maxC)||128,dwell_s:parseFloat(dwell)||45,
        timeout_s:parseFloat(timeout)||180,temperature:parseFloat(temperature)||0}}); navigate(`/tests/${test.id}`); }
    catch(e){setError((e as Error).message);}
  }
  return <div className="two-panel" style={{display:"flex",gap:"1.5rem",alignItems:"flex-start"}}>
    <section className="card" style={{flex:3}}><h2>New Test</h2>
      <label htmlFor="endpoint">Endpoint</label><select id="endpoint" value={epId ?? ""} onChange={e => {const id=Number(e.target.value);setEpId(id);const selected=endpoints.find(x=>x.id===id);setModel(selected?.default_model ?? "");}}>
        {endpoints.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select>
      {!endpoints.length && <p><Link to="/endpoints">Add an endpoint first →</Link></p>}
      <label htmlFor="model">Model</label><input id="model" value={model} onChange={e=>setModel(e.target.value)}/>
      <label htmlFor="workload">Workload</label><select id="workload" value={workload} onChange={e=>setWorkload(e.target.value)}>{Object.entries(WORKLOADS).map(([key,value])=><option key={key} value={key}>{value.label}</option>)}</select>
      <label htmlFor="budget-ttft">p95 TTFT budget (ms, optional)</label><input id="budget-ttft" value={budgetTtft} disabled={!streaming} placeholder={streaming?"e.g. 1000":"N/A — non-streaming endpoint"} onChange={e=>setBudgetTtft(e.target.value)}/>
      <label htmlFor="budget-e2e">p95 E2E budget (ms, optional)</label><input id="budget-e2e" value={budgetE2e} placeholder="e.g. 8000" onChange={e=>setBudgetE2e(e.target.value)}/>
      <p className="muted">The example values are not defaults. Leaving both budgets blank uses an adaptive 5× concurrency-1 p95 latency guard and the throughput-flattening guard.</p>
      <p><a href="#advanced" onClick={e=>{e.preventDefault();setAdvanced(!advanced);}}>{advanced?"▾":"▸"} Advanced</a></p>
      {advanced && <div id="advanced"><label htmlFor="max-concurrency">Max concurrency ceiling</label><input id="max-concurrency" value={maxC} onChange={e=>setMaxC(e.target.value)}/>
        <label htmlFor="step-dwell">Step dwell (seconds)</label><input id="step-dwell" value={dwell} onChange={e=>setDwell(e.target.value)}/>
        <label htmlFor="request-timeout">Request timeout (seconds)</label><input id="request-timeout" value={timeout} onChange={e=>setTimeoutS(e.target.value)}/>
        <label htmlFor="temperature">Temperature</label><input id="temperature" value={temperature} onChange={e=>setTemperature(e.target.value)}/></div>}
    </section>
    <aside className="card" style={{flex:2,borderStyle:"dashed"}}><h3>Test plan</h3>
      <p>Will sweep concurrency <b>{plan.steps.join(" → ")}</b>, ~{dwell} s per step.</p>
      <p>Workload: {WORKLOADS[workload].desc}.</p><p>Estimated total: <b>~{plan.mins} min</b> before any refinement.</p>
      <p>{hasBudget
        ? "Will continue while all latency budgets hold, then measure one midpoint after the first crossing."
        : "Stops early once throughput flattens or latency grows beyond the default guard."}</p>
      {!streaming && <p className="badge">non-streaming: E2E latency only</p>}{error && <p className="error">{error}</p>}
      <button className="primary" style={{width:"100%",marginTop:8}} onClick={start}>▶ Find sweet spot</button></aside>
  </div>;
}
