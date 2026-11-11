export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, { ...init, headers: {'Content-Type':'application/json', ...init?.headers}, cache:'no-store' });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === 'string' ? body.detail : `Request failed (${response.status}). Try again.`);
  }
  return response.json();
}
export const money = (value: string | number | null) => new Intl.NumberFormat('en-US', {style:'currency',currency:'USD',maximumFractionDigits:2,minimumFractionDigits:2}).format(Number(value || 0));
export const date = (value:string|null, options?:Intl.DateTimeFormatOptions) => value ? new Date(value).toLocaleDateString('en-US', {month:'short',day:'numeric',timeZone:'UTC',...options}) : 'Needs review';
export const month = (period: string) => new Date(`${period}-01T00:00:00Z`).toLocaleDateString('en-US', {month:'long', year:'numeric', timeZone:'UTC'});
export const stamp = (value: string | null) => value ? `${new Date(value).toLocaleString('en-US', {month:'short', day:'numeric', year:'numeric', hour:'2-digit', minute:'2-digit', hour12:false, timeZone:'UTC'})} UTC` : 'Needs review';
export const daysBetween = (from: string, to: string) => Math.floor((new Date(to).getTime() - new Date(from).getTime()) / 86_400_000);
export const outcomeLabel = {eligible:'Ready to claim', needs_review:'Needs review', not_eligible:'Not eligible'} as const;
