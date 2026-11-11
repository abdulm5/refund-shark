'use client';
import {useRef, useState} from 'react';
import {ArrowRight, FileUp, FlaskConical, Loader2, Plus} from 'lucide-react';
import {api} from '@/lib/api';
import type {ReadResult} from '@/lib/types';

type Answer = 'yes' | 'no' | 'unknown';
const answerValue = {yes: true, no: false, unknown: null} as const;
const words = (s: string) => s.replaceAll('_', ' ').replace(/^./, c => c.toUpperCase());
const previousMonth = () => { const d = new Date(); d.setUTCDate(1); d.setUTCMonth(d.getUTCMonth() - 1); return d.toISOString().slice(0, 7); };
const today = () => new Date().toISOString().slice(0, 10);

/** Start a stored case from an agreement read here: the form asks exactly what this agreement's own terms require. */
export default function StartCase({read: r, onStarted}: {read: ReadResult; onStarted: (id: string) => void}) {
  const prerequisites = r.case_prerequisites ?? [];
  const metric = r.terms.downtime_definition?.value as {kind?: string; covered_experiences?: string[]} | null;
  const exclusions = (r.terms.exclusions?.value as {machine_checkable?: boolean}[] | null) ?? [];
  // ?start=1 opens the form directly (used for demos and screenshots).
  const [open, setOpen] = useState(() => typeof window !== 'undefined' && new URLSearchParams(window.location.search).get('start') === '1');
  const [customer, setCustomer] = useState('');
  const [reference, setReference] = useState('');
  const [period, setPeriod] = useState(previousMonth);
  const [asOf, setAsOf] = useState(today);
  const [cycle, setCycle] = useState<'monthly' | 'annual'>('monthly');
  const [fees, setFees] = useState('');
  const [receiving, setReceiving] = useState('');
  const [answers, setAnswers] = useState<Record<string, Answer>>({});
  const [reviewed, setReviewed] = useState(false);
  const [log, setLog] = useState<{csv: string; name: string; demo: boolean} | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const input = useRef<HTMLInputElement>(null);

  if (!r.read_id) return r.case_unavailable_reason ? <p className="case-unavailable">{r.case_unavailable_reason}</p> : null;
  if (metric?.kind !== 'request_error_rate') return <p className="case-unavailable">Starting a case needs an agreement whose downtime is a per-minute request error rate, the log format this workspace measures. This one is measured as {words(metric?.kind || 'not extracted').toLowerCase()}.</p>;

  async function sample() {
    const response = await fetch('/api/logs/sample.csv');
    if (!response.ok) throw new Error('Couldn’t load the sample log.');
    return response.text();
  }
  async function pickSample() {
    try { setLog({csv: await sample(), name: 'sample-request-log-2026-09.csv (simulated)', demo: true}); setError(''); } catch (e) { setError((e as Error).message); }
  }
  async function fillDemo() {
    setCustomer('Acme Robotics (demo)'); setReference('DEMO-ACME-1'); setPeriod('2026-09'); setAsOf('2026-10-08'); setCycle('monthly');
    setFees('9000'); setReceiving('9000'); setAnswers(Object.fromEntries(prerequisites.map(p => [p, 'yes']))); setReviewed(true);
    await pickSample();
  }
  async function chooseFile(file: File) {
    if (file.size > 8_000_000) { setError('That file is larger than 8 MB.'); return; }
    setLog({csv: await file.text(), name: file.name, demo: false}); setError('');
  }
  const money = /^\d+(\.\d{1,2})?$/;
  const ready = customer.trim() && /^\d{4}-\d{2}$/.test(period) && /^\d{4}-\d{2}-\d{2}$/.test(asOf) && money.test(fees) && Number(fees) > 0
    && (!receiving || money.test(receiving)) && log;
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!ready || !log) return;
    setBusy(true); setError('');
    try {
      const body = {read_id: r.read_id, customer_name: customer.trim(), account_reference: reference.trim() || null, period, as_of: `${asOf}T12:00:00Z`,
        billing_cycle: cycle, fees, receiving_invoice_fees: receiving || null, exclusions_reviewed: reviewed, csv: log.csv, demo: log.demo,
        prerequisites: Object.fromEntries(prerequisites.map(p => [p, answerValue[answers[p] ?? 'unknown']]))};
      const created = await api<{id: string}>('/cases', {method: 'POST', body: JSON.stringify(body)});
      onStarted(created.id);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }

  if (!open) return <div className="start-case-cta"><div><strong>Have this vendor’s request logs?</strong><p>Start a case under these terms. The engine measures your log, prices the credit, and drafts the claim for approval.</p></div><button className="button primary small" onClick={() => setOpen(true)}><Plus size={14}/>Start a case</button></div>;
  return <form className="start-case" onSubmit={submit} aria-label="Start a case">
    <div className="start-case-head"><div><h3>Start a case under these terms</h3><p>Saved in this workspace. Values you enter are not checked against a billing system; submission stays simulated.</p></div><button type="button" className="text-button" onClick={fillDemo}><FlaskConical size={13}/>Fill demo values</button></div>
    <div className="case-grid">
      <label className="reader-field"><span>Customer</span><input value={customer} onChange={e => setCustomer(e.target.value)} placeholder="Acme Robotics"/></label>
      <label className="reader-field"><span>Account reference</span><input value={reference} onChange={e => setReference(e.target.value)} placeholder="Site or account ID"/></label>
      <label className="reader-field"><span>Billing period</span><input type="month" value={period} onChange={e => setPeriod(e.target.value)}/></label>
      <label className="reader-field"><span>Decide as of (UTC)</span><input type="date" value={asOf} onChange={e => setAsOf(e.target.value)}/></label>
      <label className="reader-field"><span>Billing</span><select value={cycle} onChange={e => setCycle(e.target.value as 'monthly' | 'annual')}><option value="monthly">Monthly</option><option value="annual">Annual</option></select></label>
      <label className="reader-field"><span>{cycle === 'annual' ? 'Annual' : 'Monthly'} fee for {r.selection.service} (USD)</span><input inputMode="decimal" value={fees} onChange={e => setFees(e.target.value)} placeholder="9000"/></label>
      <label className="reader-field"><span>Invoice the credit lands on (USD, optional)</span><input inputMode="decimal" value={receiving} onChange={e => setReceiving(e.target.value)} placeholder="For the credit cap"/></label>
    </div>
    {prerequisites.length > 0 && <fieldset className="case-prerequisites"><legend>What this agreement requires before a credit</legend>
      {r.terms.prerequisites?.quote && <blockquote>{r.terms.prerequisites.quote}</blockquote>}
      {prerequisites.map(p => <label key={p} className="prereq-row"><span>{words(p)}</span><select value={answers[p] ?? 'unknown'} onChange={e => setAnswers({...answers, [p]: e.target.value as Answer})}><option value="unknown">Not known yet</option><option value="yes">Yes</option><option value="no">No</option></select></label>)}
    </fieldset>}
    {exclusions.some(x => x.machine_checkable === false) && <label className="case-check"><input type="checkbox" checked={reviewed} onChange={e => setReviewed(e.target.checked)}/>I reviewed the exclusions code can’t check (for example customer equipment or third-party products)</label>}
    <div className="case-log"><span>Request log: per-minute CSV (minute, experience, requests, errors){metric.covered_experiences?.length ? `; covered: ${metric.covered_experiences.join(', ')}` : ''}</span>
      <div className="upload-actions"><button type="button" className="button secondary small" onClick={() => input.current?.click()}><FileUp size={14}/>Choose a CSV</button><button type="button" className="button secondary small" onClick={pickSample}><FlaskConical size={14}/>Use the sample log</button>{log && <span className="case-log-name">{log.name}</span>}</div>
      <input ref={input} type="file" accept=".csv,text/csv" hidden onChange={e => { const file = e.target.files?.[0]; if (file) void chooseFile(file); e.target.value = ''; }}/></div>
    {error && <div className="notice reader-error"><p>{error}</p></div>}
    <div className="start-case-actions"><button className="button primary" type="submit" disabled={busy || !ready}>{busy ? <Loader2 className="spin" size={15}/> : <ArrowRight size={15}/>}Measure and price</button><button type="button" className="text-button" onClick={() => setOpen(false)}>Cancel</button></div>
  </form>;
}
