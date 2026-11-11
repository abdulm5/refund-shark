# Claim reliability and guided demo

Implementation order: contract review → monthly cases → incident actions → monitoring → guided demo.

1. **Contract review.** Keep the extraction immutable. Review values alongside the original text and PDF page references; validate corrections and their quotes. Approving creates a numbered version with reviewer, reason, changes, and parent. Existing vendors/cases keep their pinned version until explicitly changed.
2. **Monthly consolidation.** Select multiple logs and tickets for a company/account/product/month. Merge identical minute/experience rows once; reject conflicting counts. Reuse the monthly case, show an explicit update confirmation when evidence changes, and invalidate approvals when updated. Block changes after simulated submission and prevent another submitted claim for the same account/month. Preserve prior case revisions.
3. **Incident action panel.** Show affected subscription, owner, support destination, ticket draft, and action checklist. Record the real ticket reference/opened time locally. Keep uncertain customer-impact windows unconfirmed, and never equate a public status window with proof of customer impact.
4. **Monitoring connector.** Use Datadog, as selected by the user. Read mapped request/error count metrics through the official API with one-minute resolution; retain source metadata and sync status. Reject coarse, interpolated, conflicting, or impossible samples and preserve missing data as gaps. Server-side credentials only; no live call without configured access. A configurable background sync is off by default.
5. **Guided demo.** Run an isolated, clearly simulated walkthrough using the existing engine and sample files. Step through detection, ticket capture, measurement, approval/submission, and invoice matching. Reset only this walkthrough, preserving workspace records. No external calls or delivery.

## Design
Keep the existing Ramp-inspired palette (white #ffffff, pale sidebar #f7f8f3, ink #252a21, lime #e5f58a, muted #7c8373), Inter headings/body and tabular figures. Use source-and-value columns for review and one visible five-step timeline for the demo. Reuse existing forms, panels, and focus states; stack columns on narrow screens.

## Verification
Meaningful regression tests cover immutable versions, invalid citations/rules, monthly deduplication and conflict handling, submission locks, incident ticket timing, Datadog payloads/credentials/gaps/retries, and demo reset isolation. Run the full Python suite, TypeScript, a production build, and browser flows. Mock external delivery and monitoring; do not spend API credits or send messages during verification.

## Completed — November 4, 2026

All five features are implemented. The full Python suite passes (288 tests), TypeScript checks pass, and the production build succeeds with `npm run build -- --webpack`.

Browser verification covered saving an approved contract version, linking it to a vendor, updating the existing monthly case to a $900 credit without adding another case, saving a simulated incident ticket with timing left unconfirmed, saving a Datadog mapping without credentials, and completing all five isolated demo steps through a $900 invoice match. The guided demo also renders at a 390-pixel viewport. Evidence: `docs/screenshots/guided-demo.jpg`.

Live Datadog sync still requires server-side credentials and account-specific metric mappings. Connector tests use mocked responses; automatic sync is disabled by default. No messages, real claims, or paid model requests were sent during these checks.
