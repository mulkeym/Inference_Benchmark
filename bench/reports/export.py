import csv
import html
import io
import json
from pathlib import Path

VENDOR_JS = Path(__file__).resolve().parent / "vendor" / "echarts.min.js"
CSV_COLS = ["concurrency", "prompt_id", "t_send_wall", "ttft_ms", "e2e_ms",
            "prompt_tokens", "output_tokens", "tokens_estimated", "error_class", "error_detail"]


def _defuse(value):
    """Neutralize spreadsheet formula injection in text cells (=, +, -, @ prefixes)."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return value


def to_csv(_test: dict, requests: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows({k: _defuse(v) for k, v in row.items()} for row in requests)
    return buffer.getvalue()


def _verdict_text(verdict: dict | None) -> str:
    if not verdict:
        return "No verdict (fewer than 3 completed steps or client-saturated run)."
    metric = "p95 TTFT" if verdict["latency_metric"] == "ttft" else "p95 E2E"
    text = (f"Sweet spot <b>{verdict['knee_concurrency']}</b> concurrent · "
            f"<b>{round(verdict['throughput_tps'] or 0)}</b> tok/s · "
            f"{metric} <b>{round(verdict['p95_latency_ms'] or 0)}</b> ms")
    budget = verdict.get("budget")
    if budget:
        if budget["met"]:
            if budget.get("crossed"):
                text += (f" · budget boundary <b>~{budget['max_concurrency']}</b> concurrent "
                         f"(limited by {budget['limited_by']})")
            else:
                text += (f" · budget held through highest tested concurrency "
                         f"<b>{budget['max_concurrency']}</b> (crossing not reached)")
        else:
            text += " · <b>budget not met at any tested concurrency</b>"
    guard = verdict.get("guard")
    if guard:
        text += (f" · default {guard['metric'].upper()} guard "
                 f"<b>{round(guard['limit_ms'])}</b> ms")
        if guard.get("crossed"):
            text += f" · boundary <b>~{guard['max_concurrency']}</b> concurrent"
    return text


def _display_ms(value: float | None) -> str | int:
    return "N/A" if value is None else round(value)


def _latency_thresholds(test: dict, steps: list[dict]) -> list[dict]:
    thresholds = []
    if test.get("budget_ttft_ms") is not None:
        thresholds.append({"label": f"p95 TTFT budget: {round(test['budget_ttft_ms'])} ms",
                           "value": test["budget_ttft_ms"], "color": "#d9a13d"})
    if test.get("budget_e2e_ms") is not None:
        thresholds.append({"label": f"p95 E2E budget: {round(test['budget_e2e_ms'])} ms",
                           "value": test["budget_e2e_ms"], "color": "#e5534b"})
    if not thresholds and steps:
        first = min(steps, key=lambda step: step["concurrency"])
        verdict = test.get("verdict") or {}
        metric = verdict.get("latency_metric") or (
            "ttft" if first.get("ttft_p95_ms") is not None else "e2e")
        baseline = first.get(f"{metric}_p95_ms")
        if baseline is not None:
            value = baseline * 5
            thresholds.append({"label": f"default 5× baseline {metric.upper()} guard: {round(value)} ms",
                               "value": value, "color": "#b07cff"})
    return thresholds


def to_html(test: dict, steps: list[dict], endpoint_name: str) -> str:
    if not VENDOR_JS.exists():
        raise RuntimeError(f"missing vendored echarts at {VENDOR_JS}")
    rows = "".join(
        f"<tr><td>{s['concurrency']}</td><td>{s['requests_completed']}</td>"
        f"<td>{round(s['throughput_tps'] or 0)}</td>"
        f"<td>{_display_ms(s['ttft_p50_ms'])}</td>"
        f"<td>{_display_ms(s['ttft_p95_ms'])}</td>"
        f"<td>{_display_ms(s['e2e_p50_ms'])}</td><td>{_display_ms(s['e2e_p95_ms'])}</td>"
        f"<td>{s['error_count']}</td></tr>" for s in steps)
    endpoint = html.escape(endpoint_name)
    model = html.escape(test["model"])
    workload = html.escape(test["workload"])
    status = html.escape(test["status"])
    steps_json = json.dumps(steps).replace("</", "<\\/")
    verdict_json = json.dumps(test.get("verdict")).replace("</", "<\\/")
    thresholds_json = json.dumps(_latency_thresholds(test, steps)).replace("</", "<\\/")
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Benchmark {test['id']} — {endpoint} / {model}</title>
<style>body{{font-family:system-ui,sans-serif;background:#0e1116;color:#e6e6e6;max-width:960px;margin:2rem auto;padding:0 1rem}}table{{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}}th,td{{border:1px solid #333;padding:4px 8px;text-align:right;font-size:.85rem}}</style>
<script>{VENDOR_JS.read_text()}</script></head><body>
<h1>{endpoint} / {model} — {workload} workload — {status}</h1>
<div>{_verdict_text(test.get('verdict'))}</div><div id="chart" style="height:420px"></div>
<table><thead><tr><th>Concurrency</th><th>Requests</th><th>tok/s</th><th>TTFT p50</th><th>TTFT p95</th><th>E2E p50</th><th>E2E p95</th><th>Errors</th></tr></thead><tbody>{rows}</tbody></table>
<script>var steps={steps_json};var verdict={verdict_json};var latencyThresholds={thresholds_json};var chart=echarts.init(document.getElementById('chart'),'dark');var ttftSteps=steps.filter(s=>Number.isFinite(s.ttft_p95_ms));var series=[{{name:'throughput (tok/s)',type:'line',yAxisIndex:0,color:'#5ba3f5',symbol:'circle',symbolSize:8,lineStyle:{{color:'#5ba3f5',width:2}},data:steps.map(s=>[s.concurrency,s.throughput_tps])}},{{name:'p95 E2E (ms)',type:'line',yAxisIndex:1,color:'#d96f6f',symbol:'circle',symbolSize:7,lineStyle:{{color:'#d96f6f',width:2,type:'dashed'}},itemStyle:{{color:'#d96f6f'}},data:steps.filter(s=>Number.isFinite(s.e2e_p95_ms)).map(s=>[s.concurrency,s.e2e_p95_ms])}}];if(ttftSteps.length)series.push({{name:'p95 TTFT (ms)',type:'line',yAxisIndex:1,color:'#f4c152',symbol:'triangle',symbolSize:9,showSymbol:true,z:10,lineStyle:{{color:'#f4c152',width:4,type:'solid'}},itemStyle:{{color:'#f4c152',borderColor:'#0e1116',borderWidth:1}},data:ttftSteps.map(s=>[s.concurrency,s.ttft_p95_ms])}});if(latencyThresholds.length)series.push({{name:'latency threshold',type:'line',yAxisIndex:1,data:[],silent:true,animation:false,markLine:{{symbol:'none',label:{{show:true,position:'insideEndTop',formatter:'{{b}}'}},data:latencyThresholds.map(t=>({{name:t.label,yAxis:t.value,lineStyle:{{color:t.color,type:'dashed',width:2}},label:{{color:t.color}}}}))}}}});if(verdict)series.push({{name:'sweet zone',type:'line',markArea:{{itemStyle:{{color:'rgba(80,200,140,.12)'}},data:[[{{xAxis:verdict.sweet_zone[0]}},{{xAxis:verdict.sweet_zone[1]}}]]}},data:[]}});if(verdict&&verdict.budget&&verdict.budget.crossed)series.push({{name:'budget crossing',type:'scatter',yAxisIndex:1,symbol:'diamond',symbolSize:13,data:[[verdict.budget.max_concurrency,verdict.budget.limit_ms]]}});chart.setOption({{legend:{{bottom:0}},grid:{{left:78,right:88,top:48,bottom:95}},xAxis:{{type:'log',logBase:2,min:1,name:'concurrency',nameLocation:'middle',nameGap:30,axisLabel:{{margin:12}}}},yAxis:[{{type:'value',name:'output tok/s',position:'left',nameTextStyle:{{color:'#5ba3f5',fontWeight:'bold'}},axisLabel:{{color:'#5ba3f5'}},axisLine:{{show:true,lineStyle:{{color:'#5ba3f5',width:2}}}},axisTick:{{show:true,lineStyle:{{color:'#5ba3f5'}}}},splitLine:{{lineStyle:{{color:'rgba(91,163,245,.10)'}}}}}},{{type:'value',name:'p95 latency (ms)',position:'right',nameTextStyle:{{color:'#d9a13d',fontWeight:'bold'}},axisLabel:{{color:'#d9a13d'}},axisLine:{{show:true,lineStyle:{{color:'#d9a13d',width:2}}}},axisTick:{{show:true,lineStyle:{{color:'#d9a13d'}}}},splitLine:{{show:false}}}}],tooltip:{{trigger:'axis'}},series:series}});</script>
</body></html>"""
