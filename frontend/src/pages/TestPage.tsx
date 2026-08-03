import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router";
import * as echarts from "echarts";
import { api, type BenchTest, type Step, wsUrl } from "../api";
import { buildOption, type LatencyThreshold } from "../chart";
import PromptAnalysis from "./PromptAnalysis";

interface Tick { concurrency:number; requests_done:number; step_pct:number; tps_now:number;
  p95_latency_now_ms:number|null; p95_ttft_now_ms:number|null; p95_e2e_now_ms:number|null;
  errors:number; elapsed_s:number; }

const displayMs = (value:number|null) => value == null ? "N/A" : Math.round(value);

function latencyThresholds(test:BenchTest,steps:Step[],streaming:boolean):LatencyThreshold[]{
  const thresholds:LatencyThreshold[]=[];
  if(streaming&&test.budget_ttft_ms!=null)thresholds.push({label:`p95 TTFT budget: ${Math.round(test.budget_ttft_ms)} ms`,value:test.budget_ttft_ms,color:"#d9a13d"});
  if(test.budget_e2e_ms!=null)thresholds.push({label:`p95 E2E budget: ${Math.round(test.budget_e2e_ms)} ms`,value:test.budget_e2e_ms,color:"#e5534b"});
  if(!thresholds.length&&steps.length){const first=[...steps].sort((a,b)=>a.concurrency-b.concurrency)[0];
    const baseline=streaming?first.ttft_p95_ms:first.e2e_p95_ms;if(baseline!=null)thresholds.push({
      label:`default 5× baseline ${streaming?"TTFT":"E2E"} guard: ${Math.round(baseline*5)} ms`,value:baseline*5,color:"#b07cff"});}
  return thresholds;
}

