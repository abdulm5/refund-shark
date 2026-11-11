# Standard walkthrough — voice-over guide

Use **refund-shark-standard-demo.mp4**. It is a continuous recording of the working app, about 2:58, with normal clicks and scrolling. There are no added title cards or camera zooms. The audio is empty so you can record your own narration.

The timing below follows the actions in the video. These are suggested words; you can describe the same steps in your own voice.

| Time | Suggested narration |
| --- | --- |
| **0:00–0:16** | This is Refund Shark. It connects service-level agreements, outage evidence, and invoices to prepare service-credit requests. I’ll start the local demo. These are simulated customers and records, using Atlassian’s published Jira agreement. The recovery scan shows three different outcomes. |
| **0:16–0:28** | Harbor is missing a support ticket that the agreement requires during an incident. Cobalt has a different issue: its downtime falls inside excluded maintenance. Neither amount enters the supported total. |
| **0:28–0:41** | Now I’ll add Harbor’s missing ticket. The app checks the evidence and recalculates. Harbor supports a four-hundred-eighty-dollar request, bringing the total awaiting review from nine hundred to thirteen eighty. Cobalt still doesn’t qualify. |
| **0:41–1:00** | Here’s how Harbor’s amount was calculated. There are sixty-two qualifying downtime minutes. That puts the month in the ten-percent credit tier. Applying that to the forty-eight-hundred-dollar product fee gives four hundred eighty dollars. Below that is the prepared request, including the calculation and the original contract clause. |
| **1:00–1:13** | After reviewing the evidence, I can accept the proposal and save a draft. That does not approve or send it. The saved case also lets us test what happens when the evidence changes. |
| **1:13–1:30** | Remove the required ticket and the supported amount drops to zero. The app explains what’s missing. Now import the same evidence twice: the amount stays at four hundred eighty dollars. These challenges leave the saved draft unchanged. |
| **1:30–1:38** | I can download the evidence bundle, too. It includes the records needed to check the files and reproduce the calculation offline. |
| **1:38–1:55** | The Documents feature handles contract and invoice PDFs. Here’s a sample invoice with a credit. It suggests fields for review and keeps the extracted text by page, along with the original file. Those invoice values need confirmation before they’re used. |
| **1:55–2:10** | Agreements shows the extracted terms beside their source quotations: the covered product, uptime commitment, credit tiers, exclusions, and deadline. This is a saved model run. Matching a quote checks its source; it isn’t a guarantee that the interpretation is correct. |
| **2:10–2:21** | Vendor setup also includes the Datadog connector. Request and error metrics are mapped to the covered experiences. This demo has no live Datadog credentials, so we’re looking at the setup screen. |
| **2:21–2:35** | Finally, the guided walkthrough shows the complete lifecycle in a separate Northstar simulation. It detects an outage, captures the timely support ticket, and measures the monthly evidence. |
| **2:35–2:48** | That evidence supports a nine-hundred-dollar request. The draft can then be approved and marked submitted inside this simulation. Nothing is sent to a real vendor. |
| **2:48–2:58** | The last step checks a later invoice and matches the expected nine-hundred-dollar credit. That completes the workflow, from the contract and outage evidence through to the invoice credit. |

## Recording details

- Full-frame **1920 × 1080**, **30 fps**, silent H.264 MP4, exported from the browser's 1280 × 720 screencast.
- The main claim is Harbor’s **$480** request. The final guided walkthrough is a **separate $900 Northstar example**.
- Customer records, tickets, invoices, submissions, and credits are simulated. No live Datadog sync or real reimbursement is shown.
- The pointer follows the actual browser mouse events; page changes, scrolling, loading, and pauses retain their recorded timing.

To reproduce: start an isolated demo on ports 3012/8012 with polling disabled, run `node scripts/record_standard_demo.mjs`, then `.venv/bin/python scripts/encode_standard_demo.py`. The macOS encoder from `scripts/video_encoder.swift` must be compiled first. Recordings and the preparation workspace are kept in `.cache/standard-video-capture/` and `.cache/standard-video-demo.sqlite3`.
