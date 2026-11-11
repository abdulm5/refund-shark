'use client';
import {useEffect, useState} from 'react';
import {Check, CircleMinus, Clock3, ExternalLink, FileText, Link2, Loader2, ShieldCheck, Sparkles} from 'lucide-react';
import {api, date} from '@/lib/api';
import {summarizeTerm, termLabels} from '@/lib/terms';
import type {ReadResult, SavedAgreement, TermStatus} from '@/lib/types';
import StartCase from './start-case';
import {uploadPdf} from './documents';
import AgreementReview from './agreement-review';
import type {DocumentRecord} from '@/lib/recovery';

const statusLabel: Record<TermStatus, string> = {verified: 'Quote matched', absent: 'Not in the agreement', unverified: 'Quote not found', missing: 'Not extracted'};
const statusClass: Record<TermStatus, string> = {verified: 'green', absent: 'gray', unverified: 'amber', missing: 'amber'};
const modeLabel = {live: 'Live extraction', replay: 'Cached extraction', saved_run: 'Saved model run'};

export default function AgreementReader({onCaseStarted, documentId}: {onCaseStarted: (id: string) => void; documentId?:string|null}) {
  const [source, setSource] = useState<'url' | 'text' | 'pdf'>(documentId?'pdf':'url');
  const [documents,setDocuments]=useState<DocumentRecord[]>([]);
  const [pdfId,setPdfId]=useState(documentId||'');
  const [url, setUrl] = useState('');
  const [text, setText] = useState('');
  const [service, setService] = useState('');
  const [plan, setPlan] = useState('');
  const [saved, setSaved] = useState<SavedAgreement[]>([]);
  const [result, setResult] = useState<ReadResult | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState('');

  useEffect(() => { api<{agreements: SavedAgreement[]}>('/agreements/saved').then(r => setSaved(r.agreements)).catch(() => {}); }, []);
  useEffect(()=>{api<{documents:DocumentRecord[]}>('/documents').then(r=>setDocuments(r.documents.filter(d=>d.kind==='contract'))).catch(e=>setError(e.message));},[]);
  // ?read=<catalog id> opens a saved run directly (used for demos and screenshots).
  useEffect(() => { const id = new URLSearchParams(window.location.search).get('read'); if (id) void read({catalog_id: id}, id); }, []);

  async function read(body: object, label: string) {
    setBusy(label); setError('');
    try { setResult(await api<ReadResult>('/agreements/read', {method: 'POST', body: JSON.stringify(body)})); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(null); }
  }
  const ready = source === 'url' ? /^https:\/\/\S+$/.test(url.trim()) : source==='pdf'?!!pdfId:text.trim().length > 40;
  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (ready && service.trim() && plan.trim()) void read(source === 'url' ? {url: url.trim(), service, plan} : source==='pdf'?{document_id:pdfId,service,plan}:{text, service, plan}, 'form');
  }

  return <section className="reader" aria-labelledby="reader-title">
    <div className="reader-intro"><span className="insight-symbol"><Sparkles size={19}/></span><div><h2 id="reader-title">Read an agreement</h2><p>Claude extracts each term with a quote. Code checks every quote against the page and says whether the engine can price a credit under these terms.</p></div></div>
    <form className="reader-form" onSubmit={submit}>
      <div className="segmented" role="tablist" aria-label="Agreement source">{(['url', 'text','pdf'] as const).map(s => <button type="button" key={s} role="tab" aria-selected={source === s} className={source === s ? 'selected' : ''} onClick={() => setSource(s)}>{s === 'url' ? <><Link2 size={13}/>Link</> : <><FileText size={13}/>{s==='pdf'?'PDF document':'Paste text'}</>}</button>)}</div>
      {source === 'url'
        ? <label className="reader-field wide"><span>Public https link to the SLA</span><input type="url" inputMode="url" placeholder="https://www.atlassian.com/legal/sla" value={url} onChange={e => setUrl(e.target.value)}/></label>
        : source==='text'?<label className="reader-field wide"><span>Agreement text</span><textarea rows={5} placeholder="Paste the service level agreement…" value={text} onChange={e => setText(e.target.value)}/></label>:<div className="reader-field wide"><label htmlFor="contract-document">Saved contract PDF</label><select id="contract-document" value={pdfId} onChange={e=>setPdfId(e.target.value)}><option value="">Choose a PDF or upload below</option>{documents.map(d=><option key={d.id} value={d.id}>{d.filename}</option>)}</select><label className="pdf-upload-label">Upload a searchable PDF (up to 10 MB)<input type="file" accept=".pdf,application/pdf" disabled={!!busy} onChange={async e=>{const f=e.target.files?.[0];if(!f)return;setBusy('upload');setError('');try{const d=await uploadPdf(f,'contract');setDocuments(x=>[d,...x.filter(v=>v.id!==d.id)]);setPdfId(d.id);}catch(e){setError((e as Error).message);}finally{setBusy(null);}}}/></label></div>}
      <label className="reader-field"><span>Product</span><input placeholder="Jira" value={service} onChange={e => setService(e.target.value)}/></label>
      <label className="reader-field"><span>Plan</span><input placeholder="Premium" value={plan} onChange={e => setPlan(e.target.value)}/></label>
      <button className="button primary" type="submit" disabled={!!busy || !ready || !service.trim() || !plan.trim()}>{busy === 'form' ? <Loader2 className="spin" size={15}/> : <Sparkles size={15}/>}Read with Claude</button>
    </form>
    {saved.length > 0 && <div className="saved-runs"><span>Saved model runs, no key needed:</span>{saved.map(s => <button key={s.id} className="chip" disabled={!!busy} onClick={() => read({catalog_id: s.id}, s.id)}>{busy === s.id ? <Loader2 className="spin" size={12}/> : null}{s.title?.replace(/ service level agreement$/i, '') || s.id} · {s.selection.plan}</button>)}</div>}
    {error && <div className="notice reader-error"><Clock3 size={17}/><p>{error}</p></div>}
    <AgreementReview initialId={result?.read_id}/>
    {result && <ReaderResult key={result.read_id ?? result.source.sha256} result={result} onCaseStarted={onCaseStarted}/>}
  </section>;
}

