'use client';
import {useEffect, useRef, useState} from 'react';
import {Download, FileUp, FlaskConical, Loader2} from 'lucide-react';
import {api, money} from '@/lib/api';
import type {Decision, Scenario} from '@/lib/types';
import {DowntimeBudget} from './charts';

type WhatIf = {what_if: true; note: string; decision: Decision;
  analysis: {rows: number; minutes_covered: number; period_minutes: number; downtime_minutes: number; zero_request_minutes: number;
    below_threshold_error_minutes: number; excluded_error_minutes: Record<string, number>; covered_experiences: string[]; threshold_pct: string}};

const wouldBe = {eligible: 'Would be ready to claim', needs_review: 'Would still need review', not_eligible: 'Would not qualify'} as const;

/** Bring your own logs: the engine prices an uploaded per-minute log as a what-if, and nothing is stored. */
export default function LogUpload({scenario}: {scenario: Scenario}) {
  const [result, setResult] = useState<WhatIf | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [name, setName] = useState('');
  const input = useRef<HTMLInputElement>(null);

  async function analyze(csv: string, label: string) {
    setBusy(true); setError(''); setName(label);
    try { setResult(await api<WhatIf>(`/opportunities/${scenario.id}/logs/analyze`, {method: 'POST', body: JSON.stringify({csv})})); }
    catch (e) { setError((e as Error).message); setResult(null); }
    finally { setBusy(false); }
  }
  async function analyzeFile(file: File) {
    if (file.size > 8_000_000) { setError('That file is larger than 8 MB.'); return; }
    await analyze(await file.text(), file.name);
  }
  async function analyzeSample() {
    setBusy(true);
    try { const response = await fetch('/api/logs/sample.csv'); await analyze(await response.text(), 'sample-request-log-2026-09.csv (simulated)'); }
    catch (e) { setError((e as Error).message); setBusy(false); }
  }

  // ?whatif=sample runs the sample upload on load (used for demos and screenshots).
  useEffect(() => { if (new URLSearchParams(window.location.search).get('whatif') === 'sample') void analyzeSample(); }, []);  // eslint-disable-line react-hooks/exhaustive-deps
  const d = result?.decision;
  const replacesStored = scenario.incidents.some(i => i.evidence_kind === 'customer_metrics');
  const counted = d?.estimates.find(e => e.counted);
  const open = d?.checks.filter(c => c.status !== 'pass') ?? [];
  return <section className="detail-section upload-card" aria-label="Measure your own logs">
    <div className="detail-section-title"><h2>Measure your own logs</h2><span className="outline-pill">What-if · nothing is saved</span></div>
    <p className="upload-intro">Upload a per-minute request log (CSV columns: minute, experience, requests, errors). The same engine counts Downtime Minutes under this agreement and prices the result. The stored case and any approval stay as they are.</p>
    <div className="upload-actions">
      <button className="button secondary small" onClick={() => input.current?.click()} disabled={busy}><FileUp size={14}/>Choose a CSV</button>
      <button className="button secondary small" onClick={analyzeSample} disabled={busy}>{busy ? <Loader2 size={14} className="spin"/> : <FlaskConical size={14}/>}Try the sample log</button>
      <a className="text-button" href="/api/logs/sample.csv" download><Download size={13}/>Download the sample</a>
      <input ref={input} type="file" accept=".csv,text/csv" hidden onChange={e => { const file = e.target.files?.[0]; if (file) void analyzeFile(file); e.target.value = ''; }}/>
    </div>
    {error && <div className="notice reader-error"><p>{error}</p></div>}
    {result && d && <div className="upload-result">
      <p className="upload-file">{name} · {result.analysis.rows.toLocaleString('en-US')} rows · {result.analysis.minutes_covered.toLocaleString('en-US')} of {result.analysis.period_minutes.toLocaleString('en-US')} minutes covered{replacesStored ? ' · replaces the stored customer logs in this what-if' : ''}</p>
      <div className="upload-headline"><span className={`badge ${d.outcome === 'eligible' ? 'green' : d.outcome === 'needs_review' ? 'amber' : 'gray'}`}>{wouldBe[d.outcome]}</span><strong>{result.analysis.downtime_minutes} Downtime Minutes</strong>
        <span>{!d.estimates.length ? `Measured, but this agreement cannot be priced yet: ${d.reason}` : counted?.credit_pct ? `${counted.credit_pct}% tier · ${money(counted.amount)} ${d.outcome === 'eligible' ? 'would be supported' : 'once the items below are resolved'}` : 'No credit tier reached'}</span></div>
      <ul className="upload-facts">
        <li>A minute counts when more than {Number(result.analysis.threshold_pct)}% of requests to {result.analysis.covered_experiences.join(', ')} fail</li>
        <li>{result.analysis.below_threshold_error_minutes} minutes with errors at or under that line do not count</li>
        {Object.entries(result.analysis.excluded_error_minutes).map(([experience, count]) => <li key={experience}>{count} error minutes on {experience} are excluded: not a covered experience</li>)}
        <li>{result.analysis.zero_request_minutes} minutes with no requests count as 0%</li>
      </ul>
      {d.estimates.length > 0 && <DowntimeBudget decision={d} scenario={scenario}/>}
      {open.length > 0 && <div className="not-alerted"><span>{d.outcome === 'eligible' ? 'Notes' : 'Still needed'}</span><ul>{open.map((c, i) => <li key={i}><b>{c.name}</b> · {c.detail}</li>)}</ul></div>}
      <p className="formula-note">{result.note}</p>
    </div>}
  </section>;
}
