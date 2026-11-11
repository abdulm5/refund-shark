import hashlib
import json
import threading
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from shark.claims.drafter import dispute_draft, draft
from shark.engine.disputes import revisit_window
from shark.extract.citations import normalize, quote_found
from shark.claims.matching import matches_credit
from shark.engine import evaluate
from shark.engine.metrics import apply_logs
from shark.models import Claim, Decision, Scenario
from shark.storage import Store, utcnow

ROOT = Path(__file__).resolve().parents[1]
# What each review check is waiting for, in the words a finance operator would use.
MISSING = {"Customer evidence": "customer request logs", "Covered experience": "covered-experience evidence", "Period coverage": "full-month coverage",
           "Exclusions": "exclusions review", "Support ticket during incident": "support ticket", "Production instance": "production-instance proof",
           "Account fully paid": "proof the account is paid up", "Credit cap": "receiving invoice", "Complete period": "the month to end",
           "Claim deadline": "a deadline time zone", "Measurement evidence": "a metric the engine can price", "Maintenance": "maintenance confirmation",
           "Credit table": "a credit table check"}


def load_scenarios() -> list[Scenario]:
    # Logged customer metrics are recomputed from their pinned raw logs on every load.
    return [apply_logs(Scenario.model_validate(s)) for s in json.loads((ROOT / "data/fixtures/scenarios.json").read_text())]


def revision(s: Scenario, d: Decision):
    return hashlib.sha256((s.model_dump_json() + d.model_dump_json()).encode()).hexdigest()


