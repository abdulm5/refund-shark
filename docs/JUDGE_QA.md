# Explaining Refund Shark

## Opening pitch

Refund Shark turns contractual uptime promises into evidence-backed service-credit requests. It links an agreement, customer monitoring evidence, and the right invoice; shows the calculation and supporting clauses; prepares a request for approval; and checks a supplied later invoice for the credit. Models read the terms. Deterministic code decides what the evidence supports.

## Questions to rehearse

**What is real in the demo?** The cited Atlassian agreement and public incidents are real saved sources. The portfolio's customers, invoices, monitoring logs, tickets, and review confirmations are explicitly simulated. The October 8 replay clock demonstrates the workflow before those historical filing deadlines; it is not permission to file those old claims today.

**Why isn't a public outage enough?** Discovery and measurement serve different purposes. The supported Jira agreement measures customer request failures in covered experiences. A status page cannot establish the customer's actual error rate or prove prerequisites such as a timely support ticket.

**Why not let the model decide?** Arithmetic, interval merging, exact tier boundaries, filing windows, and approval checks need reproducible behavior. The model extracts cited terms into a schema; code verifies quotes, refuses unsupported rules, and evaluates evidence. Claim facts are rendered from that decision.

**Does finding a quote prove the extraction is correct?** No. It checks that the quoted text exists in the supplied source. A model could still choose the wrong plan or misinterpret a boundary. An [AI-assisted reference audit](AI_REFERENCE_REVIEW.md) found and corrected three demo-label issues, including missing requester authorization. Human reference review is still pending, so no extraction-accuracy result is claimed.

**How many vendors are fully supported?** One complete eligibility path: Atlassian Jira Premium under the supported rules. The reader has saved runs for eight agreements; those are not eight complete claims integrations. Unsupported formulas stop for review.

**What happens when the evidence is wrong or incomplete?** Missing facts stay unknown, known failed prerequisites cause refusal, and conflicting logs or ambiguous invoices block the proposal. Duplicate measurements count once. Accepting changed evidence requires a new draft and invalidates earlier approval; submitted cases are locked.

**What does the downloadable bundle prove?** Its verifier checks file integrity, remeasures bundled logs, and reproduces the saved decision with the matching engine. It does not establish independent source authenticity or guarantee the vendor will award the request.

**Is it connected to Ramp and Datadog?** Read-only adapters are implemented and tested with representative recorded or mocked responses. Live account access has not been exercised. Ramp spend identifies possible subscriptions; it does not establish the product plan or price the claim. Datadog mappings require human confirmation.

**Does it actually send claims or recover money?** Claim submission and receipt are simulated. The reconciliation workflow checks user-confirmed invoice data. No money was recovered in this demo. Optional owner alerts have a separate disabled-by-default delivery path, with explicit review.

**What would production require?** Authenticated users and tenant isolation, live connector validation, operational monitoring, and vendor-specific rule coverage before broader use. The current deliverable is a local working prototype with review gates and clear capability boundaries.

## Strongest three demo moments

1. Harbor's timely ticket changes the supported portfolio total from $900 to $1,380; Cobalt remains excluded.
2. Removing the ticket in a challenge makes Harbor's supported amount $0 without editing its saved $480 draft.
3. The downloaded evidence bundle reproduces that saved $480 decision offline.

Describe these as supported service-credit requests. Vendor approval and actual receipt are separate outcomes.
