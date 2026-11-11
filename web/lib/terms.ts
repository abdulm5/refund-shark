export const termLabels: Record<string,string> = {vendor:'Vendor',covered_services:'Covered products',plan:'Subscription plan',uptime_commitment_pct:'Uptime commitment',measurement_period:'Measurement period',timezone:'Time zone',downtime_definition:'Downtime definition',credit_tiers:'Credit table',credit_basis:'Fee basis',credit_cap:'Credit cap',min_credit:'Minimum credit',exclusions:'Exclusions',prerequisites:'Claim requirements',claim_window:'Claim deadline',claim_procedure:'Submission procedure',remedy_form:'Remedy'};

type Obj = Record<string, unknown>;
const words = (v: unknown) => String(v).replaceAll('_', ' ');

/** One readable line per extracted value; the quote carries the detail. */
export function summarizeTerm(field: string, value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (field === 'credit_tiers' && Array.isArray(value)) return (value as Obj[]).map(t => `${t.lower_inclusive === false ? '>' : '≥'} ${t.lower}% and ${t.upper_inclusive ? '≤' : '<'} ${t.upper}% → ${t.credit_pct}%`).join(' · ');
  if (field === 'downtime_definition') { const m = value as Obj; return `${words(m.kind)}${m.error_threshold_pct ? ` above ${m.error_threshold_pct}%` : ''} per ${m.interval_seconds}s${Array.isArray(m.covered_experiences) && m.covered_experiences.length ? ` · ${m.covered_experiences.join(', ')}` : ''}`; }
  if (field === 'credit_cap') { const c = value as Obj; return `${c.pct}% of ${words(c.basis)}`; }
  if (field === 'min_credit') { const m = value as Obj; return `${m.inclusive ? 'At least' : 'More than'} $${m.amount}`; }
  if (field === 'claim_window') { const w = value as Obj; return `${w.value} ${words(w.kind)} · ${w.timezone || 'no time zone stated'}`; }
  if (field === 'claim_procedure') { const p = value as Obj; return String(p.channel); }
  if (field === 'exclusions' && Array.isArray(value)) return (value as Obj[]).map(e => words(e.kind)).join(', ');
  if (Array.isArray(value)) return value.map(words).join(', ');
  if (typeof value === 'object') return JSON.stringify(value);
  return field === 'uptime_commitment_pct' ? `${value}%` : words(value);
}
