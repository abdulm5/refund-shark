'use client';
import {useCallback, useEffect, useRef, useState} from 'react';
import {Check, FileUp, FlaskConical, Link2} from 'lucide-react';
import {api} from '@/lib/api';
import MonthlyCase from './monthly-case';
import Monitoring from './monitoring';
import type {AgreementRead, DocumentRecord, Evidence, VendorProfile} from '@/lib/recovery';

export default function VendorEvidence({vendor,read,invoices,dirty,onStarted}:{vendor:VendorProfile;read?:AgreementRead;invoices:DocumentRecord[];dirty:boolean;onStarted:(id:string)=>void}) {
  const [items,setItems]=useState<Evidence[]>([]);const[configured,setConfigured]=useState(false);const[busy,setBusy]=useState(false);const[error,setError]=useState('');
  const inv=invoices.find(d=>d.id===vendor.invoice_id)?.confirmed;
  const [period,setPeriod]=useState(inv?.period||'');
  const [ticket,setTicket]=useState({reference:'',url:'',opened_at:'',incident_start:'',incident_end:''});
  const picker=useRef<HTMLInputElement>(null);
  const refresh=useCallback(async()=>{const r=await api<{evidence:Evidence[];integration_configured:boolean}>(`/vendors/${vendor.id}/evidence`);setItems(r.evidence);setConfigured(r.integration_configured);},[vendor.id]);
  useEffect(()=>{refresh().catch(e=>setError(e.message));},[refresh]);
  async function add(body:object){setBusy(true);setError('');try{const e=await api<Evidence>(`/vendors/${vendor.id}/evidence`,{method:'POST',body:JSON.stringify(body)});await refresh();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  async function log(file:File){if(file.size>8_000_000){setError('Choose a CSV under 8 MB.');return;}await add({kind:'logs',period,label:file.name,csv:await file.text()});}
  async function sample(){setBusy(true);setError('');try{const r=await fetch('/api/logs/sample.csv');if(!r.ok)throw new Error('Could not load the sample log.');setPeriod('2026-09');await add({kind:'logs',period:'2026-09',label:'Sample September log (simulated)',csv:await r.text(),simulated:true});}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  async function saveTicket(e:React.FormEvent){e.preventDefault();await add({kind:'ticket',period,label:ticket.reference,...ticket,url:ticket.url||null,opened_at:ticket.opened_at?`${ticket.opened_at}:00Z`:null,incident_start:ticket.incident_start?`${ticket.incident_start}:00Z`:null,incident_end:ticket.incident_end?`${ticket.incident_end}:00Z`:null});}
  return <section className="recovery-panel"><div className="recovery-panel-head"><div><span className="overline">EVIDENCE INBOX</span><h2>From customer impact to a claim</h2></div><span className="badge gray">{items.length} records</span></div>
    <p className="muted">Logs are measured under the linked agreement. Ticket times are checked against the window you supply. All records stay attached to this vendor.</p>
    <div className="evidence-upload-row"><label className="reader-field"><span>Evidence month</span><input type="month" value={period} onChange={e=>setPeriod(e.target.value)}/></label><button className="button secondary" disabled={busy||!period||!vendor.read_id} onClick={()=>picker.current?.click()}><FileUp size={14}/>Import request log</button><button className="text-button" disabled={busy||!vendor.read_id} onClick={sample}><FlaskConical size={14}/>Sample September log</button><input hidden type="file" accept=".csv,text/csv" ref={picker} onChange={e=>{if(e.target.files?.[0])void log(e.target.files[0]);e.target.value='';}}/></div>
    <details className="recovery-details"><summary>Add a support-ticket reference</summary><form onSubmit={saveTicket}><div className="case-grid">{([['reference','Ticket reference','text'],['url','Ticket URL (optional)','url'],['opened_at','Opened at (UTC)','datetime-local'],['incident_start','Incident began (UTC, optional)','datetime-local'],['incident_end','Incident ended (UTC, optional)','datetime-local']] as const).map(([k,label,type])=><label className="reader-field" key={k}><span>{label}</span><input type={type} required={k==='reference'||k==='opened_at'} value={ticket[k]} onChange={e=>setTicket({...ticket,[k]:e.target.value})}/></label>)}</div><p className="muted">Leave the incident window unknown if it has not been confirmed. A ticket opened outside that window cannot satisfy the timing requirement.</p><div className="form-actions"><button className="button secondary" disabled={busy||!period}>Save ticket reference</button>{vendor.simulated&&<button type="button" className="text-button" onClick={()=>{setPeriod('2026-09');setTicket({reference:'DEMO-SUPPORT-001',url:'',opened_at:'2026-09-03T16:00',incident_start:'2026-09-03T15:00',incident_end:'2026-09-03T17:30'});}}>Fill simulated ticket</button>}</div></form></details>
    <div className="evidence-records">{items.map(e=><div key={e.id}><Check size={14}/><div><strong>{e.label}</strong><small>{e.period} · {e.source} · {e.simulated?'Simulated':'Provided evidence'}</small><p>{e.analysis?`${e.analysis.downtime_minutes} downtime minutes · ${e.analysis.minutes_covered.toLocaleString()} / ${e.analysis.period_minutes.toLocaleString()} minutes covered`:e.ticket_during_incident===null?'Incident timing not confirmed':e.ticket_during_incident?'Ticket opened during the supplied incident window':'Ticket opened outside the supplied incident window'}</p></div></div>)}</div>
    <details className="recovery-details"><summary><Link2 size={14}/> Connect a monitoring or support export</summary><p>Push the same CSV counts or ticket metadata into this inbox. The integration is {configured?'configured':'not configured'}; set a server-side evidence token before using it.</p><code className="endpoint">POST /api/integrations/evidence/{vendor.id}</code><p className="muted">Use Authorization: Bearer with SHARK_EVIDENCE_TOKEN. Logs need minute, experience, requests, errors. Integration setup and a ready-to-run uploader are in docs/INTEGRATIONS.md.</p></details>
    <Monitoring vendorId={vendor.id} read={read} onImported={refresh}/>
    {error&&<p role="alert" className="notice reader-error">{error}</p>}
    <MonthlyCase vendor={vendor} read={read} invoices={invoices} items={items} dirty={dirty} onStarted={onStarted}/>

  </section>;
}
