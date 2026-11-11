'use client';
import {useEffect, useState} from 'react';
import {Copy, ExternalLink, Radio} from 'lucide-react';
import {api, stamp} from '@/lib/api';
import type {LiveStatus} from '@/lib/types';

function useLiveStatus(preview = false) {
  const [status, setStatus] = useState<LiveStatus | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => { api<LiveStatus>(`/status/live${preview ? '?preview=true' : ''}`).then(setStatus).catch(() => setFailed(true)); }, [preview]);
  return {status, failed};
}

/** Incident feed panel: every vendor's live feed, with paste-ready tickets for open incidents. */
export function LiveStatusPanel({notify}: {notify: (s: string) => void}) {
  const [preview, setPreview] = useState(false);
  const {status, failed} = useLiveStatus(preview);
  async function copy(text: string) {
    try { await navigator.clipboard.writeText(text); notify('Ticket text copied. Nothing was sent.'); } catch { notify('Clipboard unavailable. Select the ticket text to copy it.'); }
  }
  return <section className="live-panel" aria-labelledby="live-title">
    <div className="section-heading"><h2 id="live-title"><Radio size={15}/> Live status</h2><span className="last-checked">{status ? `Checked ${stamp(status.checked_at)} · public status pages` : failed ? 'Unavailable' : 'Checking…'}</span></div>
    {failed && <p className="muted">The live status check is unavailable. Saved incidents are below.</p>}
    {status && !status.preview && status.open_incidents === 0 && <button className="text-button preview-toggle" onClick={() => setPreview(true)}>Preview what an open incident looks like (simulated)</button>}
    {status?.preview && <p className="preview-note">Showing a simulated incident added for preview. <button className="text-button" onClick={() => setPreview(false)}>Hide preview</button></p>}
    <div className="live-feeds">{status?.feeds.map(f => <article key={f.host} className={`live-feed ${!f.ok ? 'unknown' : f.incidents.length ? 'open' : 'clear'}`}>
      <div className="live-feed-head"><span className="live-dot"/><strong>{f.vendor} {f.product}</strong><span>{f.message}</span><a className="text-button" href={f.status_url} target="_blank" rel="noreferrer">Status page <ExternalLink size={12}/></a></div>
      {f.incidents.map(i => <div key={i.id} className="live-incident"><p>{i.simulated && <span className="badge amber">Simulated</span>} <b>{i.name}</b> · {i.status} · since {stamp(i.started_at)}{i.components.length ? ` · ${i.components.join(', ')}` : ''}</p>
        {i.action ? <><blockquote>“{i.action.requirement_quote}” <small>{i.action.source_label}</small></blockquote>
          {i.action.tickets.map(t => <div key={t.scenario_id} className="ticket"><div><strong>{t.customer}</strong><button className="text-button" onClick={() => copy(t.text)}><Copy size={12}/>Copy ticket</button></div><pre>{t.text}</pre></div>)}</>
          : <p className="muted">{i.action_note}</p>}
      </div>)}
    </article>)}</div>
  </section>;
}
