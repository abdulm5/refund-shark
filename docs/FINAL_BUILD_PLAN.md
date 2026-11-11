# Final recovery workflow

Approved scope: recovery scan, interactive claim challenges, reproducible evidence bundles, and evaluation/submission preparation. Local only; no external claims, messages, paid model calls, or publication.

1. **Recovery scan.** Scan confirmed invoices and configured vendors into a persisted proposal queue. Match account/product/plan/month exactly, refuse ambiguous invoices and conflicting evidence, and keep missing facts unknown. Recompute proposals when evidence changes. Only accepting a reviewed proposal changes a monthly case; earlier approvals cannot carry into a changed case. Surface ready, missing evidence, ineligible, and already handled outcomes. Include an isolated portfolio replay that demonstrates evidence arriving.
2. **Challenge this claim.** Re-evaluate a deep copy with missing/late tickets, maintenance, expired deadlines, and duplicate evidence. Show the original decision, changed decision, facts changed, checks, and citations. Saved cases and approvals remain untouched.
3. **Evidence bundle.** Download selected case evidence, source text, terms, decision, and optional draft in a ZIP with a manifest. A local CLI validates file hashes and engine compatibility, remeasures logs, and reproduces the decision without network access. Limit archive sizes and reject unsafe paths. Hashes demonstrate internal integrity, not source authenticity.
4. **Evaluation and delivery.** Prepare a reference-label review worksheet and scoring path without representing machine labels as human reviewed. Update the demo script and README; run regression tests, production build, browser flows, and a clean source-copy check.

## Design

Retain the existing white (#ffffff), pale sidebar (#f7f8f3), ink (#252a21), muted green (#7c8373), and lime (#e5f58a) palette. Inter handles headings and body; tabular figures carry money and durations. A horizontal outcome summary sits above an actionable recovery list. Each expanded proposal shows the missing facts and source-linked calculation. The signature interaction is a before/after claim comparison, visibly isolated from saved work. Existing navigation and form styles remain consistent, with stacked layouts on mobile and visible focus states.

## Verification

Cover scan idempotence, evidence refresh, identity/period ambiguity, stale acceptance, locked submissions, explicit revisions, deadline clocks, unknown prerequisites, and isolated replay. Challenges must run the real engine and preserve stored claims. Bundle verification must detect tampering, missing evidence, unsafe archives, incompatible engines, and calculation mismatches. Human reference review remains pending until performed by a person.

## Delivered and verified

All four implementation areas are complete. The human review tooling and local worksheet are prepared; the actual human attestation and resulting score remain pending. No live integration credentials, paid model calls, outbound messages, vendor submissions, or publication were used for this build.

| Check | Result |
| --- | --- |
| Workspace regression suite | 313 passed after the demo-entry update |
| Fresh source copy, without secrets, databases, installed dependencies, or private raw sources | Previous final-workflow pass: locked Python and Node dependencies installed from local package caches; 310 passed, 2 private-source checks skipped as expected |
| Production build from fresh copy | Default Next.js build and TypeScript validation passed; webpack build also passed |
| Offline fixture evaluation | All 6 expected outcomes and amounts matched |
| Source integrity in fresh copy | All 3 committed source excerpts intact; private snapshots correctly reported unavailable |
| Browser journey | Portfolio starts at $900; arriving ticket raises it to $1,380; explicit acceptance saves Harbor's $480 draft requiring approval; missing-ticket challenge returns $0 without changing the saved draft |
| Downloaded bundle | All 18 files checked; $480 decision reproduced offline in both the workspace and fresh source copy |
| Responsive review | Desktop and 390 px layouts reviewed; claim toolbar overflow fixed; no browser console errors observed |
| Sample PDFs | All 6 rendered and visually reviewed |
| Review package | README, integration guide, narrated demo script, and screenshots updated |

The temporary QA preview was stopped after verification. Run `make demo` to start the app again; use the isolated portfolio or guided-demo reset buttons for a fresh presentation. No Git staging, commit, push, or external submission was performed. See [EVALUATION_REVIEW.md](EVALUATION_REVIEW.md) for the remaining human label review, and [DEMO_SCRIPT.md](DEMO_SCRIPT.md) for the recommended presentation.

## Demo presentation pass

The overview now leads with **Start demo**, linking directly to the isolated portfolio and its three prompts. An idempotent start endpoint preserves existing progress on repeat visits and duplicate requests; resetting remains explicit. The prompts open Cobalt's refusal, add Harbor's ticket, and reveal its calculation or saved challenge. Desktop and 390 px mobile entry, resumed drafts, and the complete prompt flow were verified in the browser with no console errors observed. The production webpack build and TypeScript validation passed, and all 313 tests passed.

The README opens with a 28-second, seven-frame GIF captured from the running app (about 1 MB), with captions and a static-image alternative. All GIF frames were rendered and visually checked. [JUDGE_QA.md](JUDGE_QA.md) provides the pitch and answers about supported rules, simulated evidence, live-access limits, and evaluation. The human worksheet remains unconfirmed; this pass did not attest to a review on the user's behalf.
