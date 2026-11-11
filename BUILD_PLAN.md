# Refund Shark — implementation plan

Planning date: October 29, 2026 (America/Los_Angeles).

## Agreed direction

- Deliverable: a GitHub repository, per Abdul's clarification. Video and public hosting are optional.
- First release: a local demo that works without API credentials.
- Current access: no API accounts or keys. Development budget is undecided; paid calls are not a dependency of the initial build.
- Deadline assumption: November 10, 2026, from the supplied brief. Target a finished repository by November 9 and submission by November 10 at 6 PM PT.
- Workspace: `/Users/abdulmohammad/Projects/ramp` was empty when planning began. This document is the only implementation artifact created during planning.
- Product rule: the model extracts and helps write; deterministic code calculates and applies supported rules.

## What we are building

A local app that connects a vendor agreement, incident evidence, and an invoice to an explainable service-credit decision. The user can inspect the source clauses, see the calculation, prepare a claim, approve it, and track a simulated submission and credit.

The first complete flow covers one selected Atlassian product and plan. Eight SLA documents form an extraction evaluation set; they do not imply eight fully implemented eligibility integrations.

The interface should show **Potential service credits**, with estimated, review-needed, and confirmed amounts separated. Most remedies in the proposed dataset are future billing credits, so the UI should not describe every amount as a cash refund or money already owed.

Three required demo cases:

| Case | Inputs | Result |
| --- | --- | --- |
| Claim candidate | A real incident plus clearly labeled simulated invoice, customer evidence, and account prerequisites | Show the estimate and calculation; demonstrate approval using the simulated customer evidence |
| Refusal | An authored scenario with downtime excluded by the selected agreement | Not eligible, with the relevant clause and calculation |
| Missing evidence | A real public incident without the customer evidence required by its agreement | Needs review, with the missing inputs named and any provisional estimate kept separate |

Use a real maintenance example only if we find one with sufficient evidence. Never present an invented incident, ticket, invoice, evaluation score, or payout as real.

## Corrections to the original brief

