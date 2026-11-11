# Documents, vendor setup, and recovery

This is a local, single-workspace application. Keep the API bound to localhost. There is no company login or tenant isolation; do not expose it as a public service.

## Local walkthrough (no external accounts or model calls)

1. Start `make demo`. Open **Documents → Subscription invoice**, review the extracted fields, and confirm them. This is a labeled sample; no real billing data is used.
2. Open **Agreements → Atlassian** to load the saved model run. This saves an agreement read without calling Claude.
3. Open **Vendors → Add vendor**, select the confirmed invoice, choose US East and the saved Jira Premium agreement, and assign an owner (use a test address). Confirm the subscription and save. The four setup checks should turn green.
4. In the evidence inbox, import the sample September log. Expand **Add a support-ticket reference**, fill the simulated ticket, and save. The log measures 62 qualifying minutes; the ticket is inside the supplied incident window.
5. Upload and confirm the **Invoice with credit** sample from Documents. Back in the vendor, check the saved log and ticket, then choose the later invoice. Use the demo replay date and confirmations, then **Measure and create monthly case**: $900 supported at the October 8 replay clock. If this account/month already has a case, explicitly confirm updating it. At today's date the September filing window is closed.
6. From **Alerts**, run a simulated outage. The configured US East subscription is matched. Open **Alert outbox** to review the message or download its `.eml` draft. Delivery is off by default.
7. For a reconciliation demo using the sample PDF unchanged, open the original **Northstar / Jira** claim, prepare, approve, and mark sent (simulated). In **Reconciliation**, select that claim and the confirmed October invoice: $900 matched. The sample's claim reference is `RS-JIRA-SEPT-CLAIM`. To check a newly created case instead, use a later invoice containing that new claim's reference.

## PDF handling

Contracts and invoices accept searchable PDFs up to 10 MB, 60 pages, and 250,000 extracted characters. Encrypted, corrupt, blank-page, and image-only files are refused. OCR is not included. Each original is stored with its SHA-256 and page text in the local SQLite database.

Agreement PDF extraction uses the existing Claude reader only when **Read with Claude** is clicked. Document upload and invoice field suggestions do not call a model. Extracted contract quotes carry page citations, including fragments spanning page boundaries. Uploaded contract text is treated as supplied evidence, not an independently verified official contract.

Invoice suggestions recognize explicit `Label: value` lines (Vendor, Customer, Account, Product, Plan, Invoice number, Billing period, Issue date, Currency, Billing cycle, Product fee, Service credit, Credit period, Claim reference). Other layouts need manual entry and review. Conflicting labels remain empty. Confirmation preserves a snapshot; upload a corrected PDF to replace mistaken evidence. Enter each product's own fee, not a bundled invoice total. A credit amount is positive, with its original claim month and exact claim reference.

## Evidence integration

The provider-neutral push API accepts per-minute monitoring aggregates and support exports, not raw request events. A separate native Datadog connector is documented below; CloudWatch is not connected. Configure `SHARK_EVIDENCE_TOKEN` on the API server and sender. Keep the token in the environment or the ignored `.env`; it is never returned to the browser.

`POST /api/integrations/evidence/{vendor_id}` requires `Authorization: Bearer <token>`. The vendor ID and endpoint are shown in the vendor's integration panel. Repeated identical payloads are idempotent. Records survive restart and say **integration** as their source; authentication does not certify the supplied data as true.

For logs, send JSON with `kind: "logs"`, `period: "2026-09"`, `label: "monitoring-september.csv"`, and `csv` containing the CSV text. The columns are `minute,experience,requests,errors`; times need UTC offsets, and experience names must match the agreement exactly. The saved record reports period coverage and qualifying downtime. Partial coverage remains reviewable evidence, not proof of a full month.

The provided uploader reads a file and sends it to the local API:

```bash
.venv/bin/python scripts/push_evidence.py --vendor vendor-ID --period 2026-09 --csv /path/to/export.csv
```

A support export looks like:

```json
{
  "kind": "ticket",
  "period": "2026-09",
  "label": "Support ticket SUP-123",
  "reference": "SUP-123",
  "url": "https://support.example.com/tickets/SUP-123",
  "opened_at": "2026-09-03T16:00:00Z",
  "incident_start": "2026-09-03T15:00:00Z",
  "incident_end": "2026-09-03T17:30:00Z"
}
```

