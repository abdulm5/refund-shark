'use client';
import {useCallback, useEffect, useRef, useState} from 'react';
import {ArrowDownToLine, Check, FileText, FileUp, Loader2, ShieldCheck} from 'lucide-react';
import {api, date} from '@/lib/api';
import type {DocumentRecord, InvoiceFields} from '@/lib/recovery';

export async function uploadPdf(file: File, kind: 'contract'|'invoice'): Promise<DocumentRecord> {
  if (file.size > 10_000_000) throw new Error('Choose a PDF no larger than 10 MB.');
  return api<DocumentRecord>(`/documents?kind=${kind}&filename=${encodeURIComponent(file.name)}`, {method:'POST',headers:{'Content-Type':'application/pdf'},body:file});
}

export default function DocumentsView({onRead}: {onRead:(id:string)=>void}) {
  const [docs,setDocs]=useState<DocumentRecord[]>([]);
  const [selected,setSelected]=useState<DocumentRecord|null>(null);
  const [kind,setKind]=useState<'contract'|'invoice'>('contract');
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const picker=useRef<HTMLInputElement>(null);
  const refresh=useCallback(async()=>setDocs((await api<{documents:DocumentRecord[]}>('/documents')).documents),[]);
  useEffect(()=>{refresh().catch(e=>setError(e.message));},[refresh]);
  async function choose(id:string) {try{setSelected(await api<DocumentRecord>(`/documents/${id}`));setError('');}catch(e){setError((e as Error).message);}}
  async function upload(file:File, type=kind) {setBusy(true);setError('');try{const d=await uploadPdf(file,type);setSelected(d);await refresh();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  async function sample(name:'contract'|'invoice'|'credit') {setBusy(true);setError('');try{const r=await fetch(`/api/documents/samples/${name}`);if(!r.ok)throw new Error('The sample PDF could not be loaded.');await upload(new File([await r.blob()],`sample-${name}.pdf`,{type:'application/pdf'}),name==='contract'?'contract':'invoice');}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  return <div className="recovery-view">
    <div className="page-heading"><div><div className="eyebrow">SOURCE DOCUMENTS</div><h1>Documents</h1><p>Keep the agreement, the invoice, and the evidence behind every credit together.</p></div></div>
    <section className="recovery-upload"><FileUp size={25}/><div><h2>Add a contract or invoice</h2><p>Searchable PDF · up to 10 MB / 60 pages. Original files stay in this local workspace.</p></div><select aria-label="Document type" value={kind} onChange={e=>setKind(e.target.value as typeof kind)}><option value="contract">Contract / SLA</option><option value="invoice">Invoice / credit note</option></select><button className="button primary" disabled={busy} onClick={()=>picker.current?.click()}>{busy?<Loader2 size={15} className="spin"/>:<FileUp size={15}/>}Upload PDF</button><input hidden type="file" ref={picker} accept="application/pdf,.pdf" onChange={e=>{if(e.target.files?.[0])void upload(e.target.files[0]);e.target.value='';}}/></section>
    <div className="sample-links"><span>Try a simulated sample:</span>{(['invoice','credit','contract'] as const).map(s=><button key={s} disabled={busy} className="text-button" onClick={()=>sample(s)}>{s==='credit'?'Invoice with credit':s==='contract'?'Agreement excerpts':'Subscription invoice'}</button>)}</div>
    {error&&<div role="alert" className="notice reader-error">{error}</div>}
    <div className="recovery-split">
      <section className="recovery-list" aria-label="Saved documents"><div className="section-heading"><h2>Saved documents <span>{docs.length}</span></h2></div>{!docs.length&&<div className="recovery-empty"><FileText size={28}/><h3>Your evidence starts here</h3><p>Upload a PDF or try a sample above. Invoice fields are reviewed before use.</p></div>}{docs.map(d=><button className={`document-list-row ${selected?.id===d.id?'selected':''}`} key={d.id} onClick={()=>choose(d.id)}><FileText size={19}/><span><strong>{d.filename}</strong><small>{d.kind==='contract'?'Agreement':'Invoice'} · {d.page_count} page{d.page_count===1?'':'s'} · {date(d.created_at)}</small><small>{d.simulated?'Simulated sample · ':''}{d.confirmed?'Fields confirmed':d.kind==='invoice'?'Review required':'Ready to read'}</small></span>{d.confirmed&&<ShieldCheck size={15}/>}</button>)}</section>
      {selected?<section className="recovery-panel" key={selected.id}><div className="recovery-panel-head"><div><span className="overline">{selected.simulated?'SIMULATED SAMPLE':'UPLOADED DOCUMENT'}</span><h2>{selected.filename}</h2></div><a className="button secondary small" href={`/api/documents/${selected.id}/file`}><ArrowDownToLine size={14}/>Original PDF</a></div>
        {selected.kind==='invoice'?<InvoiceReview doc={selected} onConfirmed={d=>{setSelected(d);void refresh();}}/>:<div className="recovery-callout"><p>Read the agreement to extract its terms. Matched quotes include page references; unsupported rules stay in review.</p><button className="button primary" onClick={()=>onRead(selected.id)}>Read agreement <FileText size={14}/></button></div>}
        <details className="pdf-pages" open={selected.kind==='contract'}><summary>Extracted text · {selected.page_count} page{selected.page_count===1?'':'s'}</summary>{selected.pages?.map(p=><article key={p.number}><div>PAGE {p.number}</div><pre>{p.text}</pre></article>)}</details><p className="document-hash">SHA-256 {selected.sha256}</p>
      </section>:<section className="recovery-panel recovery-empty"><ShieldCheck size={30}/><h3>Review the source, then use it</h3><p>Select a document to see its text by page and confirm invoice details.</p></section>}
    </div>
  </div>;
}

const invoiceLabels:Record<string,string>={vendor:'Vendor',customer:'Customer',account_reference:'Account reference',product:'Product',plan:'Plan',invoice_number:'Invoice number',period:'Billing month',issued_on:'Issue date',currency:'Currency',billing_cycle:'Billing cycle',fees:'Product fee before credit',credit_amount:'Service credit on this invoice',credit_period:'Original claim month',claim_reference:'Claim reference'};
function InvoiceReview({doc,onConfirmed}:{doc:DocumentRecord;onConfirmed:(d:DocumentRecord)=>void}) {
  const [fields,setFields]=useState<Record<string,string>>(()=>Object.fromEntries(Object.keys(invoiceLabels).map(k=>[k,String(doc.confirmed?.[k as keyof InvoiceFields]??doc.suggestions?.[k]?.value??(k==='credit_amount'?'0':k==='billing_cycle'?'monthly':''))])));
  const [reviewed,setReviewed]=useState(false);const [busy,setBusy]=useState(false);const [error,setError]=useState('');
  async function submit(e:React.FormEvent){e.preventDefault();setBusy(true);setError('');try{onConfirmed(await api<DocumentRecord>(`/documents/${doc.id}/confirm`,{method:'POST',body:JSON.stringify({...fields,currency:fields.currency.toUpperCase(),credit_period:fields.credit_period||null,claim_reference:fields.claim_reference||null,reviewed:true})}));}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  return <form className="invoice-review" onSubmit={submit}><div className="recovery-callout"><strong>{doc.confirmed?'Invoice fields confirmed':'Review invoice fields'}</strong><p>{doc.confirmed?'These reviewed values are preserved with the original PDF.':'Suggestions come from explicit labels, not guessed totals. Check every field against the PDF; enter any fields we could not extract.'}</p></div><div className="case-grid">{Object.entries(invoiceLabels).map(([key,label])=><label className="reader-field" key={key}><span>{label}</span>{key==='billing_cycle'?<select disabled={!!doc.confirmed} value={fields[key]} onChange={e=>setFields({...fields,[key]:e.target.value})}><option value="monthly">Monthly</option><option value="annual">Annual</option></select>:<input disabled={!!doc.confirmed} required={!['credit_period','claim_reference'].includes(key)||Number(fields.credit_amount)>0} type={['period','credit_period'].includes(key)?'month':key==='issued_on'?'date':'text'} inputMode={['fees','credit_amount'].includes(key)?'decimal':undefined} value={fields[key]} onChange={e=>setFields({...fields,[key]:e.target.value})}/>}{doc.suggestions?.[key]&&<small title={doc.suggestions[key].quote}>Suggested from page {doc.suggestions[key].page}</small>}</label>)}</div>{error&&<p role="alert" className="notice reader-error">{error}</p>}{!doc.confirmed&&<><label className="case-check"><input type="checkbox" checked={reviewed} onChange={e=>setReviewed(e.target.checked)}/>I checked these values against the PDF, including the credit’s amount and claim reference.</label><button className="button primary" disabled={!reviewed||busy}>{busy?<Loader2 className="spin" size={15}/>:<Check size={15}/>}Confirm invoice fields</button></>}</form>;
}
