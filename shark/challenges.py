"""Counterfactuals run the production engine on copies; they never write case state."""
from datetime import timedelta

from shark.engine import evaluate
from shark.engine.metrics import apply_logs
from shark.models import Maintenance

CHALLENGES = {
    "missing_ticket": ("Remove the support ticket", "The required ticket is no longer evidenced."),
    "late_ticket": ("Open the ticket too late", "Assume the required ticket was opened outside customer impact."),
    "maintenance": ("Confirm excluded maintenance", "Treat the measured outage windows as confirmed scheduled maintenance."),
    "deadline": ("Miss the filing deadline", "Move the evaluation clock beyond every reading of the filing deadline."),
    "duplicate": ("Import the same evidence twice", "Add an identical copy of each customer log record. Overlapping minutes must count once."),
    "missing_logs": ("Remove the customer logs", "Keep public incident discovery, but remove the customer's measurement evidence."),
    "citation": ("Break a supporting citation", "Replace the credit-table quote with text absent from the saved agreement."),
}


def available(s):
    tickets = any("support_ticket" in p for p in s.terms.prerequisites.value or [])
    measured = any(i.evidence_kind != "public_timeline" and i.metric_verified for i in s.incidents)
    excluded = any(e.kind == "scheduled_maintenance" for e in s.terms.exclusions.value or [])
    d = evaluate(s)
    return [{"id": key, "label": label, "description": description,
             "available": (tickets if key in {"missing_ticket", "late_ticket"} else
                           measured and excluded if key == "maintenance" else
                           d.deadline is not None if key == "deadline" else
                           measured if key in {"duplicate", "missing_logs"} else True)}
            for key, (label, description) in CHALLENGES.items()]


def challenge(s, action):
    option = next((x for x in available(s) if x["id"] == action), None)
    if not option or not option["available"]:
        raise ValueError("That challenge does not apply to this case's evidence and contract.")
    original = apply_logs(s.model_copy(deep=True))
    changed = original.model_copy(deep=True)
    before = evaluate(original)
    if action in {"missing_ticket", "late_ticket"}:
        for p in changed.terms.prerequisites.value or []:
            if "support_ticket" in p:
                changed.account.prerequisites[p] = None if action == "missing_ticket" else False
    elif action == "maintenance":
        changed.maintenance.extend(Maintenance(service=changed.account.service, interval=span.model_copy(),
            exclusion_confirmed=True, reference="Isolated challenge: confirmed maintenance", simulated=True)
            for i in changed.incidents if i.metric_verified and i.evidence_kind != "public_timeline" for span in i.intervals)
    elif action == "deadline":
        changed.as_of = (before.deadline_latest or before.deadline) + timedelta(seconds=1)
    elif action == "duplicate":
        copies = [i.model_copy(deep=True) for i in changed.incidents if i.evidence_kind != "public_timeline"]
        for i in copies:
            i.id += "-challenge-duplicate"
        changed.incidents.extend(copies)
        changed = apply_logs(changed)
    elif action == "missing_logs":
        changed.incidents = [i for i in changed.incidents if i.evidence_kind == "public_timeline"]
        changed.account.history_complete = False
    elif action == "citation":
        changed.terms.credit_tiers.quote = "CHALLENGE: this sentence is deliberately not in the contract."
    after = evaluate(changed)
    previous = {c.name: (c.status, c.detail) for c in before.checks}
    changes = [c.model_dump(mode="json") for c in after.checks if previous.get(c.name) != (c.status, c.detail)]
    fields = {c.field for c in after.checks if c.field and c.status != "pass"}
    if action == "maintenance":
        fields.add("exclusions")
    return {"action": action, "label": option["label"], "description": option["description"], "isolated": True,
            "before": before.model_dump(mode="json"), "after": after.model_dump(mode="json"), "changed_checks": changes,
            "amount_change": str(after.amount - before.amount), "unchanged": before.model_dump() == after.model_dump(),
            "clauses": [{"field": field, "quote": getattr(original.terms, field).quote, "section": getattr(original.terms, field).section}
                        for field in sorted(fields) if field in type(original.terms).model_fields],
            "note": "Hypothetical inputs, evaluated by the same engine. No saved evidence, claim, or approval was changed."}