export default function TestPage() {
  const {id}=useParams(); const navigate=useNavigate(); const testId=Number(id);
  const [test,setTest]=useState<BenchTest|null>(null); const [steps,setSteps]=useState<Step[]>([]);
  const [tick,setTick]=useState<Tick|null>(null); const [error,setError]=useState("");
  const activeConcurrency=tick?.concurrency??null;
  const chartElement=useRef<HTMLDivElement>(null); const chart=useRef<echarts.ECharts|null>(null);
  useEffect(()=>{let websocket:WebSocket|null=null;let poll:number|undefined;let alive=true;
    api.getTest(testId).then(current=>{if(!alive)return;setTest(current);setSteps(current.steps??[]);if(current.status!=="running")return;
      websocket=new WebSocket(wsUrl(testId)); websocket.onmessage=event=>{const message=JSON.parse(event.data);
        if(message.type==="snapshot")setSteps(message.data.steps); if(message.type==="tick")setTick(message.data);
        if(message.type==="step")setSteps(previous=>previous.some(s=>s.concurrency===message.data.concurrency)?previous:[...previous,message.data]);
        if(message.type==="status")api.getTest(testId).then(done=>{setTest(done);setSteps(done.steps??[]);setTick(null);});};
      websocket.onclose=()=>{poll=window.setInterval(async()=>{const current=await api.getTest(testId);setTest(current);setSteps(current.steps??[]);if(current.status!=="running"&&poll)clearInterval(poll);},2000);};
    }).catch(e=>setError(e.message)); return()=>{alive=false;websocket?.close();if(poll)clearInterval(poll);chart.current?.dispose();chart.current=null;};},[testId]);
  useEffect(()=>{if(!chartElement.current||!test)return;chart.current??=echarts.init(chartElement.current);
    const streaming=test.supports_streaming==null?test.endpoint_type==="openai":Boolean(test.supports_streaming);
    chart.current.setOption(buildOption(steps,test.verdict,streaming,
      activeConcurrency!=null?{concurrency:activeConcurrency}:null,
      latencyThresholds(test,steps,streaming)),true);},[steps,test,activeConcurrency]);
  if(error)return <p className="error">{error}</p>; if(!test)return <p>Loading…</p>;
  const running=test.status==="running"; const verdict=test.verdict; const latencyLabel=verdict?.latency_metric==="e2e"?"p95 E2E":"p95 TTFT";
  const streaming=test.supports_streaming==null?test.endpoint_type==="openai":Boolean(test.supports_streaming);
  const thresholds=latencyThresholds(test,steps,streaming); const stopReason=String(test.flags.stop_reason??"");
  const refinement=typeof test.flags.refinement_concurrency==="number"?test.flags.refinement_concurrency:null;
  let stopExplanation="";
  if(stopReason==="latency_blowup"&&thresholds[0]){const field=streaming?"ttft_p95_ms":"e2e_p95_ms";
    const crossing=[...steps].sort((a,b)=>a.concurrency-b.concurrency).find(step=>(step[field]??-Infinity)>thresholds[0].value);
    stopExplanation=`p95 ${streaming?"TTFT":"E2E"}${crossing?` reached ${Math.round(crossing[field]??0)} ms at concurrency ${crossing.concurrency}`:""}, exceeding the ${Math.round(thresholds[0].value)} ms default guard.`;}
  else if(stopReason==="budget_exceeded")stopExplanation="A configured latency budget was exceeded.";
  else if(stopReason==="flat_throughput")stopExplanation="Throughput improved by less than 10% for two consecutive steps.";
  else if(stopReason==="error_rate")stopExplanation="The request error rate exceeded 10%.";
  else if(stopReason==="user_stop")stopExplanation="The test was stopped by the user.";
  if(stopExplanation&&refinement!=null)stopExplanation+=` A boundary refinement was then measured at concurrency ${refinement}.`;
  return <div><header style={{display:"flex",justifyContent:"space-between",alignItems:"center",gap:12}}><div>
    <b>{test.endpoint_name} / {test.model}</b> · {test.workload} workload <span className="badge">{test.status.toUpperCase()}{running?(tick?` — step ${tick.concurrency}`:" — preparing"):""}</span>
    {Object.entries(test.flags).map(([flag,value])=><span key={flag} className="badge" title={flag.replaceAll("_"," ")}>
      {flag.replaceAll("_","-")}{value !== true ? `: ${value}` : ""}</span>)}</div>
    {running?<button className="danger" onClick={()=>api.stopTest(testId).catch(e=>setError(e.message))}>■ Stop</button>:<span>
      <a href={`/api/tests/${testId}/export.html`}>Export HTML</a>{" · "}<a href={`/api/tests/${testId}/export.csv`}>Export CSV</a>{" · "}
      <button className="danger" onClick={async()=>{if(confirm("Delete this test and all its data?")){await api.deleteTest(testId);navigate("/history");}}}>Delete</button></span>}
  </header><section className="card" style={{marginTop:"1rem"}}>
    {running&&<div className={`step-progress${tick?"":" is-indeterminate"}`} role="progressbar"
      aria-label={tick?`Measuring concurrency ${tick.concurrency}`:"Preparing and warming up"}
      aria-valuemin={0} aria-valuemax={100} aria-valuenow={tick?.step_pct} aria-busy={!tick}>
      <div className="step-progress-label">{tick?<><span>Measuring concurrency <b>{tick.concurrency}</b></span>
        <b className="metric">{tick.step_pct}%</b></>:<><span>Preparing and warming up…</span><b className="metric">RUNNING</b></>}</div>
      <div className="step-progress-track"><div className="step-progress-fill" style={tick?{width:`${tick.step_pct}%`}:undefined}/></div>
    </div>}
    <div ref={chartElement} style={{width:"100%",height:420}}/>
    {!running&&stopExplanation&&<p className="stop-reason"><b>Why it stopped:</b> {stopExplanation}</p>}
    {!running&&(verdict?<p className="metric">Sweet spot <b>{verdict.knee_concurrency}</b> concurrent · <b>{Math.round(verdict.throughput_tps)}</b> tok/s · {latencyLabel} <b>{Math.round(verdict.p95_latency_ms??0)}</b> ms
      {verdict.budget&&(verdict.budget.met
        ? verdict.budget.crossed
          ? <> · budget boundary <b>~{verdict.budget.max_concurrency}</b> concurrent (limited by {verdict.budget.limited_by})</>
          : <> · budget held through highest tested concurrency <b>{verdict.budget.max_concurrency}</b> (crossing not reached)</>
        : <> · <b className="error">budget not met at concurrency 1</b></>)}</p>
      :<p className="muted">No verdict: {test.error??"fewer than 3 completed steps, or the load generator was saturated."}</p>)}</section>
  {running&&tick&&<div className="stat-row" style={{display:"flex",gap:12,marginTop:12}}>{[
    ["Current step",`${tick.concurrency} × · ${tick.requests_done} reqs`],["Throughput now",`${tick.tps_now} tok/s`],
    ...(streaming?[["p95 TTFT now",tick.p95_ttft_now_ms==null?"—":`${tick.p95_ttft_now_ms} ms`]]:[]),
    ["p95 E2E now",tick.p95_e2e_now_ms==null?"—":`${tick.p95_e2e_now_ms} ms`],["Errors",String(tick.errors)]
  ].map(([label,value])=><div className="card metric" style={{flex:1,minWidth:150}} key={label}><label>{label}</label><b>{value}</b></div>)}</div>}
  {steps.length>0&&<table style={{marginTop:"1.25rem"}} className="metric"><thead><tr><th>Concurrency</th><th>Requests</th><th>tok/s</th><th>TTFT p50</th><th>TTFT p95</th><th>E2E p50</th><th>E2E p95</th><th>Errors</th></tr></thead>
    <tbody>{steps.map(step=><tr key={step.concurrency}><td>{step.concurrency}</td><td>{step.requests_completed}</td><td>{Math.round(step.throughput_tps??0)}</td>
      <td>{displayMs(step.ttft_p50_ms)}</td><td>{displayMs(step.ttft_p95_ms)}</td><td>{displayMs(step.e2e_p50_ms)}</td><td>{displayMs(step.e2e_p95_ms)}</td><td>{step.error_count}</td></tr>)}</tbody></table>}
  {!running&&steps.length>0&&<PromptAnalysis testId={testId} preferredConcurrency={verdict?.knee_concurrency??null}/>} 
  </div>;
}