1. **A public incident is discovery evidence, not sufficient proof of contractual downtime.** Atlassian's current SLA uses customer request error rates per minute and treats its own monitoring as authoritative. It also defines the cap against the future receiving invoice. Our design must represent these distinctions instead of treating every agreement as duration × monthly fee. [Atlassian SLA](https://www.atlassian.com/legal/sla)
2. **Claim prerequisites belong in the engine.** Atlassian's support process requires a technical support request during the incident, followed by a compensation request. Missing proof produces review; a known failed prerequisite produces refusal. The reminder banner can remain optional. [Atlassian compensation process](https://support.atlassian.com/subscriptions-and-billing/docs/service-level-agreement-for-atlassian-cloud-products/)
3. **Sandbox access need not begin with an email.** Ramp documents a sandbox request form and separate sandbox credentials. Keep the mock invoice adapter first, then assess integration after access is available. [Ramp sandbox guidance](https://support.ramp.com/accessing-the-developer-api)
4. **Do not use deprecated terms as a current agreement.** GitHub's linked SLA explicitly applies to customers licensed before January 4, 2021 and redirects newer customers to current terms. Historical terms can be an extraction fixture, but cannot silently govern a current claim. [GitHub historical SLA](https://docs.github.com/en/site-policy/site-policy-deprecated/github-enterprise-service-level-agreement)
5. **Quote matching proves provenance, not interpretation.** A quote can exist while the extracted number, plan, or meaning is wrong. Keep separate citation verification, semantic evaluation against gold, and rule validation.
6. **A set of allowed numbers is not a sufficient draft guard.** It would allow the model to swap a fee and a credit when both numbers appear in the input. Render the entire factual claim body in code; let the model vary only narrowly defined introductory and closing text. Validate those additions and fall back to the template.

## Scope

Required for the repository:

- Python backend, typed contract and evidence models, and deterministic eligibility engine.
- One complete vendor/product/plan path with a clear capability boundary.
- Citation-aware extraction implementation and an offline replay adapter.
- Statuspage ingestion with dated snapshots and explicit coverage limitations.
- Mock invoice source, claim preparation, approval, and simulated tracking.
- Small local dashboard with the three demo cases.
- Engine tests, a reproducible evaluation runner, and honest recorded results when live extraction is available.
- README, environment example, dependency locks, CI, and one-command demo startup.

Optional after the core passes:

- Ramp sandbox adapter; tone controls; multi-customer impact view; live incident reminders.
- Hosted demo and video.

Outside this release:

- Sending real vendor claims, production Ramp access, autonomous financial actions, multi-user SaaS authentication, or general support for arbitrary contract formulas.

## Architecture

Use Python 3.12, FastAPI, Pydantic v2, SQLite, pytest, and a thin Next.js/TypeScript frontend with Tailwind. Keep model access behind one interface. The proposed Claude Sonnet model can be selected through configuration once access exists; Anthropic currently documents schema-constrained JSON output. Our quoted evidence fields are part of that JSON, rather than combining it with native citation blocks. [Structured output documentation](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)

Start with authored fixtures, deterministic templates, and a CLI. Add the UI after the same application service produces the intended results. Avoid adding an agent framework or background queue to the MVP.

```text
SLA snapshots → extracted terms → citation/rule validation
                                      ↓
Incident evidence + account context + invoice → decision + audit trace
                                                        ↓
                                           deterministic claim draft
                                                        ↓
                                               human approval
                                                        ↓
                                         simulated sent / credit tracking
```

```text
ramp/
  shark/
    models/
    extract/          # snapshot loading, provider/replay adapters, citations
    incidents/        # Statuspage fetcher, history normalization, coverage
    invoices/         # mock adapter; optional Ramp adapter
    engine/           # periods, metrics, exclusions, pricing, prerequisites
    claims/           # deterministic renderer, optional prose, lifecycle
    storage/          # SQLite and run records
    pipeline.py
    cli.py
  api/
  web/
  data/
    sources.yaml
    fixtures/         # authored examples and explicitly marked demo inputs
    gold/             # human-reviewed labels with source provenance
  eval/
  tests/
  docs/
  .github/workflows/
  .env.example
  Makefile
  README.md
```

Proposed commands, to be implemented:

- `make demo`: start the complete local demo using bundled authored fixtures; no keys or external calls.
- `make test`: run deterministic checks.
- `make fetch-sources`: acquire public source material and record hashes.
- `make eval`: evaluate available extraction outputs and report provenance and coverage.
- `shark run --vendor atlassian --product jira --plan premium --period 2026-10`: execute the selected real-source or explicitly simulated scenario.

## Data and decision contracts

Model these concepts before building screens:

| Model | Required information |
| --- | --- |
| SourceSnapshot | URL, retrieval time, effective date if available, content hash, parser version |
| CitedValue | Value, source reference, exact quote, section/span, verification result, missing-value reason |
| SLATerms | Product and plan, measurement method, period/timezone, explicit tier boundaries, fee basis, cap scope, minimum comparison, exclusions, prerequisites, deadline rules, submission procedure, remedy |
| AccountContext | Customer/product/plan mapping, environment, billing cycle, account prerequisites, support evidence |
| IncidentEvidence | Source ID/link, affected component/region, ordered updates, interval provenance, metric evidence, coverage/completeness |
| Invoice | Customer/vendor/product, service period, currency, eligible fee lines, payment context, simulated-data flag |
| Decision | Outcome, provisional amount separately from supported amount, calculation steps, applied clauses, missing facts, deadline, evidence quality |
| Claim | Decision revision, immutable facts, rendered body, approvals, simulated submission events, credit match evidence |

Rule handling:

- Represent missing, unsupported, contradicted, and verified values distinctly. An absent cap must not be confused with a failed extraction.
- Support the selected calendar-month contract first. Represent quarters and billing cycles in the schema; return review for unsupported calculations instead of interpreting them as months.
- Encode `<`, `<=`, `>`, and `>=` explicitly for tier boundaries and minimum-credit rules.
- Use integer durations and Decimal arithmetic. Preserve exact threshold comparisons; round money only under the applicable currency/rule policy.
- Filter and merge intervals per covered product, customer, and region as appropriate. Split at the contract's period boundaries.
- Maintenance overlap alone does not prove an excluded outage. Check scope and exclusion conditions; keep uncertainty visible.
- Use historical incident updates, not the component's current operational status, to interpret an old incident. A return from monitoring to an active incident needs explicit handling.
- Keep public-timeline estimates separate from contractual metrics. Unsupported error-rate or visitor-ratio formulas produce review without the necessary inputs.
- Treat an empty or truncated feed as unknown coverage, not automatically 100% uptime. Complete empty fixtures can test the no-downtime path.
- Distinguish the current evaluation time from the billing period. Historical replay displays its chosen as-of time; expired claims remain expired in current mode.
- Unknown deadline timezone or billing-cycle boundaries must be surfaced, not guessed.
- Any changed decision invalidates prior approval. Re-running a pipeline cannot duplicate claims.
- Credit matching uses customer, vendor, service, currency, period, and references as well as amount. Approximate matches remain candidates for review.

## Build sequence and acceptance gates

Dates are target checkpoints; finish each gate before adding optional features.

| Phase | Target | Work | Done when |
| --- | --- | --- | --- |
| 1. Foundation and demo fixtures | Oct 29–30 | Project skeleton, locked dependencies, typed models, source manifest, three authored scenarios, local invoice adapter | Fixtures validate and the CLI loads them without credentials |
| 2. Deterministic engine | Oct 31–Nov 2 | Period arithmetic, metric adapters, interval handling, tiers, pricing, prerequisites, deadlines, audit trace | The original 13 engine cases plus the added evidence/boundary cases pass |
| 3. Extraction and real sources | Nov 3–4 | Snapshot intake, citation verifier, provider/replay adapters, six-document tuning set, incident ingestion | One real-source scenario yields an explainable result; missing evidence consistently causes review |
| 4. Complete claim flow | Nov 5 | Persisted runs, deterministic drafting, approval/rejection, simulated sent/credit states, FastAPI endpoints | One CLI/API flow reaches a draft and approved simulated submission; reruns are idempotent |
| 5. Dashboard | Nov 6–7 | Opportunities list, detail view, source clauses, calculation, required evidence, claim/tracker controls | All three cases work through the UI with clear demo and estimate labels |
| 6. Evaluation and packaging | Nov 8–9 | Freeze prompt/config, final held-out evaluation if API access exists, clean-clone verification, CI, README, screenshots | Repository is reproducible and every reported result is backed by a saved run |
| 7. Submission buffer | Nov 10 | Verify repository access, final smoke test, submit repository link | Repository is accessible to reviewers and submitted by the chosen 6 PM PT target |

Start public incident snapshots early during implementation. The documented public API exposes the 50 most recent incidents and maintenance entries, so a new cache cannot prove full historical coverage. Store revisions by source ID and fetch time. [Statuspage public API](https://status.atlassian.com/api)

## Verification and evaluation

Preserve all 13 engine scenarios from the original brief. Add tests for:

- Plan mismatch, unsupported metric, missing customer evidence, and missing versus known-failed claim prerequisites.
- Exact minimum amount, each inclusive/exclusive tier boundary, currency mismatch, and unknown fee allocation.
- Duplicate incidents, reversed update ordering, reopening after monitoring, and incomplete history.
- Month/quarter boundaries, leap years, explicit deadline boundaries, and timezone uncertainty.
- Quote present but wrong extracted value; altered source hash; failed citation needed by a decision.
- Prose attempting to swap factual values, add a number, or change the remedy.
- Approval invalidation, invalid lifecycle transitions, and duplicate credit matches.

Use invariant tests for interval arithmetic: input ordering and duplicate intervals must not change the union, and counted downtime must stay within the period duration. Add one pipeline integration test and one browser smoke check for the three outcomes.

Evaluation protocol:

1. Confirm eight distinct source documents and their versions before freezing the dataset. Start from the supplied seven candidates and select the eighth for a useful supported or deliberately unsupported case. Verify availability before committing to a vendor. Do not count two plans from the same document as independent documents.
2. Make six tuning documents and two held-out documents explicit in a manifest. Human-review gold values against their cited sources; do not treat model-generated gold as ground truth without review.
3. Tune only against the six-document set. Freeze prompt, schema, source hashes, model configuration, and code before evaluating the held-out set.
4. Perform three extraction repetitions per document. Interpret “held out once” as one final evaluation batch with three repetitions, with no tuning after observing its results.
5. Report field-value accuracy separately from quote-match rate, full credit-table accuracy, supported-case decision/amount agreement, and false-positive eligibility decisions.
6. Show counts, unsupported/review coverage, and per-run results with the worst result. Agreement caused by both paths returning review is reported separately from successful priced decisions.
7. Use curated boundary fixtures as well as real incident periods. A set consisting only of no-credit months is not a useful decision evaluation.

Without a model key, the repository can demonstrate replay, citation checks, and engine behavior. Live extraction accuracy remains **not run**; replay results are never labeled as new live model evaluation. Eight documents remain a small validation set, not a general benchmark.

## Reproducibility and cost

Keep a fully authored, redistributable demo dataset in the repository so `make demo` works offline. Keep fetched raw vendor documents in an ignored cache; commit source URLs, hashes, retrieval metadata, reviewed labels, and bounded supporting excerpts where appropriate. A fresh fetch that changes the hash must not silently stand in for the original evaluation source.

For a real-source demo, offer a separate fetch/preparation command and explain the snapshot requirements. Clearly distinguish the offline authored demo, replayed model output, and fresh extraction.

Initial development uses no paid services. Once credentials are available, run a small extraction pilot, inspect actual token usage, and choose a development budget before the full evaluation. Cache results by source hash, model, prompt, and schema; use bounded retries and log usage. Keep secrets server-side and out of the repository.

Ramp integration remains optional until access and the actual bill/credit fields are verified. If available, add a read-only adapter through the same invoice interface. It must not become a prerequisite for the local demo.

## Decisions settled and remaining

Settled: repository deliverable, local-first demo, no existing API access, budget undecided. No further answers are needed to begin the foundation and engine when Abdul says to start.

Before live extraction: select/configure an API provider and establish the spend budget. Before creating the remote repository: confirm the GitHub destination and visibility if these are not already clear from the account context. Before submission: confirm that the deadline in the pasted brief is still the intended deadline.

Keep optional work out until the complete local flow passes. If time tightens, cut hosting/video, live reminders, multi-customer view, tone controls, and Ramp integration first. If the frontend threatens delivery, use a simpler local UI. Preserve cited extraction, tested decisions, refusal/review behavior, reproducibility, and honest evaluation reporting.