class Pipeline:
    def __init__(self, store: Store, scenarios: list[Scenario] | None = None):
        self.store = store
        self.scenarios = {s.id: s for s in (scenarios if scenarios is not None else load_scenarios())}
        self.lock = threading.RLock()
        for payload in store.user_cases():
            try:
                s = apply_logs(Scenario.model_validate_json(payload))
            except ValueError as error:  # a stored case that no longer validates is reported, not fatal
                store.event("review", "Saved case not loaded", str(error)[:200])
                continue
            self.scenarios[s.id] = s

    def add_user_case(self, s: Scenario) -> Scenario:
        s = apply_logs(s)
        with self.lock:
            from shark.monthly import scenario_identity, SUBMITTED
            for other in self.scenarios.values():
                if other.id != s.id and other.role == "user_case" and s.role == "user_case" and scenario_identity(other) == scenario_identity(s):
                    raise ValueError(f"Monthly case {other.id} already covers this company, account, product, and month. Open it to update the evidence.")
            claim = self.store.get_claim(s.id)
            if claim and claim["status"] in SUBMITTED:
                raise ValueError("A submitted monthly case is locked. Follow up on the existing claim.")
            self.store.save_user_case(s.id, s.model_dump_json())
            self.scenarios[s.id] = s
            self.store.event("case", "Case started", f"{s.account.display_name}: {s.product} {s.plan}, {s.period}.")
        return s

    def delete_user_case(self, case_id: str) -> bool:
        with self.lock:
            s = self.scenarios.get(case_id)
            if s is None or s.role != "user_case":
                return False
            from shark.monthly import SUBMITTED
            if (self.store.get_claim(case_id) or {}).get("status") in SUBMITTED:
                raise ValueError("A submitted case cannot be deleted; retain its claim history.")
            self.store.delete_user_case(case_id)
            del self.scenarios[case_id]
            self.store.event("case", "Case deleted", f"{s.account.display_name}: {s.product} {s.plan}, {s.period}.")
        return True

    def run(self, scenario_id: str, as_of: datetime | None = None):
        s = self.scenarios[scenario_id]
        d = evaluate(s, as_of)
        key = revision(s, d)
        with self.lock, self.store.connect() as db:
            payload = json.dumps({"decision": d.model_dump(mode="json"), "stages": ["source_loaded", "citations_checked", "rules_evaluated"], "source_hash": s.source_hash})
            db.execute("INSERT OR IGNORE INTO runs VALUES(?,?,?,?)", (key, scenario_id, payload, utcnow()))
            row = db.execute("SELECT payload FROM claims WHERE scenario_id=?", (scenario_id,)).fetchone()
            if row:
                claim = Claim.model_validate_json(row[0])
                if claim.decision_hash != key and claim.status not in {"invalidated", "rejected"}:
                    claim.status = "invalidated"
                    claim.updated_at = datetime.fromisoformat(utcnow())
                    claim.events.append({"action": "invalidated", "at": utcnow()})
                    db.execute("UPDATE claims SET payload=? WHERE scenario_id=?", (claim.model_dump_json(), scenario_id))
                    self.store.event("review", "Approval invalidated", f"Evidence changed for {s.product}.", db)
        return d, key

    def current(self, s: Scenario) -> dict:
        """Read-only evaluation at the real clock. It never touches stored runs or claims."""
        d = evaluate(s, datetime.now(timezone.utc))
        return {"outcome": d.outcome, "reason": d.reason, "as_of": d.evaluated_at.isoformat()}

    def detail(self, scenario_id):
        s = self.scenarios[scenario_id]
        d, key = self.run(scenario_id)
        return {"scenario": s.model_dump(mode="json"), "decision": d.model_dump(mode="json"), "revision": key,
                "claim": self.store.get_claim(scenario_id), "current": self.current(s)}

    def list(self):
        result = []
        for s in list(self.scenarios.values()):  # cases can be added or deleted concurrently
            d, key = self.run(s.id)
            result.append({"id": s.id, "vendor": s.vendor, "product": s.product, "plan": s.plan, "color": s.color, "initials": s.initials,
                           "summary": s.summary, "period": s.period, "simulated": s.simulated, "customer": s.account.display_name, "role": s.role,
                           "as_of": s.as_of.isoformat(), "decision": d.model_dump(mode="json"), "revision": key,
                           "claim": self.store.get_claim(s.id), "current": self.current(s)})
        return result

    def reminders(self):
        """Open claim windows, soonest first: what to file, and what evidence to collect before the deadline."""
        result = []
        for s in list(self.scenarios.values()):  # cases can be added or deleted concurrently
            d, _ = self.run(s.id)
            claim = self.store.get_claim(s.id)
            if d.deadline is None or d.outcome == "not_eligible" or (claim and claim["status"] in {"sent", "credited", "rejected"}):
                continue
            filing = d.outcome == "eligible"
            amount = d.amount if filing else d.estimated_amount
            missing = [] if filing else list(dict.fromkeys(MISSING.get(c.name, c.name.lower()) for c in d.checks if c.status == "review"))
            result.append({"id": s.id, "customer": s.account.display_name, "vendor": s.vendor, "product": s.product, "plan": s.plan, "period": s.period,
                           "kind": "file" if filing else "collect", "deadline": d.deadline.isoformat(),
                           "deadline_latest": d.deadline_latest.isoformat() if d.deadline_latest else None, "as_of": s.as_of.isoformat(),
                           "days_left": (d.deadline - s.as_of).days, "amount": None if amount is None else str(amount),
                           "amount_kind": "supported" if filing else ("provisional" if amount is not None else None), "missing": missing,
                           "claim_status": claim["status"] if claim else None})
        return sorted(result, key=lambda r: (r["deadline"], r["kind"] != "file"))

    def impact(self):
        """Group customers by the public incident behind their case: one outage, every account it touches."""
        groups: dict[str, dict] = {}
        for s in list(self.scenarios.values()):  # cases can be added or deleted concurrently
            d, _ = self.run(s.id)
            for incident in s.incidents:
                if incident.evidence_kind != "public_timeline" or incident.service != s.account.service:
                    continue
                spans = incident.intervals + [span for w in incident.context_windows for span in w.intervals]
                group = groups.setdefault(incident.id, {
                    "incident_id": incident.id, "title": incident.title, "vendor": s.vendor, "product": s.product, "severity": incident.severity,
                    "real": not incident.simulated, "source_url": incident.source_url,
                    "started_at": (incident.started_at or min((span.start for span in spans), default=None)),
                    "customers": [], "supported": Decimal(0), "provisional": Decimal(0)})
                group["customers"].append({"scenario_id": s.id, "customer": s.account.display_name, "period": s.period, "outcome": d.outcome,
                                           "amount": str(d.amount), "estimated_amount": None if d.estimated_amount is None else str(d.estimated_amount)})
                if d.outcome == "eligible":
                    group["supported"] += d.amount
                elif d.outcome == "needs_review" and d.estimated_amount is not None:
                    group["provisional"] += d.estimated_amount
        result = []
        for group in groups.values():
            supported, provisional = group.pop("supported"), group.pop("provisional")
            started = group.pop("started_at")
            result.append({**group, "started_at": started.isoformat() if started else None,
                           "supported_total": f"{supported:.2f}", "provisional_total": f"{provisional:.2f}"})
        return sorted(result, key=lambda g: g["started_at"] or "")

    def check_monthly_claim(self, scenario_id):
        from shark.monthly import scenario_identity
        s = self.scenarios[scenario_id]
        if s.role != "user_case":
            return
        for other in self.scenarios.values():
            if other.id == s.id or other.role != "user_case" or scenario_identity(other) != scenario_identity(s):
                continue
            claim = self.store.get_claim(other.id)
            if claim and claim["status"] not in {"rejected", "invalidated"}:
                raise ValueError(f"Claim {claim['id']} already covers this account and month. Open that claim instead.")

    def create_claim(self, scenario_id, tone="polite"):
        with self.lock:
            self.check_monthly_claim(scenario_id)
            d, key = self.run(scenario_id)
            s = self.scenarios[scenario_id]
            old = self.store.get_claim(scenario_id)
            if old and old["decision_hash"] == key and old["status"] not in {"rejected", "invalidated"}:
                if old["tone"] == tone:
                    return old
                if old["status"] != "draft":
                    raise ValueError("An approved claim cannot be rewritten")
            subject, body = draft(s, d, tone)
            # A redraft (new tone, or after rejection or invalidation) keeps the claim's history.
            events = [*old["events"], {"action": "redrafted", "at": utcnow(), "tone": tone}] if old else [{"action": "drafted", "at": utcnow(), "tone": tone}]
            claim = Claim(id=f"RS-{scenario_id.upper()}", scenario_id=scenario_id, decision_hash=key, status="draft", subject=subject, body=body,
                          tone=tone, amount=d.amount, currency=d.currency, created_at=old["created_at"] if old else utcnow(), updated_at=utcnow(), events=events)
            with self.store.connect() as db:
                db.execute("INSERT OR REPLACE INTO claims VALUES(?,?)", (scenario_id, claim.model_dump_json()))
                self.store.event("draft", f"Draft prepared for {s.product}", f"{d.currency} {d.amount} · demo claim", db)
            return claim.model_dump(mode="json")

    def transition(self, scenario_id, action: str, expected_revision: str, amount=None, now: datetime | None = None):
        """Move a claim one step. Awards, revisit requests, and credits are simulated."""
        allowed = {"approve": ({"draft"}, "approved"), "reject": ({"draft"}, "rejected"), "mark_sent": ({"approved"}, "sent"),
                   "record_award": ({"sent"}, None), "send_dispute": ({"short_paid"}, "disputed"), "accept_award": ({"short_paid"}, "credited"),
                   "match_credit": ({"sent", "disputed"}, "credited")}
        if action not in allowed:
            raise ValueError("Unknown claim action")
        now = now or datetime.now(timezone.utc)
        with self.lock:
            if action in {"approve", "mark_sent"}:
                self.check_monthly_claim(scenario_id)
            d, key = self.run(scenario_id)
            if key != expected_revision:
                raise ValueError("The decision changed. Reload and review the latest evidence.")
            s = self.scenarios[scenario_id]
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT payload FROM claims WHERE scenario_id=?", (scenario_id,)).fetchone()
                if not row:
                    raise ValueError("Create a draft first")
                claim = Claim.model_validate_json(row[0])
                before, after = allowed[action]
                if claim.decision_hash != key or d.outcome != "eligible":
                    raise ValueError("This claim is no longer supported by the current evidence")
                if claim.status not in before:
                    raise ValueError(f"Cannot {action.replace('_', ' ')} a {claim.status.replace('_', ' ')} claim")
                stamp = now.isoformat()
                if action == "record_award":
                    if amount is None:
                        raise ValueError("Record the awarded amount")
                    award = Decimal(str(amount)).quantize(Decimal("0.01"))
                    claim.award_amount = award
                    claim.events.append({"action": "award_recorded", "at": stamp, "amount": str(award)})
                    if award >= claim.amount:
                        after = "credited"
                    else:
                        after = "short_paid"
                        rule = s.dispute_rule
                        quoted = rule is not None and rule.value is not None and quote_found(rule.quote, normalize(s.source_text))
                        if quoted:
                            earliest, utc_reading, latest = revisit_window(now, rule.value.business_days)
                            claim.dispute_deadline, claim.dispute_deadline_latest = earliest, latest
                            deadline = (f"{earliest:%b %d, %Y %H:%M} UTC (the earliest reading: the process names no time zone or holiday calendar; "
                                        f"counted Monday to Friday, it is {utc_reading:%b %d, %Y %H:%M} in UTC)")
                        else:
                            deadline = "the deadline in your process"
                        claim.dispute_body = dispute_draft(s, d, claim.id, claim.amount, award, deadline, rule.quote if quoted else None)
                elif action == "send_dispute" and claim.dispute_deadline_latest and now > claim.dispute_deadline_latest:
                    raise ValueError(f"The revisit window closed on {claim.dispute_deadline_latest:%b %d, %Y %H:%M} UTC.")
                claim.status, claim.updated_at = after, now
                claim.events.append({"action": after, "at": stamp})
                if action == "match_credit":
                    credited = Decimal(str(amount)).quantize(Decimal("0.01")) if amount is not None else claim.amount
                    invoice = s.invoice
                    expected = {"customer_id": invoice.customer_id, "vendor": invoice.vendor, "service": invoice.service,
                                "period": invoice.period, "currency": claim.currency, "claim_reference": claim.id, "amount": str(credited)}
                    credit = {**expected, "reference": f"DEMO-CREDIT-{scenario_id}"}
                    if not matches_credit(expected, credit):
                        raise ValueError("The credit does not match this claim")
                    claim.award_amount = credited
                    claim.events.append({"action": "simulated_matching_credit", "at": stamp, **credit})
                db.execute("UPDATE claims SET payload=? WHERE scenario_id=?", (claim.model_dump_json(), scenario_id))
                self.store.event(after, f"{s.product}: {after.replace('_', ' ')}", "Simulated workflow; nothing sent to a vendor.", db)
            return claim.model_dump(mode="json")
