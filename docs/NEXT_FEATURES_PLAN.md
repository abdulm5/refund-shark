# Recovery workflow expansion

Build order: documents → vendor setup → evidence → notifications → reconciliation.

Status: all five phases implemented and verified on November 3, 2026. See [the local walkthrough](INTEGRATIONS.md) for setup and demo steps.

## 1. Documents
- Upload a contract or invoice PDF into the local workspace. Enforce file, page, and text limits; refuse encrypted, corrupt, and image-only PDFs with useful errors.
- Preserve the original PDF, its SHA-256, and text by page. Contract reads reuse the existing extractor and show page references for matched quotes.
- Suggest invoice fields only from explicit labels. A person reviews and confirms invoice identity, dates, currency, fees, and credit lines before use.
- Ship clearly labeled sample PDFs and support viewing/downloading saved documents.

## 2. Vendor setup
- Persist a company/vendor profile with product, plan, account, region, billing cycle, owner, linked agreement, and confirmed invoice.
- Show a checklist and allow saving incomplete setup. Only matching, confirmed inputs can make setup complete.
- Add configured subscriptions to the existing outage matcher and restore them after restart.
- Use these saved inputs and evidence to create cases through the existing decision engine.

## 3. Evidence collection
- Keep request logs and support-ticket references attached to the vendor and billing month.
- Accept a CSV upload and an authenticated integration POST using a server-side bearer token; document the payload so monitoring exports can push per-minute aggregates.
- Validate logs using the agreement's actual metric. Preserve hashes and provenance. Evidence creates a new case; existing approvals never change silently.
- Support ticket metadata includes reference, URL, opened-at time, and incident window; known timing failures remain failures, and unconfirmed timing remains unknown.

## 4. Actionable alerts
- Build a persistent outbox from outage alerts and claim deadlines, routed to the configured owner.
- Provide previews, downloadable email drafts, delivery history, and explicit per-message delivery through SMTP or a configured Slack incoming webhook.
- Credentials stay in environment variables. No delivery during development, no automatic sending, and no silent retries after an ambiguous failure. Deduplicate queued messages.

## 5. Reconciliation
- Compare a confirmed later invoice's credit line with a submitted claim using company/account, vendor, product, claim period, reference, currency, and amount.
- Report matched, partial, missing, excess, or identity mismatch, with the expected/observed amount and evidence document.
- Keep invoice verification separate from simulated claim submission. Do not claim that a bank payment or vendor API was verified.

## Interface
Keep the established Inter typography, pale sidebar, white workspace, lime primary actions, and compact tables. Add Vendors, Documents, and Reconciliation to navigation; place notifications beside Alerts. The signature interaction is a visible setup checklist beside the actual source evidence. Use labeled forms, keyboard focus, responsive stacking, useful empty states, and disabled actions with explanations.

## Verification
- Regression suite plus integration tests for document parsing, invoice confirmation, vendor matching/restoration, evidence authentication and timing, outbox idempotency and mocked delivery, and reconciliation mismatches/partial awards.
- TypeScript check and production build.
- Inspect rendered sample PDFs. Browser smoke checks when the supported browser surface is available.
- Update README and demo instructions with the complete local flow, configuration, and remaining limits. No external messages, paid model calls, or account connections are required for verification.

### Completed checks
- All 258 Python tests pass, including 31 recovery workflow tests. Email and Slack transports were verified with mocks; no external message was sent.
- TypeScript validation and the production Webpack build pass (`npm run build -- --webpack`). The default Turbopack production build encountered a local worker-port permission error.
- All four sample PDF pages were rendered and visually inspected.
- Browser verification covered invoice review, a complete vendor setup, 62 qualifying downtime minutes, a support-ticket reference, a $900 supported demo case, the owner-routed alert preview with sending disabled, and a $900 match against a later invoice.
- The preview uses a separate local QA database. Claim submission remains simulated; live Ramp access, paid extraction calls, and real delivery were not exercised.
