'use client';
import {useCallback, useEffect, useState} from 'react';
import {Download, Loader2, Trash2, Wallet} from 'lucide-react';
import {api} from '@/lib/api';
import type {Customer} from '@/lib/types';

type Imported = {customers: Customer[]; ramp_configured: boolean; scopes: string};
const cycle = {annual: 'billed annually', monthly: 'billed monthly', unknown: 'billing cycle unknown'} as const;

/** Customers found in Ramp spend: which watched vendors a business pays. Spend never shows the plan, so it stays unknown. */
export default function RampImport({notify, onImported}: {notify: (s: string) => void; onImported: () => Promise<void>}) {
  const [data, setData] = useState<Imported | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const load = useCallback(async () => { try { setData(await api<Imported>('/spend/customers')); } catch { setData(null); } }, []);
  useEffect(() => { void load(); }, [load]);

  async function run(label: string, action: () => Promise<string>) {
    setBusy(label);
    try { const message = await action(); await load(); await onImported(); notify(message); } catch (e) { notify((e as Error).message); } finally { setBusy(null); }
  }
  const importFrom = (source: 'sample' | 'api') => run(source, async () => {
    const r = await api<{customer: Customer; report: {watched: string[]; not_watched: string[]}; new_alerts: number}>('/spend/ramp/import', {method: 'POST', body: JSON.stringify({source})});
    return `Imported ${r.customer.display_name}: ${r.report.watched.join(', ') || 'no watched vendors'}.${r.new_alerts ? ` ${r.new_alerts} alert${r.new_alerts === 1 ? '' : 's'} for open incidents.` : ''}`;
  });
  const remove = (c: Customer) => run(c.customer_id, async () => { await api(`/spend/customers/${c.customer_id}`, {method: 'DELETE'}); return `Removed ${c.display_name}. Past alerts stay in the history.`; });

  return <section className="ramp-import" aria-labelledby="ramp-import-title">
    <div className="ramp-import-head"><span className="insight-symbol"><Wallet size={18}/></span><div><h2 id="ramp-import-title">Customers from Ramp spend</h2>
      <p>Spend shows which watched vendors a business pays, not the plan. So when one of those vendors has an outage, the business is marked possibly affected and gets a ticket to open while it still counts. Only fields documented in Ramp’s public API reference are read; memos and amounts are not.</p></div></div>
    <div className="upload-actions">
      <button className="button secondary small" disabled={!!busy} onClick={() => importFrom('sample')}>{busy === 'sample' ? <Loader2 size={14} className="spin"/> : <Download size={14}/>}Import the sample Ramp business</button>
      <button className="button secondary small" disabled={!!busy || !data?.ramp_configured} title={data?.ramp_configured ? undefined : `Set RAMP_ACCESS_TOKEN on the server (scopes: ${data?.scopes ?? ''})`} onClick={() => importFrom('api')}>{busy === 'api' ? <Loader2 size={14} className="spin"/> : <Wallet size={14}/>}Import from Ramp</button>
      {!data?.ramp_configured && <span className="muted small-note">Live import needs RAMP_ACCESS_TOKEN on the server.</span>}
    </div>
    {data && data.customers.length > 0 && <ul className="ramp-customers">{data.customers.map(c => <li key={c.customer_id}>
      <div className="ramp-customer-head"><strong>{c.display_name}</strong><span className={`badge ${c.simulated ? 'amber' : 'blue'}`}>{c.simulated ? 'Simulated sample' : 'From Ramp'}</span>
        <button className="text-button" disabled={!!busy} onClick={() => remove(c)}><Trash2 size={12}/>Remove</button></div>
      <ul>{c.subscriptions.map(s => <li key={s.vendor}><b>{s.vendor}</b> · {s.product ?? 'product unknown'} · plan unknown · {cycle[s.billing_cycle ?? 'unknown']}<small>{s.evidence}</small></li>)}</ul>
    </li>)}</ul>}
  </section>;
}
