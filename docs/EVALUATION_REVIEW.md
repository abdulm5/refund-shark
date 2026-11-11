# Human reference review

Status: **AI-assisted review completed November 10; a person's review remains pending**. See the [findings and corrected demo behavior](AI_REFERENCE_REVIEW.md). The 24 frozen extraction runs and original draft labels are unchanged. The worksheet/scorer supports a small review of the main Jira agreement, without relabeling the existing held-out experiment.

The app, demo, and tests work without this review. It is not an application setup step. Complete it before reporting a human-reference extraction score; otherwise leave the status pending and describe only the tests and quote checks actually performed. An AI-assisted check cannot substitute for a person's attestation. The review covers one agreement and 16 fields, not all eight agreements or every possible contract.

Run `uv run python -m eval.review_labels prepare`. The `.cache/label-review/` directory receives a draft JSON worksheet and the complete frozen input text, including the compensation procedure. Preparing it requires the original cached source pages; new live pages cannot silently replace them. The private source copies and unfinished review stay out of Git.

Read `frozen-source.txt`, then review every field in `atlassian-review.json`. To start from the corrected AI proposal instead of the original draft, copy `data/reviews/atlassian-jira-premium.ai-proposed.json` to a new worksheet in `.cache/label-review/`; preserve any existing review work. Its human-review flags are deliberately still false. The AI findings help identify issues but do not replace checking the source yourself.

| Fields | What to check |
| --- | --- |
| Vendor, covered services, plan | Product and plan scope, including Premium versus Enterprise |
| Uptime commitment, period, time zone | The actual promise and measurement window |
| Downtime definition | Covered experiences, per-minute aggregation, strictly greater than 5% error rate |
| Credit tiers | All rates and exact inclusive/exclusive boundaries |
| Credit basis, cap, minimum | Affected product fees, receiving invoice cap, absent versus unknown minimum |
| Exclusions, prerequisites | Every stated exclusion and required fact, including requester authorization |
| Claim window, procedure, remedy | Filing deadline and timezone uncertainty, support during the incident, service credit versus cash refund |

Correct the typed values and quotes where needed. Mark each corresponding `fields_reviewed` entry true only after checking it. Enter your name and review date, and set `confirmed_human_review` true after completing the review. These are supplied review labels, not an authenticated or legal approval.

Then run:

```sh
uv run python -m eval.review_labels score .cache/label-review/atlassian-review.json
```

The report compares material fields, exact credit tables, decision outcomes, and amounts across the three frozen Atlassian runs and four existing scenarios. It separately reports cases where both outputs need review and false-positive eligibility. Quotes and rules are checked before scoring. A matching quote alone does not prove a correct interpretation.

The four original evaluation scenarios are now pinned to a hash-checked snapshot captured before the November 10 demo corrections. Human and AI comparisons use those original inputs, including their missing authorization facts. To reproduce the AI-only diagnostic without human attestation, run `uv run python -m eval.review_labels audit data/reviews/atlassian-jira-premium.ai-proposed.json`. It reports material field agreement, never human-reference accuracy.

Describe any resulting numbers as **a post-hoc human reference review of one agreement**, not held-out accuracy or a benchmark across all eight agreements. Do not edit frozen model outputs to improve the score.
