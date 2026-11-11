# Refund Shark — 2:30 voice-over

Record over `refund-shark-demo-1080p.mp4`. The MP4 is intentionally silent. Read at a conversational pace; leave a short beat at the chapter changes. The timestamps match the exported video exactly. You can record each section separately and place it at its start time.

| Time | Narration |
| --- | --- |
| **0:00–0:07** | Your SaaS contracts promise uptime. Refund Shark turns downtime into service-credit requests you can actually prove. |
| **0:07–0:15** | This is the local demo. The customers, invoices, and logs are simulated, and the agreement is Atlassian’s published Jira SLA. |
| **0:15–0:24** | We start with the contract: the plan, downtime definition, credit tiers, and filing deadline. Each extracted term stays attached to a source quote. |
| **0:24–0:34** | One disruption reaches three customers. Northstar supports a nine-hundred-dollar request. Harbor is missing evidence. Cobalt’s maintenance is excluded. Only supported requests enter the total. |
| **0:34–0:43** | Cobalt’s logs contain errors, but every qualifying minute sits inside a confirmed maintenance window. The agreement excludes that downtime, so there’s no supported claim. |
| **0:43–0:51** | Harbor has another blocker: a support ticket was required during the incident. Without that evidence, its potential credit stays out of the total. |
| **0:51–1:03** | Now the timely ticket arrives. Refund Shark checks the evidence and recalculates. Harbor adds four hundred eighty dollars, taking supported proposals from nine hundred to thirteen eighty. We haven’t manually edited a credit. |
| **1:03–1:17** | Here’s the math. Sixty-two qualifying downtime minutes put Harbor in the ten-percent credit tier. Apply that to the forty-eight-hundred-dollar product fee, and the request is four hundred eighty dollars. Every input is visible. |
| **1:17–1:26** | Refund Shark prepares the request with the account, incident, calculation, and relevant clause. Finance can inspect the evidence before doing anything with it. |
| **1:26–1:34** | After review, accepting saves a draft. Approval and submission stay separate. This walkthrough does not send anything to a real vendor. |
| **1:34–1:39** | But the most important question is: does the claim still hold up? |
| **1:39–1:52** | Let’s remove the required ticket. The supported amount drops from four hundred eighty dollars to zero, and the engine explains why. This challenge runs on a copy, so the original draft stays unchanged. |
| **1:52–2:02** | Now import the same evidence twice. The request stays at four hundred eighty dollars. Overlapping measurements don’t create extra downtime, so duplicate evidence can’t inflate the amount. |
| **2:02–2:13** | Download the evidence bundle and verify it offline: file hashes, request logs, and the same four-hundred-eighty-dollar decision. The vendor still decides whether to award the credit. |
| **2:13–2:23** | The workflow also closes the loop. In this separate Northstar simulation, Refund Shark matches the expected nine-hundred-dollar credit to a later invoice and checks the difference. |
| **2:23–2:30** | Models read. Code decides. Finance approves. Refund Shark: a working local demo, with three hundred eighteen passing tests. |

## Recording notes

- Keep the opening confident and conversational. Emphasize “prove” and pause briefly before “Now try to break it.”
- The main portfolio story is Harbor’s **$480** request, which raises the awaiting-review total from **$900 to $1,380**. The final invoice-match shot is a **separate $900 Northstar simulation**.
- A source-quote match is not extraction accuracy. This script makes no accuracy or real-recovery claim.
- Import the video into your editor, record or add your voice-over, and export at **1920 × 1080, 30 fps**. No background audio needs removing.

## Rebuild the edit

The app screens are real captures; only framing, zooms, transitions, chapter titles, and the verification-output presentation were composed in the editor script. The verification output comes from an actual offline bundle check.

1. Run the isolated demo on web port 3012 and API port 8012 with polling disabled and `SHARK_DB=.cache/video-demo.sqlite3`.
2. Run `node scripts/capture_demo_video.mjs` to capture the UI into `.cache/video-capture/`.
3. Run `.venv/bin/python -m shark.bundle verify .cache/video-capture/harbor-evidence.zip > .cache/video-capture/bundle-verification.json`.
4. On macOS, compile `swiftc -module-cache-path .cache/swift-modules scripts/video_encoder.swift -o .cache/video-encoder`.
5. Run `.venv/bin/python scripts/render_demo_video.py --preview` to inspect the storyboard, then `.venv/bin/python scripts/render_demo_video.py` to export the full MP4.

The source media stays in the ignored cache. The exported video, poster, and edit timeline live beside this script. Only the capture and rendering tools are needed to rebuild; the application code is unchanged by the video edit.
