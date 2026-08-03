import { useEffect, useState } from "react";
import { Link } from "react-router";
import { api, type BenchTest } from "../api";

type SortKey = "endpoint" | "workload" | "status" | "budget_ttft_ms" | "budget_e2e_ms" |
  "sweet_spot" | "throughput" | "p95_latency" | "started_at";
type Direction = "asc" | "desc";

function sortValue(test:BenchTest,key:SortKey):string|number|null {
  if(key==="endpoint")return `${test.endpoint_name} ${test.model}`.toLowerCase();
  if(key==="workload"||key==="status"||key==="started_at")return test[key];
  if(key==="budget_ttft_ms"||key==="budget_e2e_ms")return test[key];
  if(key==="sweet_spot")return test.verdict?.knee_concurrency??null;
  if(key==="throughput")return test.verdict?.throughput_tps??null;
  return test.verdict?.p95_latency_ms??null;
}

export default function HistoryPage() {
  const [tests,setTests]=useState<BenchTest[]>([]); const [endpoint,setEndpoint]=useState(""); const [model,setModel]=useState("");
  const [sort,setSort]=useState<{key:SortKey;direction:Direction}>({key:"started_at",direction:"desc"});
  const [deleting,setDeleting]=useState<number|null>(null); const [error,setError]=useState("");
  useEffect(()=>{api.listTests().then(setTests).catch(e=>setError(e.message));},[]);
  const names=[...new Set(tests.map(test=>test.endpoint_name))].sort();
  const rows=tests.filter(test=>(!endpoint||test.endpoint_name===endpoint)&&(!model||test.model.toLowerCase().includes(model.toLowerCase())))
    .sort((a,b)=>{const av=sortValue(a,sort.key);const bv=sortValue(b,sort.key);
      if(av==null&&bv==null)return 0;if(av==null)return 1;if(bv==null)return -1;
      const result=typeof av==="number"&&typeof bv==="number"?av-bv:String(av).localeCompare(String(bv),undefined,{numeric:true});
      return sort.direction==="asc"?result:-result;});
  const budget=(value:number|null)=>value==null?"—":`${Math.round(value)} ms`;
  const changeSort=(key:SortKey)=>setSort(current=>current.key===key
    ? {key,direction:current.direction==="asc"?"desc":"asc"}:{key,direction:"asc"});
  const sortHeader=(key:SortKey,label:string)=><th aria-sort={sort.key===key?(sort.direction==="asc"?"ascending":"descending"):"none"}>
    <button className="sort-button" onClick={()=>changeSort(key)}>{label}<span aria-hidden="true">{sort.key===key?(sort.direction==="asc"?"▲":"▼"):"↕"}</span></button></th>;
  const deleteTest=async(test:BenchTest)=>{
    if(!confirm(`Delete test #${test.id} (${test.endpoint_name} / ${test.model}) and all of its results? This cannot be undone.`))return;
    setDeleting(test.id);setError("");
    try{await api.deleteTest(test.id);setTests(current=>current.filter(item=>item.id!==test.id));}
    catch(e){setError(e instanceof Error?e.message:"Could not delete the test results.");}
    finally{setDeleting(null);}
  };
  return <div><h2>History</h2><div style={{display:"flex",gap:12,maxWidth:500}}><div style={{flex:1}}>
    <label htmlFor="history-endpoint">Endpoint filter</label><select id="history-endpoint" value={endpoint} onChange={e=>setEndpoint(e.target.value)}><option value="">All</option>{names.map(name=><option key={name}>{name}</option>)}</select></div>
    <div style={{flex:1}}><label htmlFor="history-model">Model filter</label><input id="history-model" value={model} onChange={e=>setModel(e.target.value)}/></div></div>
    {error&&<p className="error" role="alert">{error}</p>}
    <div style={{overflowX:"auto"}}><table className="history-table" style={{marginTop:"1rem",minWidth:1140}}><thead><tr>
      {sortHeader("endpoint","Endpoint / model")}{sortHeader("workload","Workload")}{sortHeader("status","Status")}
      {sortHeader("budget_ttft_ms","TTFT budget")}{sortHeader("budget_e2e_ms","E2E budget")}
      {sortHeader("sweet_spot","Sweet spot")}{sortHeader("throughput","tok/s")}{sortHeader("p95_latency","p95 latency")}
      {sortHeader("started_at","Started")}<th className="actions-column">Actions</th></tr></thead>
      <tbody>{rows.map(test=><tr key={test.id}><td><Link to={`/tests/${test.id}`}>{test.endpoint_name} / {test.model}</Link></td><td>{test.workload}</td><td>{test.status}</td>
        <td className="metric">{budget(test.budget_ttft_ms)}</td><td className="metric">{budget(test.budget_e2e_ms)}</td>
        <td className="metric">{test.verdict?.knee_concurrency ?? "—"}</td><td className="metric">{test.verdict?Math.round(test.verdict.throughput_tps):"—"}</td>
        <td className="metric">{test.verdict?.p95_latency_ms!=null?`${Math.round(test.verdict.p95_latency_ms)} ms`:"—"}</td><td>{test.started_at}</td>
        <td className="actions-column"><button className="danger" disabled={deleting===test.id||test.status==="running"} title={test.status==="running"?"Stop the running test before deleting it":"Delete this test and its results"}
          onClick={()=>deleteTest(test)}>{deleting===test.id?"Deleting…":"Delete"}</button></td></tr>)}</tbody></table></div>
  </div>;
}
