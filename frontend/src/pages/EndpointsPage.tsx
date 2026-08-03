import { useEffect, useState } from "react";
import { api, type Endpoint, type ProbeResult } from "../api";

interface EndpointForm { name:string; type:"openai"|"asksage"; base_url:string;
  api_key:string; default_model:string; verify_tls:boolean; }
const emptyForm=():EndpointForm=>({name:"",type:"openai",base_url:"",api_key:"",default_model:"",verify_tls:true});

export default function EndpointsPage() {
  const [endpoints,setEndpoints]=useState<Endpoint[]>([]); const [form,setForm]=useState<EndpointForm>(emptyForm());
  const [editing,setEditing]=useState<number|null>(null); const [probes,setProbes]=useState<Record<number,ProbeResult|"loading">>({});
  const [availableModels,setAvailableModels]=useState<string[]>([]); const [fetchingModels,setFetchingModels]=useState(false);
  const [modelStatus,setModelStatus]=useState(""); const [error,setError]=useState("");
  const reload=()=>api.listEndpoints().then(setEndpoints).catch(e=>setError(e.message));
  useEffect(()=>{reload();},[]);

  const resetForm=()=>{setForm(emptyForm());setEditing(null);setAvailableModels([]);setModelStatus("");};
  const changeConnection=(change:Partial<Pick<EndpointForm,"type"|"base_url"|"api_key"|"verify_tls">>)=>{
    setForm(current=>({...current,...change}));setAvailableModels([]);setModelStatus("");
  };
  async function save(){setError("");const body:Partial<EndpointForm>={...form};if(!body.api_key)delete body.api_key;
    try{if(editing)await api.updateEndpoint(editing,body);else await api.createEndpoint(body);resetForm();reload();}
    catch(e){setError((e as Error).message);}}
  async function probe(id:number){setProbes(p=>({...p,[id]:"loading"}));
    try{const result=await api.probeEndpoint(id);setProbes(p=>({...p,[id]:result}));reload();}
    catch(e){setError((e as Error).message);}}
  async function fetchModels(){setError("");setModelStatus("");setFetchingModels(true);
    try{const result=await api.fetchEndpointModels({type:form.type,base_url:form.base_url,
        api_key:form.api_key||null,verify_tls:form.verify_tls,endpoint_id:editing});
      setAvailableModels(result.models);
      if(result.models.length){setModelStatus(`${result.models.length} available model${result.models.length===1?"":"s"} found.`);
        setForm(current=>({...current,default_model:current.default_model||result.models[0]}));}
      else setModelStatus("No models were returned; enter the model ID manually.");
    }catch(e){setAvailableModels([]);setError((e as Error).message);}
    finally{setFetchingModels(false);}}
  return <div><h2>Endpoints</h2>{error&&<p className="error" role="alert">{error}</p>}
    <table><thead><tr><th>Name</th><th>Type</th><th>Base URL</th><th>Model</th><th>Streaming</th><th>Actions</th></tr></thead>
      <tbody>{endpoints.map(ep=><tr key={ep.id}><td>{ep.name}</td><td>{ep.type}</td><td style={{textAlign:"left"}}>{ep.base_url}</td>
        <td>{ep.default_model??"—"}</td><td>{ep.supports_streaming==null?"?":ep.supports_streaming?"yes":"no"}</td><td>
          <button onClick={()=>probe(ep.id)}>Test connection</button>{" "}
          <button onClick={()=>{setEditing(ep.id);setForm({name:ep.name,type:ep.type,base_url:ep.base_url,
            api_key:"",default_model:ep.default_model??"",verify_tls:ep.verify_tls});setAvailableModels([]);setModelStatus("");}}>Edit</button>{" "}
          <button className="danger" onClick={async()=>{if(!confirm(`Delete endpoint ${ep.name}?`))return;
            try{await api.deleteEndpoint(ep.id);reload();}catch(e){setError((e as Error).message);}}}>Delete</button>
          {probes[ep.id]==="loading"&&<div className="muted">probing…</div>}
          {probes[ep.id]&&probes[ep.id]!=="loading"&&<div className="muted">{(probes[ep.id] as ProbeResult).auth_ok
            ? `OK · ${(probes[ep.id] as ProbeResult).latency_ms} ms · models: ${(probes[ep.id] as ProbeResult).models.join(", ")||"n/a"}`
            : `Failed: ${(probes[ep.id] as ProbeResult).error}`}</div>}
        </td></tr>)}</tbody></table>

    <section className="card" style={{marginTop:"1.5rem",maxWidth:560}}><h3>{editing?"Edit endpoint":"Add endpoint"}</h3>
      <label htmlFor="endpoint-name">Name</label><input id="endpoint-name" value={form.name} onChange={e=>setForm({...form,name:e.target.value})}/>
      <label htmlFor="endpoint-type">Type</label><select id="endpoint-type" value={form.type} disabled={editing!=null}
        title={editing?"Endpoint type cannot be changed after creation":undefined}
        onChange={e=>changeConnection({type:e.target.value as EndpointForm["type"]})}>
        <option value="openai">OpenAI-compatible</option><option value="asksage">AskSage</option></select>
      {form.type==="asksage"&&<p className="muted">Non-streaming API — TTFT is unavailable; the E2E latency budget applies.</p>}
      <label htmlFor="base-url">Base URL</label><input id="base-url" value={form.base_url} placeholder="https://host:8000/v1" onChange={e=>changeConnection({base_url:e.target.value})}/>
      <label htmlFor="api-key">API key {editing?"(leave blank to keep current)":"(optional)"}</label><input id="api-key" type="password" value={form.api_key} onChange={e=>changeConnection({api_key:e.target.value})}/>
      <label className="check"><input type="checkbox" checked={form.verify_tls} onChange={e=>changeConnection({verify_tls:e.target.checked})}/> Verify TLS</label>
      <div style={{marginTop:"1rem"}}><button onClick={fetchModels} disabled={fetchingModels||!form.base_url.trim()}>
        {fetchingModels?"Fetching models…":"Fetch available models"}</button></div>
      {modelStatus&&<p className="muted" aria-live="polite">{modelStatus}</p>}
      <label htmlFor="default-model">Default model</label><input id="default-model" list="endpoint-model-options" value={form.default_model}
        placeholder={availableModels.length?"Select an available model":"Enter a model ID"}
        onChange={e=>setForm({...form,default_model:e.target.value})}/>
      <datalist id="endpoint-model-options">{availableModels.map(model=><option key={model} value={model}/>)}</datalist>
      {availableModels.length>0&&<p className="muted">Choose from the fetched suggestions or enter a model ID manually.</p>}
      <div style={{marginTop:"1rem"}}><button className="primary" onClick={save}>{editing?"Save":"Add endpoint"}</button>{" "}
        {editing&&<button onClick={resetForm}>Cancel</button>}</div>
    </section>
  </div>;
}
