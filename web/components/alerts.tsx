'use client';
import {useCallback, useEffect, useState} from 'react';
import {BellRing, Check, Copy, ExternalLink, Loader2, Radio, RefreshCw, Siren} from 'lucide-react';
import {api, stamp} from '@/lib/api';
import type {AlertsResponse, Match} from '@/lib/types';
import RampImport from './ramp-import';
import IncidentActionPanel from './incident-action';

const matchLabel: Record<Match['status'], string> = {affected: 'Affected', possibly_affected: 'Possibly affected', other_region: 'Other region', outside_covered: 'Not a covered feature', no_sla: 'No SLA on plan'};
const alertable = (m: Match) => m.status === 'affected' || m.status === 'possibly_affected';

/** Outages as the watcher saw them: who was alerted, who was not and why, and what to do next. */
export default function AlertsView({notify, onChange}: {notify: (s: string) => void; onChange: () => Promise<void>}) {
  const [data, setData] = useState<AlertsResponse | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [actionId,setActionId]=useState<number|null>(null);
  const load = useCallback(async () => { try { setData(await api<AlertsResponse>('/alerts')); setError(''); } catch (e) { setError((e as Error).message); } }, []);
  useEffect(() => { void load(); }, [load]);

  async function run(label: string, path: string, body?: object, done?: (r: {new_alerts: number; detected?: number; resolved?: number}) => string) {
    setBusy(label);
    try { const r = await api<{new_alerts: number; detected?: number; resolved?: number}>(path, {method: 'POST', body: body ? JSON.stringify(body) : undefined}); await load(); await onChange(); if (done) notify(done(r)); }
    catch (e) { notify((e as Error).message); } finally { setBusy(null); }
  }
  async function copy(id: number, text: string) {
    try { await navigator.clipboard.writeText(text); notify('Ticket text copied. Nothing was sent.'); } catch { notify('Clipboard unavailable. Select the ticket text to copy it.'); }
    await run('read', `/alerts/${id}/read`);
  }

  const watch = data?.watch, poll = watch?.last_poll;
  const reachable = poll?.feeds.filter(f => f.ok).length ?? 0;
  return <>
    <div className="page-heading"><div><div className="eyebrow">OUTAGES, AS THEY HAPPEN</div><h1>Alerts</h1><p>When a vendor goes down, the customers it could owe hear about it while a support ticket still counts.</p></div></div>
    <section className="watch-bar" aria-label="Status watcher">
      <Radio size={17}/>
      <div><strong>{watch && watch.interval_seconds > 0 ? `Watching ${watch.feeds.length} vendor status pages every ${Math.round(watch.interval_seconds / 60)} min` : 'Automatic checks are off (set SHARK_WATCH_INTERVAL)'}</strong>
        <span>{poll ? `Last checked ${stamp(poll.at)} · ${reachable} of ${poll.feeds.length} feeds answered` : 'Not checked yet'}</span></div>
      <button className="button secondary small" disabled={!!busy} onClick={() => run('poll', '/watch/poll', undefined, r => `Checked. ${r.detected ? `${r.detected} new incident${r.detected === 1 ? '' : 's'}, ` : 'No new incidents, '}${r.new_alerts} new alert${r.new_alerts === 1 ? '' : 's'}.`)}>{busy === 'poll' ? <Loader2 size={14} className="spin"/> : <RefreshCw size={14}/>}Check now</button>
      {watch?.drill_active
        ? <button className="button secondary small" disabled={!!busy} onClick={() => run('drill', '/watch/drill', {action: 'end'}, r => `Drill resolved: ${r.new_alerts} follow-up${r.new_alerts === 1 ? '' : 's'} with the filing deadline.`)}>{busy === 'drill' ? <Loader2 size={14} className="spin"/> : <Check size={14}/>}Resolve the drill</button>
        : <button className="button primary small" disabled={!!busy} onClick={() => run('drill', '/watch/drill', {action: 'start'}, r => `Simulated outage: ${r.new_alerts} customer${r.new_alerts === 1 ? '' : 's'} alerted.`)}>{busy === 'drill' ? <Loader2 size={14} className="spin"/> : <Siren size={14}/>}Run a simulated outage</button>}
    </section>
    {error && <p className="error-message">{error}</p>}
    {data && !data.incidents.length && <div className="empty-state bordered"><BellRing size={28}/><h3>No outages so far.</h3><p>The watcher checks Jira, GitHub, and Cloudflare. Run a simulated outage to see who gets alerted and why.</p></div>}
    {actionId!==null&&<IncidentActionPanel key={actionId} alertId={actionId} onClose={()=>setActionId(null)} notify={notify}/>}
    <div className="alert-incidents">{data?.incidents.map(incident => {
      const sent = data.alerts.filter(a => a.incident_key === incident.key);
      const skipped = incident.matches.filter(m => !alertable(m));
      return <article key={incident.key} className={`alert-incident ${incident.open ? 'open' : 'resolved'}`}>
        <div className="alert-incident-head"><div>
          <span className="overline">{incident.vendor} {incident.product}</span>
          <h2>{incident.simulated && <span className="badge amber">Simulated drill</span>} {incident.name}</h2>
          <p>{incident.open ? 'Open' : `Resolved ${stamp(incident.resolved_at)}`} · started {stamp(incident.started_at || incident.first_seen)}{incident.shortlink && <> · <a className="text-button" href={incident.shortlink} target="_blank" rel="noreferrer">Status page <ExternalLink size={11}/></a></>}</p></div>
          <span className={`badge ${incident.open ? 'amber' : 'gray'}`}>{incident.open ? 'Open now' : 'Resolved'}</span></div>
        <div className="alert-list">{sent.map(a => <div key={a.id} className={`alert-row ${a.read ? 'read' : ''}`}>
          <div className="alert-row-head">{!a.read && <span className="unread-dot" aria-label="Unread"/>}<strong>{a.customer}</strong><span className={`badge ${a.match_status === 'affected' ? 'green' : 'amber'}`}>{matchLabel[a.match_status as Match['status']] ?? a.match_status}</span><span className="alert-time">{stamp(a.created_at)}</span></div>
          <p className="alert-title">{a.title}</p><p className="alert-detail">{a.detail}</p>
          <button className="button secondary small" onClick={()=>setActionId(a.id)}>Open action panel</button>
          {a.ticket && <div className="ticket"><div><span>Support ticket, ready to paste</span><button className="text-button" onClick={() => copy(a.id, a.ticket!)}><Copy size={12}/>Copy ticket</button></div><pre>{a.ticket}</pre></div>}
          {!a.read && !a.ticket && <button className="text-button" onClick={() => run('read', `/alerts/${a.id}/read`)}><Check size={12}/>Mark done</button>}
        </div>)}</div>
        {skipped.length > 0 && <div className="not-alerted"><span>Not alerted</span><ul>{skipped.map(m => <li key={m.customer_id}><b>{m.customer}</b> · {matchLabel[m.status]}: {m.reason}</li>)}</ul></div>}
      </article>;
    })}</div>
    <RampImport notify={notify} onImported={async () => { await load(); await onChange(); }}/>
    <div className="replay-note">Bundled customers, the sample Ramp business, and drills are simulated. Status pages are real and public. Nothing is sent to a vendor or a customer: alerts stay in this workspace.</div>
  </>;
}