Send it with `scripts/push_evidence.py --vendor vendor-ID --ticket-json /path/to/ticket.json`. Both incident boundaries can be omitted if unknown. Timing is satisfied only when `start <= opened_at < end`; a late ticket cannot be overridden by a case-form checkbox. The URL is retained as a reference and is not fetched. The integration can run from an existing monitoring/export job; Refund Shark does not create external jobs itself.

## Alert delivery

Outage alerts and case deadlines produce an idempotent outbox when the outbox opens or refreshes. Queueing and background status polling never send anything externally. Drafts without a vendor owner remain previews.

- Email: set `SHARK_SMTP_HOST`, `SHARK_SMTP_PORT` (default 587), `SHARK_SMTP_FROM`, and optionally `SHARK_SMTP_USERNAME` / `SHARK_SMTP_PASSWORD`. STARTTLS with certificate validation is required.
- Slack: set `SHARK_SLACK_WEBHOOK` to a Slack incoming webhook on `hooks.slack.com`, and `SHARK_SLACK_CHANNEL_LABEL` to the channel's recognizable name. Its destination is fixed by the webhook; the URL stays server-side.
- Set `SHARK_ENABLE_DELIVERY=1` only when ready. In the outbox, review the full message and destination, check the review box, then send one message. Simulation labels remain in outgoing demo messages.

Delivery records transport acceptance, not human receipt. A network failure is marked **uncertain**, never automatically retried, because a timeout may occur after delivery. Check the destination manually. Owner changes create a new draft, and the old recipient cannot be sent to after reassignment. No vendor claim submission is performed by this alert feature.

Resolved incidents retire their urgent ticket drafts in favor of follow-ups. Cases no longer needing filing retire old reminders. Actual delivery re-checks the queue before sending. The status watcher currently supports Jira, GitHub, and Cloudflare; adding a vendor profile does not create a new provider feed adapter.

