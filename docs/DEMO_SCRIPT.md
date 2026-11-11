# Demo video scripts

Every number below is what the app shows. Say "simulated" whenever a customer, invoice, or log is on screen; the app labels them, and so should the voice-over.

## Recommended final demo (about 3 minutes)

After `make setup`, run `SHARK_WATCH_INTERVAL=0 SHARK_MONITOR_INTERVAL=0 make demo`. Click **Start demo** on the home screen, or open `/?view=recovery&demo=portfolio`. It starts or resumes the isolated portfolio automatically. For recording, use **Reset portfolio replay** to restore the initial $900 state. No key or live integration is needed. Reset creates a separate local run and preserves workspace evidence. The three prompts lead to Cobalt's refusal, Harbor's arriving ticket, and its draft and challenge. [The README GIF](screenshots/demo-walkthrough.gif) is a 28-second sequence of captured app states; [the judge Q&A](JUDGE_QA.md) covers the main boundaries to explain.

**0:00–0:25 — One outage, different contractual outcomes.** Show the three customers and $900 total: “Refund Shark connects the contract to customer evidence and the invoice. These customers and their evidence are simulated; the Jira agreement is real. Northstar supports $900. Harbor lacks a required support ticket. Cobalt's scheduled maintenance is excluded. Only supported requests enter the total.”

**0:25–0:55 — Evidence changes the queue.** Click **Add Harbor's missing ticket**. Show the total becoming $1,380: “The missing ticket arrives. Refund Shark checks its timing, remeasures the logs, and prepares Harbor's $480 proposal. We didn't hand-edit a dollar amount. Cobalt still receives no credit.” Open Harbor and **Prepared request and calculation**: 62 minutes, 99.85648% uptime, 10% of $4,800, with the original clause in the draft. The replay clock is October 8, not today's filing date.

**0:55–1:20 — Finance retains approval.** Check **I reviewed this evidence and request**, then **Accept proposal and prepare draft**. Open the saved case: “Accepting creates a draft; approval and submission are separate. Its amount leaves the awaiting-review total. New evidence cannot silently overwrite an approved or submitted claim.”

**1:20–1:55 — Try to break the claim.** Choose **Remove the support ticket**: $480 becomes $0, with the missing prerequisite and original clause. Then choose **Import the same evidence twice**: $480 stays $480. “These challenges run the actual decision engine on a copy. Duplicate measurements don't double the money; missing prerequisites don't get guessed. The saved draft stays unchanged.”

**1:55–2:20 — Reproduce the number.** Download the evidence bundle and run `uv run python -m shark.bundle verify /path/to/download.zip`. Show `verified: true`, `amount: 480.00`: “This checks the selected evidence files, remeasures the request logs, and reproduces the decision offline. It proves internal consistency; the vendor still decides whether to award the credit.” Have the terminal ready before recording. Avoid displaying credentials or private files.

**2:20–2:50 — Close the loop.** Open **Guided demo** and run its five steps if time permits, or show the saved final reconciliation. This is a separate simulated $900 Northstar case. “The workflow also catches outages while tickets can still be opened, collects monitoring evidence, prepares a reviewed request, and checks a later invoice for the credit. Submission and receipt remain simulated.”

**2:50–3:00 — Close.** “Models read; code decides. The repository includes the runnable local demo, regression tests, reproducible evidence, and clear boundaries on what is live versus simulated.” Don't claim an extraction-accuracy result: human reference review is still pending.

## Original discovery demo (about 3 minutes)

## Before recording

For the new five-step walkthrough, run `make demo`, open `/?view=demo`, and follow the buttons. It uses 62 qualifying downtime minutes and a $900 service credit. **Reset walkthrough** safely starts a fresh isolated replay. The older narrated discovery demo below uses the original 96-minute fixture and reaches the same credit tier.

The newer document-to-reconciliation flow is documented in [INTEGRATIONS.md](INTEGRATIONS.md). It covers PDF upload, invoice confirmation, vendor setup, persistent logs/tickets, the alert outbox, and matching an invoice credit. The narrated script below demonstrates the original discovery and calculation flow.

1. `make setup` once. Use `SHARK_DB=.cache/recording.sqlite3 make demo` for a separate recording workspace if you need fresh claim history; keep your existing database.
2. `make demo`, and open http://127.0.0.1:3000 in a 1440 px wide window.
3. Have these tabs ready (each opens a view directly):
   - Overview: `/`
   - Claim candidate: `/?case=jira-sept-claim`
   - Missing evidence: `/?case=jira-sept-missing-evidence`
   - Refusal: `/?case=jira-sept-maintenance`
   - Live status: `/?view=incidents`
   - Read an agreement: `/?view=agreements`
4. The Agreements page shows "Saved model runs" chips for all eight agreements; `/?view=agreements&read=atlassian` opens the Atlassian run directly.

## 0:00–0:15 · The problem

> "Your SaaS contracts promise uptime. When a vendor misses it, you're owed a service credit, but only if you file inside a short window, with evidence the contract accepts, and often only if you opened a support ticket *during* the outage. Most teams never claim it. Refund Shark finds the credit, proves it, and drafts the request. A model reads the contract; code makes every decision."

## 0:15–0:40 · Overview

