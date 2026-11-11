import {Check, Clock3, CircleMinus, FileText, Send, ShieldCheck, X} from 'lucide-react';
import type {ClaimStatus, Outcome} from '@/lib/types';
const config = {
  eligible:{label:'Ready to claim',className:'green',icon:Check}, needs_review:{label:'Needs review',className:'amber',icon:Clock3}, not_eligible:{label:'Not eligible',className:'gray',icon:CircleMinus},
  draft:{label:'Draft',className:'blue',icon:FileText}, approved:{label:'Approved',className:'green',icon:ShieldCheck}, sent:{label:'Sent · demo',className:'blue',icon:Send}, short_paid:{label:'Short-paid · demo',className:'amber',icon:Clock3}, disputed:{label:'Revisit requested · demo',className:'blue',icon:Send}, credited:{label:'Credit received · demo',className:'green',icon:Check}, rejected:{label:'Rejected',className:'gray',icon:X}, invalidated:{label:'Needs new review',className:'amber',icon:Clock3}
};
export function Badge({status}: {status:Outcome|ClaimStatus}) {const c=config[status]; const Icon=c.icon;return <span className={`badge ${c.className}`}><Icon size={12}/>{c.label}</span>;}
