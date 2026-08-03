import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import * as echarts from "echarts";
import { api, type PromptAnalysis as Analysis, type PromptCell } from "../api";

type Metric = "e2e_p50_ms" | "e2e_p95_ms" | "ttft_p50_ms" | "ttft_p95_ms" | "output_rate_tps_p50";
type ColorMode = "relative" | "absolute";

const METRICS: Record<Metric,string> = {
  e2e_p50_ms:"Median E2E (ms)",
  e2e_p95_ms:"p95 E2E (ms)",
  ttft_p50_ms:"Median TTFT (ms)",
  ttft_p95_ms:"p95 TTFT (ms)",
  output_rate_tps_p50:"Median output rate (tok/s)",
};
const whole = (value:number|null) => value == null ? "N/A" : Math.round(value);
const taskText = (text:string|undefined) => text?.trim().split(/\n\s*\n/).at(-1)?.trim() ?? "Prompt text unavailable";
const escapeHtml = (text:string) => text.replace(/[&<>"']/g, char=>({
  "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;",
}[char] ?? char));

export default function PromptAnalysis({testId,preferredConcurrency}:{testId:number;preferredConcurrency:number|null}) {
  const [analysis,setAnalysis]=useState<Analysis|null>(null);
  const [metric,setMetric]=useState<Metric>("e2e_p50_ms");
  const [colorMode,setColorMode]=useState<ColorMode>("relative");
  const [concurrency,setConcurrency]=useState<number|null>(null);
  const [expandedPrompt,setExpandedPrompt]=useState<string|null>(null);
  const [error,setError]=useState("");
  const element=useRef<HTMLDivElement>(null); const chart=useRef<echarts.ECharts|null>(null);

  useEffect(()=>{api.getPromptAnalysis(testId).then(result=>{
    setAnalysis(result); const preferred=preferredConcurrency != null&&result.concurrencies.includes(preferredConcurrency)
      ? preferredConcurrency : result.concurrencies.at(-1)??null; setConcurrency(preferred);
  }).catch(e=>setError(e.message));},[testId,preferredConcurrency]);

  useEffect(()=>{if(!analysis||!element.current)return; chart.current??=echarts.init(element.current);
    const baseline=new Map<string,number>();
    [...analysis.cells].sort((a,b)=>a.concurrency-b.concurrency).forEach(cell=>{
      const value=cell[metric]; if(value!=null&&!baseline.has(cell.prompt_id))baseline.set(cell.prompt_id,value);
    });
    const isRate=metric==="output_rate_tps_p50";
    const plotted=analysis.cells.flatMap(cell=>{
      const absolute=cell[metric]; const base=baseline.get(cell.prompt_id);
      if(absolute==null||base==null||absolute<=0||base<=0)return [];
      const colorValue=colorMode==="absolute"?absolute:(isRate?base/absolute:absolute/base);
      return [{cell,absolute,colorValue}];
    });
    const values=plotted.map(item=>item.colorValue);
    const minValue=colorMode==="relative"?Math.min(1,...values):Math.min(...values);
    const rawMax=colorMode==="relative"?Math.max(1,...values):Math.max(...values);
    const maxValue=rawMax===minValue?minValue+1:rawMax;
    const data=plotted.map(({cell,absolute,colorValue})=>({
      value:[analysis.concurrencies.indexOf(cell.concurrency),analysis.prompts.indexOf(cell.prompt_id),
        colorValue,absolute,cell.request_count],
      itemStyle:cell.concurrency===concurrency?{borderColor:"#fff",borderWidth:2}:undefined,
    }));
    chart.current.setOption({backgroundColor:"transparent",
      tooltip:{formatter:(params:unknown)=>{const p=params as {data:{value:(number|string)[]}}; const value=p.data.value;
        const promptId=analysis.prompts[Number(value[1])]; const relative=colorMode==="relative"
          ? `<br/>change vs baseline: <b>${Number(value[2]).toFixed(2)}× worse</b>`:"";
        return `${promptId}: ${escapeHtml(taskText(analysis.prompt_texts[promptId]))}<br/>concurrency ${analysis.concurrencies[Number(value[0])]}<br/>${METRICS[metric]}: <b>${Math.round(Number(value[3]))}</b>${relative}<br/>requests: ${value[4]}`;}},
      grid:{left:95,right:30,top:20,bottom:85},
      xAxis:{type:"category",name:"concurrency",nameLocation:"middle",nameGap:30,
        data:analysis.concurrencies,axisLabel:{color:"#8b949e"}},
      yAxis:{type:"category",data:analysis.prompts,axisLabel:{color:"#8b949e"}},
      visualMap:{min:minValue,max:maxValue,dimension:2,calculable:true,orient:"horizontal",left:"center",bottom:0,
        textStyle:{color:"#8b949e"},formatter:(value:number)=>colorMode==="relative"?`${value.toFixed(1)}×`:String(Math.round(value)),
        inRange:{color:["#16243a","#245b8f","#4f9cf9","#d9a13d","#e5534b"]}},
      series:[{type:"heatmap",data,label:{show:false},emphasis:{itemStyle:{borderColor:"#fff",borderWidth:2}}}]
    },true);
    chart.current.off("click");
    chart.current.on("click",params=>{const value=(params.data as {value?:unknown[]}|undefined)?.value;
      const selected=analysis.concurrencies[Number(value?.[0])]; if(selected!=null)setConcurrency(selected);});
  },[analysis,metric,colorMode,concurrency]);

  useEffect(()=>()=>chart.current?.dispose(),[]);
  const rows=useMemo(()=>analysis?.cells.filter(cell=>cell.concurrency===concurrency)??[],[analysis,concurrency]);
  if(error)return <p className="error">{error}</p>; if(!analysis)return <p className="muted">Loading prompt analysis…</p>;
  if(!analysis.cells.length)return <p className="muted">No measured prompt requests are available.</p>;
  const p95Selected=metric.includes("p95");
  return <section style={{marginTop:"2rem"}}><h3>Prompt analysis</h3>
    <p className="muted">Compare prompts across measured concurrency levels. E2E includes the complete generated answer, so prompts that request longer answers naturally take longer even at the same token rate.</p>
    <div style={{display:"flex",gap:12,maxWidth:640}}><div style={{flex:1}}><label htmlFor="prompt-metric">Heatmap metric</label>
      <select id="prompt-metric" value={metric} onChange={event=>setMetric(event.target.value as Metric)}>
        {Object.entries(METRICS).map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></div>
      <div style={{flex:1}}><label htmlFor="prompt-color-mode">Color comparison</label><select id="prompt-color-mode" value={colorMode} onChange={event=>setColorMode(event.target.value as ColorMode)}>
        <option value="relative">Change vs each prompt baseline</option><option value="absolute">Absolute metric values</option></select></div></div>
    <p className="muted" style={{marginBottom:0}}>{colorMode==="relative"
      ? "Colors show how much each prompt degraded from its lowest-concurrency measurement; tooltips retain the absolute value."
      : "Colors compare absolute values across all prompts; long generated answers can dominate E2E."} Click a cell to inspect that concurrency below.</p>
    {p95Selected&&analysis.cells.some(cell=>cell.request_count<20)&&<p className="muted" style={{color:"#d9a13d"}}>Per-prompt p95 is directional here because some cells contain fewer than 20 requests. Use a median metric for a more stable comparison.</p>}
    <div className="card" style={{marginTop:12,overflow:"hidden"}}><div ref={element}
      style={{width:"100%",height:Math.max(440,analysis.prompts.length*23+150)}}/></div>
    <h4 style={{marginBottom:4}}>Prompt details — concurrency {concurrency??"—"}</h4>
    <p className="muted" style={{marginTop:0}}>The white heatmap outline marks the concurrency shown in this table.</p>
    <div style={{overflowX:"auto"}}><table className="metric" style={{marginTop:16,minWidth:1050}}><thead><tr>
      <th>Prompt</th><th>Task</th><th>Requests</th><th>Errors</th><th>TTFT p50</th><th>TTFT p95</th>
      <th>E2E p50</th><th>E2E p95</th><th>Prompt tok</th><th>Output tok</th><th>Output tok/s</th>
    </tr></thead><tbody>{rows.map((cell:PromptCell)=><Fragment key={cell.prompt_id}><tr><td>
      <button aria-expanded={expandedPrompt===cell.prompt_id}
        onClick={()=>setExpandedPrompt(current=>current===cell.prompt_id?null:cell.prompt_id)}
        style={{padding:"2px 7px",marginRight:7}}>{expandedPrompt===cell.prompt_id?"▾":"▸"}</button>
      {cell.prompt_id}</td><td style={{textAlign:"left",maxWidth:260}}>{taskText(analysis.prompt_texts[cell.prompt_id])}</td>
      <td>{cell.request_count}</td><td>{cell.error_count}</td><td>{whole(cell.ttft_p50_ms)}</td>
      <td>{whole(cell.ttft_p95_ms)}</td><td>{whole(cell.e2e_p50_ms)}</td><td>{whole(cell.e2e_p95_ms)}</td>
      <td>{whole(cell.prompt_tokens_p50)}</td><td>{whole(cell.output_tokens_p50)}</td>
      <td>{whole(cell.output_rate_tps_p50)}{cell.output_rate_estimated?"*":""}</td></tr>
      {expandedPrompt===cell.prompt_id&&<tr><td colSpan={11} style={{textAlign:"left",background:"var(--surface)"}}>
        <div className="muted" style={{marginBottom:6}}>Bundled prompt text (cache-buster prefix omitted)</div>
        <pre style={{whiteSpace:"pre-wrap",wordBreak:"break-word",maxHeight:320,overflow:"auto",
          margin:0,padding:12,background:"var(--bg)",border:"1px solid var(--border)",borderRadius:6,
          color:"var(--text)",fontFamily:"inherit"}}>{analysis.prompt_texts[cell.prompt_id]??"Prompt text unavailable."}</pre>
      </td></tr>}</Fragment>)}</tbody></table></div>
    {rows.some(cell=>cell.output_rate_estimated)&&<p className="muted">* Non-streaming output rate is approximated as output tokens ÷ E2E.</p>}
  </section>;
}