- Point at **Ready to claim $900**: "Only supported credits count here."
- Point at **Awaiting evidence $480**: "This is an upper bound that's never added in."
- Point at **Claim windows**: "Sorted by deadline. Each one says file it, or exactly what evidence is missing: request logs, a support ticket, the receiving invoice."
- Point at **Incident impact**: "This is a real Atlassian outage from September 3. It touched two of our (simulated) customers: one has the evidence and is owed $900, the other is waiting on logs."

## 0:40–1:25 · The claim candidate (Northstar, simulated)

- **Downtime budget**: "99.9% uptime allows 43 minutes and 12 seconds a month. Northstar's logs show 96 counted minutes, so it's in the 10% tier."
- **Timeline**: "Green is what counts: minutes where more than 5% of Northstar's requests failed. Their errors started at 15:04, almost an hour before Atlassian's status page reported an outage. Public timelines alone would undercount."
- **Log summary** under the incident: "This is computed from a full month of per-minute logs, 129,600 rows (simulated). One minute sat at exactly 5.0%; the contract says *greater than* 5%, so it doesn't count. 132 minutes of API errors are excluded because API calls aren't a covered experience."
- **Agreement & citations** tab: "Every term is quoted word for word from Atlassian's published SLA, and code checks each quote against the saved page. The minimum credit is marked *not in the agreement*, which is different from *not found*."
- Optional: **Print evidence packet** produces a clean printable page with the calculation, every quoted clause, and the draft.
- Back on the claim's **Overview** tab, **Claim deadline** in the right rail: "The deadline clause names no time zone, so we show the earliest possible reading instead of guessing."
- **Prepare claim**, then **Draft claim**: "Every fact in the draft is rendered from the decision, not written by a model." Then **Approve**, **Mark as sent**, **Match demo credit**: "Sending and the credit are simulated. If the evidence changes, the approval is invalidated."
- Optional: instead of **Match demo credit**, record an award of $450: "Vendors sometimes short-pay. Atlassian gives three business days to ask for a revisit, so the window opens with the deadline and a drafted revisit request."

## 1:25–1:50 · Missing evidence (Harbor, simulated)

> "Same real outage, but no request logs. Refund Shark won't guess: it's *needs review*, with the missing inputs named. It shows what each public window would be worth (the status-update window reaches no tier, the postmortem window would be $480) and counts neither."

- Scroll to **Measure your own logs** and click **Try the sample log**: "Say Harbor's logs arrive. The same engine measures them (62 Downtime Minutes) and prices it: $480 counted, still waiting on the support ticket, the receiving invoice, and an exclusions review. It's a what-if; nothing is saved."

## 1:50–2:05 · Refusal (Cobalt, simulated)

> "Here all 75 error minutes fall inside a confirmed scheduled maintenance window. Section 3(f) of the SLA excludes maintenance, so the answer is no, and the clause behind the refusal is right here."

## 2:05–2:30 · Outage alerts

- Open **Alerts** and click **Run a simulated outage**: "The watcher checks vendor status pages every couple of minutes. This is a labeled drill: a Jira incident in US East."
- "Only two customers are alerted: US East, on a plan with an SLA. Cobalt is in the EU, Juniper is in APAC, and Pinecrest is on Standard, which has no SLA, so each is listed with the reason it wasn't alerted."
- Point at the ticket: "Atlassian only pays if a support ticket is opened *during* the incident. The ticket is already written, by code, ready to paste."
- Leave the drill open and scroll to **Customers from Ramp spend**. Click **Import the sample Ramp business**: "This is a simulated business, in the exact shape of Ramp's public API. Spend shows they pay Atlassian, but not the plan, and we never guess it from a memo. So they're alerted as *possibly affected*, with a ticket, the moment they're imported."
- Click **Resolve the drill**: "When it ends, every alerted customer gets the filing deadline. Nothing is sent outside the workspace."

## Live status (optional, if time allows)

- Open **Incident feed**: "These are the real status pages for the vendors in this workspace, checked just now."
- Click **Preview what an open incident looks like (simulated)**: "When a vendor has an open incident, Refund Shark quotes the requirement, a technical support request *during the incident*, and has a ticket ready to paste for every affected customer. After the outage ends, that prerequisite can't be met any more, so timing is the whole game."

## 2:30–2:50 · Read an agreement

- Open **Agreements**. Click the **Atlassian** saved-run chip (or paste a public SLA link with a key configured). "Across 24 saved runs on eight agreements, 296 of 300 quoted terms appear word for word in the source."
- "Claude extracts each term with a quote. Code verifies every quote and says whether it can price credits under these terms. If it can't, for example a quarterly measurement or a visitor-ratio metric, it names the blocker instead of inventing a number."
- Click **Start a case**, then **Fill demo values** and **Measure and price**: "The form is generated from this agreement's own prerequisites. Upload a request log and it becomes a stored case: $900, with the same evidence checks, a draft, and a calendar deadline." (Say *simulated*: the demo values and the sample log are.)

## 2:50–3:00 · Close

> "Models read, code decides. Every number has a source, and every simulated record is labeled. One command to run it: `make setup`, then `make demo`."

## Don't say

- That any customer, invoice, log, or ticket is real. The agreement text and the incidents are real; the rest is simulated.
- That a credit was received. Every submission and credit is simulated.
- An extraction-accuracy number. No gold labels have been human-reviewed, so the README reports quote verification, run consistency, and agreement with draft labels, none of which is accuracy.
