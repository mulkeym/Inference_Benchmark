import type { EChartsOption } from "echarts";
import type { Step, Verdict } from "./api";

export interface LatencyThreshold { label:string; value:number; color:string; }

export function buildOption(steps: Step[], verdict: Verdict | null,
    streaming: boolean, inProgress: {concurrency:number} | null,
    thresholds: LatencyThreshold[] = []): EChartsOption {
  const ordered = [...steps].sort((a,b) => a.concurrency - b.concurrency);
  const series: NonNullable<EChartsOption["series"]> = [
    { name:"throughput (tok/s)", type:"line", yAxisIndex:0, symbolSize:8, color:"#5ba3f5",
      data:ordered.map(step => [step.concurrency, step.throughput_tps]),
      markArea: verdict ? { itemStyle:{color:"rgba(63,178,127,.12)"},
        data:[[{xAxis:verdict.sweet_zone[0]}, {xAxis:verdict.sweet_zone[1]}]] } : undefined,
      markLine: verdict ? { symbol:"none", lineStyle:{color:"#3fb27f",type:"dashed"},
        label:{formatter:`sweet spot: ${verdict.knee_concurrency}`},
        data:[{xAxis:verdict.knee_concurrency}] } : undefined },
    { name:"p95 E2E (ms)", type:"line", yAxisIndex:1,
      color:"#d96f6f", lineStyle:{type:"dashed"}, symbolSize:7,
      data:ordered.map(step => [step.concurrency, step.e2e_p95_ms]) },
  ];
  if (streaming) series.push({ name:"p95 TTFT (ms)", type:"line", yAxisIndex:1,
    color:"#d9a13d", lineStyle:{type:"dotted"}, symbolSize:7,
    data:ordered.map(step => [step.concurrency, step.ttft_p95_ms]) });
  if (verdict?.budget?.crossed && verdict.budget.limit_ms != null) {
    series.push({ name:`${verdict.budget.limited_by?.toUpperCase()} budget`,
      type:"scatter", yAxisIndex:1, symbol:"diamond", symbolSize:13, color:"#e5534b",
      data:[[verdict.budget.max_concurrency, verdict.budget.limit_ms]],
      label:{show:true,position:"top",color:"#e5534b",formatter:"budget crossing"} });
  }
  if (thresholds.length) series.push({name:"latency threshold",type:"line",yAxisIndex:1,data:[],silent:true,animation:false,
    markLine:{symbol:"none",label:{show:true,position:"insideEndTop",formatter:"{b}"},
      data:thresholds.map(threshold=>({name:threshold.label,yAxis:threshold.value,
        lineStyle:{color:threshold.color,type:"dashed",width:2},label:{color:threshold.color}}))}});
  if (inProgress) series.push({ name:"measuring", type:"line", yAxisIndex:0, data:[],
    markLine:{symbol:"none",lineStyle:{color:"#d9a13d",type:"dotted"},
      label:{show:false},silent:true,
      data:[{xAxis:inProgress.concurrency}]}});
  return { backgroundColor:"transparent", tooltip:{trigger:"axis"},
    legend:{bottom:0,textStyle:{color:"#8b949e"}},
    grid:{left:78,right:88,top:48,bottom:95},
    xAxis:{type:"log",logBase:2,name:"concurrency",nameLocation:"middle",nameGap:30,
      min:1,axisLabel:{color:"#8b949e",margin:12}},
    yAxis:[{type:"value",name:"output tok/s",position:"left",
      nameTextStyle:{color:"#5ba3f5",fontWeight:"bold"},axisLabel:{color:"#5ba3f5"},
      axisLine:{show:true,lineStyle:{color:"#5ba3f5",width:2}},axisTick:{show:true,lineStyle:{color:"#5ba3f5"}},
      splitLine:{lineStyle:{color:"rgba(91,163,245,.10)"}}},
      {type:"value",name:"p95 latency (ms)",position:"right",
        nameTextStyle:{color:"#d9a13d",fontWeight:"bold"},axisLabel:{color:"#d9a13d"},
        axisLine:{show:true,lineStyle:{color:"#d9a13d",width:2}},axisTick:{show:true,lineStyle:{color:"#d9a13d"}},
        splitLine:{show:false}}], series };
}
