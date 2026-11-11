# Atlassian Jira Premium reference review

Completed **November 10, 2026 by Codex (AI assistant)**. All 16 fields were checked against the complete frozen SLA and compensation procedure. **Human review remains pending.** The saved model runs were visible during this work, so this is a post-hoc diagnostic review, not an independent accuracy evaluation.

Three reference fields needed correction. The same corrections have been applied to the application's demo terms. The original draft labels, 24 model outputs, prompt/schema configuration, and evaluation report remain unchanged.

## Source and scope

The review uses the October 30 snapshots of the [Atlassian SLA](https://www.atlassian.com/legal/sla) (effective April 30, 2026) and [compensation procedure](https://support.atlassian.com/subscriptions-and-billing/docs/service-level-agreement-for-atlassian-cloud-products/). The combined full-text SHA-256 is `84beb5c6756a9b39a2ce37aa67852670470e3a9057659f7987defd5d0008bc21`. The evaluator checks both raw-file and parsed-text hashes before comparing labels. Current website content cannot silently replace these inputs.

This review selects **Jira Premium** and the demo's direct subscription. It does not validate other products, other plans, negotiated agreements, or reseller pricing. Full source copies remain in the ignored local cache; the repository contains bounded excerpts and source metadata.

## Field findings

| Field | Finding | Source location |
| --- | --- | --- |
| Vendor | Retain Atlassian. | SLA §1 |
| Covered services | Retain Jira for the selected product. | Appendix A |
| Plan | Retain Premium; do not apply Enterprise's extra tier. | SLA §1; Appendix B |
| Uptime commitment | Retain 99.9%. | SLA §1 |
| Measurement period | Retain calendar month, per eligible product. | Appendix B, Calculation |
| Timezone | Retain UTC for measuring the month. | Appendix B, Calculation |
| Downtime definition | Retain one-minute customer error rate strictly greater than 5%, across the four covered Jira browser experiences. Zero requests means a zero error rate; API/mobile activity is outside this scope. | Appendices A and B |
| Credit tiers | Retain 10% for uptime [99%, 99.9%), 25% for [95%, 99%), and 50% below 95%. | Appendix B, Premium table |
| Credit basis | Retain affected-product monthly fees. Annual fees require monthly proration; bundle allocation belongs to Atlassian. See reseller limitation below. | Appendix B notes; compensation procedure, Receive compensation |
| Credit cap | Retain 100% of the affected-product amount on the receiving invoice. | SLA §2.2 |
| Minimum credit | Retain explicitly absent: neither frozen page states a minimum. | Complete review of both pages |
| **Exclusions** | **Correct:** separate breach from not provisioning and scheduled from emergency maintenance; include sandbox, Free/Beta, and trial scope. Only confirmed scheduled-maintenance windows are subtracted automatically. | SLA §3; compensation procedure, Process to get compensation |
| **Prerequisites** | **Correct:** add requester authorization to production instance, paid account, and support ticket during the incident. Eligible requester roles are billing/technical contact, billing admin, site admin, or organization admin, as applicable. | SLA §2.2; compensation procedure, Request a compensation credit |
| Claim window | Retain 15 days after month end; filing timezone remains unspecified. Measurement in UTC does not establish the filing timezone. | SLA §2.1; compensation procedure |
| **Claim procedure** | **Correct:** remove the unsupported five-field enumeration. The source requires completion of the vendor form without listing every field. Use `other` so the draft reminds the user to complete that form. Keep the compensation topic and one ticket per app/month. | SLA §2.1; compensation procedure, To submit a request |
| Remedy | Retain future service credit, with no cash value/refund for unused credit. | SLA §2.2 |

`machine_checkable` is an implementation annotation, not contract language. Emergency-maintenance reasonableness and policy applicability still need review. A matching citation proves that text exists, not that it supports every part of a structured value. In particular, the saved model runs contain the authorization prerequisite but do not include the role paragraph in their prerequisite citation; the proposed reference now includes it.

## Eligibility defect found and fixed

With the original demo terms, the September claim remained eligible for **$900 even when `authorized_requester` was explicitly false**, because those terms omitted the prerequisite. The existing engine already handles missing and failed prerequisites correctly once the term is present.

| Requester fact | Original demo terms | Corrected demo terms |
| --- | --- | --- |
| Missing or unknown | Eligible, $900 | Needs review, $0 claim amount |
| Explicitly false | Eligible, $900 | Not eligible, $0 claim amount |
| Explicitly true | Eligible, $900 | Eligible, $900 |

The bundled fictional accounts now explicitly assume an authorized billing administrator, marked **simulated** in their provenance. This preserves the intended demo outcomes without asserting anyone's real permissions. Previously saved user cases and approvals are not silently rewritten; cases based on older terms need their normal agreement review and re-evaluation.

The source excerpt builder now includes the role paragraph. Regression tests exercise missing, unknown, false, and true authorization against the corrected demo. The form-completion reminder and fuller exclusion list are also present in those terms.

Verification: **318 tests passed** on November 10. This includes API coverage showing that a case with missing authorization remains in review and cannot produce a claim draft, source/excerpt integrity checks, and exact reproduction of the unchanged historical evaluation report.

## Diagnostic comparison with saved runs

[Proposed labels and field notes](../data/reviews/atlassian-jira-premium.ai-proposed.json) and the [machine-readable audit](../data/reviews/atlassian-ai-audit.json) are included in the repository. All human-review flags remain false.

| Diagnostic | Result |
| --- | --- |
| Material field agreement, each of three saved runs | 15 of 16 (93.75%) against the AI-proposed reference |
| Exact credit-table agreement | 3 of 3 runs |
| Differing field | Exclusions, including categorization and machine-checkability annotations |
| Original scenario comparisons | 12 |
| Both sides need review | 12 |
| Conclusive decision matches | 0 |
| False-positive eligible decisions in these comparisons | 0 |

**93.75% is not extraction accuracy.** The comparison excludes some descriptive wording and claim-channel wording, and uses an AI-proposed reference. Every original scenario is blocked by missing authorization or other evidence, so the 12 matching review outcomes do not demonstrate correct pricing or establish a reliable false-positive rate. The explicit authorization counterexample above is a separate diagnostic test.

Before correcting the demo, its four original evaluation scenarios were preserved in [a hash-checked snapshot](../data/gold/atlassian-scenarios.pre-audit.json), registered in the manifest. The historical evaluation and this audit read that snapshot; correcting the application fixtures therefore does not improve their recorded decision scores. This snapshot was captured on November 10, not presented as a new held-out dataset.

## Remaining limits

- Reseller subscriptions have a different fee basis and credit recipient under SLA §2.3. The current account schema does not represent that relationship; do not claim this review validates reseller cases.
- Atlassian's own logs remain authoritative for its final determination. Customer logs support the proposed claim but do not guarantee vendor approval.
- Non-machine exclusions, vendor-form completeness, and the three-business-day reconsideration window still require the documented workflow and responsible reviewer.
- This review covers one agreement and its supplementary procedure, not all eight saved extraction documents.

## Reproduce or finish a human review

With the original cached pages available, run:

```sh
uv run python -m eval.review_labels audit data/reviews/atlassian-jira-premium.ai-proposed.json
```

The `audit` command explicitly requires human review to remain unconfirmed. It cannot produce the human-review report. To complete that separate step, a person can copy the proposed worksheet into `.cache/label-review/`, verify all 16 fields against `frozen-source.txt`, and record their own name, date, field checks, and confirmation before using `score`. See [the review instructions](EVALUATION_REVIEW.md). No person's name or approval was supplied by this audit.
