'use client';
import type {Decision, Interval, Scenario} from '@/lib/types';

// Validated against the white surface (dataviz validator): counted vs provisional pass every check.
const COUNTED = '#2a7d4f', PROVISIONAL = '#c08a1e', CONTEXT = '#a1a898', CONTEXT_LIGHT = '#d3d7cc', TRACK = '#e8f0ea', SURFACE = '#ffffff';
const fmtMin = (m: number) => m >= 120 ? `${Math.floor(m / 60)} h ${Math.round(m % 60)} min` : Number.isInteger(m) ? `${m} min` : `${Math.floor(m)} min ${Math.round((m % 1) * 60)} s`;
const hhmm = (t: number) => new Date(t).toLocaleTimeString('en-US', {hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'UTC'});

/** Meter: counted Downtime Minutes against the allowance, with credit tiers as boundaries on the same track. */
export function DowntimeBudget({decision: d, scenario: s}: {decision: Decision; scenario: Scenario}) {
  const period = Number(d.period_minutes);
  const commitment = Number((s.terms.uptime_commitment_pct?.value as string | null) ?? NaN);
  if (!period || Number.isNaN(commitment)) return null;
  const allowance = (100 - commitment) / 100 * period;
  const counted = Number(d.estimates.find(e => e.counted)?.downtime_minutes ?? 0);
  const provisional = d.estimates.filter(e => !e.counted).map(e => ({label: e.label, minutes: Number(e.downtime_minutes), credit: e.credit_pct}));
  // A tier starts where uptime drops below its upper bound: downtime past (100 - upper)% of the period.
  const starts = ((s.terms.credit_tiers?.value as {upper: string; credit_pct: string}[] | null) ?? [])
    .map(t => ({minutes: (100 - Number(t.upper)) / 100 * period, credit: t.credit_pct})).sort((a, b) => a.minutes - b.minutes);
  const atAllowance = starts.find(t => Math.abs(t.minutes - allowance) < 1e-6);
  const peak = Math.max(counted, Number(d.excluded_minutes), ...provisional.map(p => p.minutes), allowance);
  const nextStart = starts.find(t => t.minutes >= peak * 1.1 && t.minutes > allowance);
  const max = (nextStart ? nextStart.minutes : Math.min(period, peak * 1.6)) * 1.18;
  const W = 820, x = (m: number) => 8 + Math.min(m, max) / max * (W - 16), barY = 50, barH = 14;
  const anchor = (px: number) => px < 150 ? 'start' : px > W - 150 ? 'end' : 'middle';
  const short = (label: string) => /postmortem/i.test(label) ? 'Postmortem window' : /status update/i.test(label) ? 'Status-update window' : /unverified/i.test(label) ? 'Unverified logs' : label.slice(0, 24);
  const bounds = [{minutes: allowance, label: `Allowance ${fmtMin(allowance)}` + (atAllowance ? ` · ${atAllowance.credit}% credit beyond` : '')},
    ...starts.filter(t => t !== atAllowance && t.minutes > allowance && t.minutes <= max).map(t => ({minutes: t.minutes, label: `${t.credit}% tier from ${fmtMin(t.minutes)}`}))];
  const over = counted > allowance;
  const summary = `${fmtMin(counted)} counted of ${fmtMin(allowance)} allowed at ${commitment}% uptime` + (provisional.length ? `; provisional windows ${provisional.map(p => fmtMin(p.minutes)).join(' and ')} are not counted` : '');
  return <section className="detail-section chart-card" aria-label="Downtime budget">
    <div className="detail-section-title"><h2>Downtime budget</h2><span className="outline-pill">{commitment}% uptime allows {fmtMin(allowance)} in {s.period}</span></div>
    <div className="budget-figure"><strong>{fmtMin(counted)}</strong><span>counted{over ? `, ${fmtMin(counted - allowance)} past the allowance` : counted ? ', within the allowance' : ''}</span></div>
    <svg viewBox={`0 0 ${W} 100`} width="100%" role="img" aria-label={summary}>
      <rect x={8} y={barY} width={W - 16} height={barH} rx={4} fill={TRACK}/>
      {counted > 0 && <rect x={8} y={barY} width={Math.max(4, x(counted) - 8)} height={barH} rx={4} fill={COUNTED}><title>{`Counted Downtime Minutes: ${fmtMin(counted)}`}</title></rect>}
      {bounds.map(b => <g key={b.label}>
        <line x1={x(b.minutes)} x2={x(b.minutes)} y1={barY - 5} y2={barY + barH + 5} stroke="#20221f" strokeWidth={1}/>
        <text x={x(b.minutes)} y={barY + barH + 19} fontSize={10} fill="#73766e" textAnchor={anchor(x(b.minutes))}>{b.label}</text></g>)}
      {provisional.map((p, i) => <g key={p.label}>
        <circle cx={x(p.minutes)} cy={barY + barH / 2} r={6} fill={PROVISIONAL} stroke={SURFACE} strokeWidth={2}><title>{`Provisional, not counted: ${p.label}, ${fmtMin(p.minutes)}${p.credit ? `, ${p.credit}% tier` : ', no tier'}`}</title></circle>
        <text x={x(p.minutes)} y={barY - 10 - (provisional.length - 1 - i) * 14} fontSize={10} fill="#20221f" textAnchor={anchor(x(p.minutes))}>{`${short(p.label)} · ${fmtMin(p.minutes)}`}</text></g>)}
      {Number(d.excluded_minutes) > 0 && <text x={8} y={barY - 10} fontSize={10} fill="#73766e">{`${fmtMin(Number(d.excluded_minutes))} logged inside confirmed maintenance: excluded`}</text>}
    </svg>
    <div className="chart-legend"><span><i style={{background: COUNTED}}/>Counted Downtime Minutes</span>{provisional.length > 0 && <span><i className="dot" style={{background: PROVISIONAL}}/>Provisional window, never counted</span>}<span><i className="rule"/>Allowance and credit tiers</span></div>
  </section>;
}