function ReaderResult({result: r, onCaseStarted}: {result: ReadResult; onCaseStarted: (id: string) => void}) {
  const fields = Object.keys(termLabels);
  const verified = fields.filter(f => r.term_status[f] === 'verified').length;
  return <div className="reader-result">
    <div className="reader-result-head">
      <div><span className="overline">{r.selection.service} · {r.selection.plan}</span><h3>{r.source.title || r.source.url || 'Pasted agreement'}</h3>
        <p><span className="badge blue">{modeLabel[r.mode]}</span> {r.served_by || r.model}{r.extracted_at ? ` · ${date(r.extracted_at, {year: 'numeric'})}` : ''}{r.runs_saved ? ` · run 1 of ${r.runs_saved}` : ''} · {verified} of {fields.length} quotes matched {r.verified_now ? (r.verified_against === 'excerpts' ? 'just now, against the committed verbatim excerpts' : 'just now') : 'when the run was made'}</p></div>
      {r.source.url && <a className="text-button" href={r.source.url} target="_blank" rel="noreferrer">Open source <ExternalLink size={13}/></a>}
    </div>
    <div className={`readiness ${r.readiness.can_price ? 'positive' : 'review'}`}><strong>{r.readiness.can_price ? 'Refund Shark can price credits under these terms.' : 'Not yet priceable: the engine would send these claims to review.'}</strong>
      <ul>{r.readiness.checks.map(c => <li key={c.name} className={c.status}><span>{c.status === 'pass' ? <Check size={12}/> : <Clock3 size={12}/>}</span><b>{c.name}</b> {c.detail}</li>)}</ul></div>
    <StartCase read={r} onStarted={onCaseStarted}/>
    <div className="term-grid">{fields.map(f => { const t = r.terms[f]; const s = r.term_status[f] || 'missing'; return <article key={f} className="term-card">
      <div className="term-card-head"><strong>{termLabels[f]}</strong><span className={`badge ${statusClass[s]}`}>{s === 'verified' ? <ShieldCheck size={11}/> : s === 'absent' ? <CircleMinus size={11}/> : <Clock3 size={11}/>}{statusLabel[s]}</span></div>
      <p className="term-value">{s === 'absent' ? (t?.missing_reason || 'The agreement states no such term.') : summarizeTerm(f, t?.value)}</p>
      {t?.quote && <blockquote>{t.quote}</blockquote>}{t?.section && <small>{t.section}</small>}{r.page_citations?.[f]?.length ? <small className="page-citation">PDF page{r.page_citations[f].length>1?'s':''} {r.page_citations[f].join(', ')}</small>:null}
    </article>; })}</div>
  </div>;
}