Adapter references: [Python SMTP](https://docs.python.org/3/library/smtplib.html) and [Slack incoming webhooks](https://docs.slack.dev/messaging/sending-messages-using-incoming-webhooks/).

## Invoice reconciliation

Requires a supported claim marked sent and a confirmed later invoice. Checks customer, vendor, product, plan, account, currency, later billing month, plus the credit's claim reference and original claim month when a credit exists. The expected amount is the recorded award when present, otherwise the requested amount. Results: **matched**, **partial**, **missing**, **excess**, or **identity mismatch**.

This is verification against user-confirmed document data, not a bank or vendor API. Results keep the PDF hash and time of comparison. A reconciliation never changes a claim's approval or simulated payment state; inspect the claim to follow up on partial or missing credits.

## Reviewed agreement versions

In **Agreements → Human review**, choose a saved extraction, read the source beside each term, and correct values, quotes, or section references. Every non-null value needs a verbatim supporting quote. Mark terms absent only when the source omits them; explain any absent or still-unknown value. Invalid rules (including overlapping credit tiers) cannot be approved. Unknown or unsupported terms remain review items in the engine.

Enter a reviewer name and review note, confirm the full version, and choose **Approve reviewed version**. This records a new immutable version, its parent, changed fields, and timestamp. A competing browser tab must reload the latest version before approving again. This local reviewer name is a supplied label, not an authenticated identity or legal approval.

To use the version, choose it explicitly in **Vendors → Agreement already read in this workspace** and save. Existing cases keep their old terms until an explicit monthly-case update. The original model extraction is preserved. Saved review versions are separate from the research dataset's human-reviewed gold labels; they do not imply model accuracy has been evaluated.

## Monthly cases and duplicate prevention

Within a vendor's evidence inbox, select all authoritative logs and support tickets for its confirmed invoice month. Logs are merged by UTC minute and covered-experience name. Repeated identical counts are retained once; conflicting counts require choosing the authoritative export. Distinct experiences contribute to the same minute's error rate. The existing engine measures the union, including separate outage windows; it does not add independent full-month credit percentages.

Every qualifying minute must fall inside a supplied incident window with a timely support ticket to confirm that prerequisite. Incomplete timing evidence stays unknown. Public status updates do not fill customer monitoring gaps.

The app uses company, vendor, product, account reference, and month to identify a user case. An identical retry reuses that case. Changed inputs require explicit confirmation and the currently displayed revision. The previous scenario and claim are saved under `/api/cases/{id}/history`; old approvals are invalidated. Submitted cases cannot be replaced or deleted, and another active claim for the same identity is blocked. Bundled example cases are excluded from this workspace uniqueness rule so their independent demonstrations remain usable. Existing legacy duplicates are surfaced rather than deleted automatically.

Limits: at most 100 selected logs, 32 MB of selected source bytes, and 16 MB of consolidated CSV. IDs are account-specific; a log from another vendor setup cannot be used. The credit engine still only prices its supported rules and USD invoices.

## Incident action panel

In **Alerts**, choose **Open action panel** for the affected customer. Check the account, plan, region, and responsible person. Copy the technical support draft and use the vendor support link to file it yourself. Record the resulting reference and opening time; this action does not submit a ticket or send a message.

The optional customer impact start/end are supplied evidence, distinct from the vendor's public status window. An ongoing incident can have a start without an end, but timing stays unconfirmed until both boundaries are known. Saving creates a ticket evidence record when the alert is associated with a configured vendor; directory-only alerts retain the reference in their action panel. Updated ticket details create a new evidence record and leave older records available for audit.

## Native Datadog connector

1. Configure `SHARK_DATADOG_API_KEY` and `SHARK_DATADOG_APP_KEY` in the server environment or ignored `.env`; restart the API. The application key requires `timeseries_query` permission. Keys are never returned to the browser or included in evidence metadata.
2. Under **Vendors → Datadog monitoring**, choose the Datadog site. Map every covered experience to a request-count metric and error-count metric, with tags limiting both to the correct customer's account/service and experience. Use actual count/rate metrics with explicit zero samples when nothing happened; gauge metrics and general server health are not substitutes for customer request counts. Mappings are a human-confirmed interpretation, not proof that the vendor accepts the measurements.
3. Save the mapping. Choose a UTC range (two minutes to 31 days, ending at least two minutes ago), then **Sync Datadog evidence**. Imported records appear in the evidence inbox. Selecting them for a monthly case remains a separate reviewed action.
4. For background imports while the server runs, set `SHARK_MONITOR_INTERVAL=300` (minimum 60 seconds) and enable automatic imports for the vendor. A new mapping begins with the most recent completed hour and a five-minute ingestion delay. Successful imports advance the cursor; this is not an automatic historical backfill. Import older ranges manually. A failed sync pauses automatic ingestion until a successful manual retry or a saved mapping resets it.

The connector uses Datadog's v2 timeseries query API, fixed allowlisted HTTPS API hosts, certificate verification, no redirects, and six-hour chunks. Queries request one-minute count rollups with interpolation disabled. Returned series are matched by query index; missing series, non-integer/negative counts, errors exceeding requests, and coarse/misaligned timestamps are refused. A minute missing either metric for any covered experience is omitted across all experiences rather than filled with zero. No evidence is written until every requested query chunk validates. Source records retain the mapping and requested window, and the UI shows the last successful sync and any error. A monthly CSV must fit the normal evidence upload limit (8 MB); split large ranges into smaller imports.

Historical retention can make one-minute data unavailable. The app refuses coarse data instead of inventing minute-level detail. API failures are not automatically retried within a sync; error bodies and credentials are not surfaced. Live Datadog access remains untested without an account; automated tests use representative mocked API responses, including gaps and rate limits.

Primary references: [Datadog timeseries query API](https://docs.datadoghq.com/api/latest/metrics/query-timeseries-data-across-multiple-products/), [count/rate modifiers](https://docs.datadoghq.com/metrics/custom_metrics/type_modifiers/), and [rollup resolution](https://docs.datadoghq.com/dashboards/functions/rollup/).

## Isolated guided demo

Open **Guided demo** or `/?view=demo`. The five buttons detect a simulated outage, capture the ticket, measure the sample logs, approve and mark the claim sent, and reconcile the unchanged October sample invoice. It uses the real deterministic engine: 62 qualifying minutes, a 10% credit tier on $9,000, and a $900 matching credit. Review the generated draft before the approval step. The replay clock is October 8, 2026; it does not imply that the old September claim can be filed today.

Each run has its own database under the active workspace database's sibling `guided-demo/` directory. Reset creates a fresh run and switches the walkthrough to it; old runs remain on disk and ordinary workspace data is untouched. Progress survives an API restart, and stale step requests are rejected. The walkthrough performs no network calls, model calls, or external delivery. Do not delete your main workspace database to reset the demo.

## Recovery scan

Open **Recovery scan → Your workspace** and start the scan after configuring vendors, confirming invoices, and importing evidence. `GET /api/recovery/scan` refreshes a started scan when inputs or the evaluation minute change; the open page polls it every eight seconds. `POST /api/recovery/scan` forces a scan. No scan timer runs while the page is closed. Datadog's separately configured background ingestion can add evidence, which is picked up on the next scan.

The scanner considers completed invoice months, matching company, vendor, product, plan, account, and billing cycle. It picks the earliest matching later invoice in the same currency for the receiving-invoice cap. Multiple invoices for either choice block the proposal. Duplicate vendor setups for one account/month collapse into one blocked row. All logs and tickets for that vendor/month are considered; contradictory measurements require resolving the exports. A public incident alone never supplies customer downtime.

Expand a proposal to review its non-ticket prerequisites and exclusions, record a reviewer, and optionally add confirmed maintenance windows. Unknown answers stay unknown; ticket timing comes from ticket evidence. Fact review is bound to the contract, invoice, and selected log evidence. New logs or changed source terms invalidate that review; an arriving ticket can complete a previously reviewed proposal without pretending the other facts changed. Reviewer names are local labels, not authenticated identities.

**Accept proposal and prepare draft** rechecks the displayed version and requires an eligible result and a renderable request. It creates or explicitly updates the existing monthly case, preserving earlier revisions and invalidating earlier approval. A repeated acceptance cannot create another case. Saved cases remain unchanged until acceptance; submitted cases are always handled and locked. Only ready proposals count toward **Supported, awaiting review**; missing, ineligible, and handled rows do not. The scanner uses the current UTC clock for ordinary data. Historical replay is refused if any configured vendor is not simulated.

**Isolated portfolio replay** creates three simulated subscriptions with confirmed PDFs and sample logs using the same paths. At the October 8 replay clock, Northstar supports $900, Harbor needs a ticket, and Cobalt has excluded maintenance. Adding Harbor's ticket raises the supported total to $1,380. Acceptance moves that proposal into handled, still requiring claim approval. Reset creates a new database under the main database's sibling `portfolio-replay/` directory and leaves old runs and workspace records intact. Portfolio cases cannot enter ordinary workspace claims. This demonstrates multi-account orchestration in one local workspace, not production tenant isolation or access to Ramp customers.

## Claim challenges and evidence bundles

The home screen's **Start demo** link opens `/?view=recovery&demo=portfolio`. `POST /api/recovery/portfolio/start` creates a replay only if none is started and otherwise returns its current state. Repeated clicks, reloads, and duplicate requests preserve evidence and saved drafts. The three prompts open the refusal, add the ticket, and take the reviewer to Harbor's request or saved challenge. **Reset portfolio replay** remains the explicit fresh-run action.

The **Challenge this claim** tab and saved portfolio cases support seven changes: missing ticket, late ticket, maintenance, missed deadline, duplicated log records, removed customer logs, and a broken credit-table citation. Each runs `evaluate()` on a deep copy after remeasurement. The page shows before/after amounts, outcomes, minutes, clock, changed checks, and relevant original clauses. It never saves the hypothetical scenario or changes approvals.

**Download evidence bundle** exports only the selected case: normalized request logs, scenario, agreement text, typed terms, reproduced decision, and any stored claim/draft. For monthly cases it also includes selected source evidence, linked invoice/contract PDFs when available, and review metadata. It excludes credentials and unrelated workspace records. Legacy fixtures that lack linked original PDFs report that limitation rather than fabricate files.

```sh
uv run python -m shark.bundle verify /path/to/refund-shark-case.zip
```

Run from the checkout that created the bundle after setup. The verifier checks the manifest's file sizes and SHA-256 hashes, compares the relevant local engine file hashes, validates the scenario against its agreement and terms, remeasures bundled logs, and compares the entire fixed-clock decision. It never executes archive code or needs network access. Archives are limited to 30 MB compressed, 100 MB uncompressed, and 250 files; unsafe paths, symlinks, duplicate entries, missing files, incompatible engines, and altered calculations are refused. Nested gzip log expansion is limited as well. Hashes establish internal consistency, not independent provenance, source authenticity, or vendor acceptance. The manifest is not cryptographically signed.

## Reference-label evaluation

The review worksheet and scorer are documented in [EVALUATION_REVIEW.md](EVALUATION_REVIEW.md). Preparing labels does not certify them as human reviewed. The scorer requires explicit review of every field and the exact frozen source. Any resulting score is a post-hoc reference review of one agreement, not a held-out benchmark or production accuracy claim.