type Lane = {label: string; color: string; spans: {start: number; end: number; note: string}[]; empty?: string};

/** Timeline: the minutes that count against the vendor's own windows, on one shared clock. */
export function IncidentTimeline({scenario: s}: {scenario: Scenario}) {
  const t = (v: string) => new Date(v).getTime();
  const span = (i: Interval, note: string) => ({start: t(i.start), end: t(i.end), note});
  const maintenance = s.maintenance.filter(m => m.exclusion_confirmed).map(m => span(m.interval, `Confirmed maintenance ${m.reference ?? ''}`.trim()));
  const customer = s.incidents.filter(i => i.evidence_kind !== 'public_timeline');
  const pub = s.incidents.filter(i => i.evidence_kind === 'public_timeline');
  const counted: Lane['spans'] = [], excluded: Lane['spans'] = [];
  for (const record of customer) for (const iv of record.intervals) {
    let pieces = [{start: t(iv.start), end: t(iv.end)}];
    for (const m of maintenance) pieces = pieces.flatMap(p => p.end <= m.start || p.start >= m.end ? [p] : [
      ...(p.start < m.start ? [{start: p.start, end: m.start}] : []), ...(m.end < p.end ? [{start: m.end, end: p.end}] : [])]);
    pieces.forEach(p => counted.push({...p, note: 'Counted Downtime Minutes'}));
    for (const m of maintenance) { const a = Math.max(t(iv.start), m.start), b = Math.min(t(iv.end), m.end); if (a < b) excluded.push({start: a, end: b, note: 'Excluded: inside confirmed maintenance'}); }
  }
  const lanes: Lane[] = [
    {label: 'Customer Downtime Minutes', color: COUNTED, spans: counted, empty: customer.length ? 'None counted' : 'No customer logs supplied'},
    ...(excluded.length ? [{label: 'Excluded (maintenance)', color: CONTEXT, spans: excluded}] : []),
    ...pub.map(i => ({label: 'Vendor status updates', color: CONTEXT, spans: i.intervals.map(iv => span(iv, `${i.title} (${i.id})`)), empty: i.intervals.length ? undefined : `${i.severity.replaceAll('_', ' ')}: no outage window`})),
    ...pub.flatMap(i => i.context_windows.map(w => ({label: w.label.replace('Vendor postmortem impact window', 'Vendor postmortem window'), color: CONTEXT_LIGHT, spans: w.intervals.map(iv => span(iv, w.detail || w.label))}))),
    ...(maintenance.length ? [{label: 'Scheduled maintenance', color: CONTEXT_LIGHT, spans: maintenance}] : []),
  ];
  const all = lanes.flatMap(l => l.spans);
  if (!all.length) return null;
  const lo = Math.min(...all.map(a => a.start)), hi = Math.max(...all.map(a => a.end));
  if (hi - lo > 2 * 86_400_000) return null;  // windows days apart: the month strip would show slivers; the calculation carries it
  const pad = Math.max((hi - lo) * 0.08, 10 * 60_000), from = lo - pad, to = hi + pad;
  const W = 820, left = 170, laneH = 26, top = 8, H = top + lanes.length * laneH + 26;
  const x = (v: number) => left + (v - from) / (to - from) * (W - left - 8);
  const step = [15, 30, 60, 120, 240].map(m => m * 60_000).find(st => (to - from) / st <= 8) ?? 480 * 60_000;
  const ticks: number[] = []; for (let v = Math.ceil(from / step) * step; v <= to; v += step) ticks.push(v);
  return <section className="detail-section chart-card" aria-label="Incident timeline">
    <div className="detail-section-title"><h2>What counted, against the vendor’s windows</h2><span className="outline-pill">{new Date(lo).toLocaleDateString('en-US', {month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC'})} · UTC</span></div>
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={`Timeline from ${hhmm(from)} to ${hhmm(to)} UTC: ${lanes.map(l => `${l.label} ${l.spans.length ? l.spans.map(sp => `${hhmm(sp.start)}–${hhmm(sp.end)}`).join(', ') : l.empty ?? 'none'}`).join('; ')}`}>
      {ticks.map(v => <g key={v}><line x1={x(v)} x2={x(v)} y1={top} y2={H - 20} stroke="#eef0ea" strokeWidth={1}/><text x={x(v)} y={H - 6} fontSize={10} fill="#73766e" textAnchor="middle">{hhmm(v)}</text></g>)}
      {lanes.map((l, i) => { const y = top + i * laneH; return <g key={l.label + i}>
        <text x={0} y={y + laneH / 2 + 4} fontSize={10.5} fill="#20221f">{l.label}</text>
        {l.spans.length ? l.spans.map((sp, k) => <rect key={k} x={x(sp.start)} y={y + 6} width={Math.max(3, x(sp.end) - x(sp.start))} height={laneH - 12} rx={3} fill={l.color} stroke={SURFACE} strokeWidth={1}>
          <title>{`${l.label}: ${hhmm(sp.start)}–${hhmm(sp.end)} UTC (${Math.round((sp.end - sp.start) / 60_000)} min) · ${sp.note}`}</title></rect>)
          : <text x={left} y={y + laneH / 2 + 4} fontSize={10} fill="#95988f">{l.empty}</text>}
      </g>; })}
    </svg>
  </section>;
}
