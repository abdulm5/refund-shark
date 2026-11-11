# Public-source research

Researched October 29, 2026. This is machine-researched source material, **not human-reviewed gold labels or a completed extraction evaluation**.

`sla-catalog.json` inventories eight distinct official agreements, with six tuning candidates and two held-out candidates. Reading the held-out agreements to prepare labels does not mean extracting or tuning on their model outputs; no model outputs exist yet.

`source-snapshots.json` records fetch URLs, timestamps, hashes, and private cache paths. Its `text_sha256` values come from the stdlib parser used during research; the repository's parser (trafilatura) produces different text from the same raw bytes, so `data/sources/manifest.json` and `data/gold/manifest.json` record trafilatura text hashes alongside the same raw-page `sha256`. All nine source downloads returned HTTP 200. Raw HTML and text are in `.cache/research/`; that directory must stay gitignored. The ninth document is Atlassian's supplemental claim procedure, not an extra independent SLA.

`real-incidents.json` contains real IDs, update timestamps, historical component changes, links, and provenance hashes for three public incidents. It excludes copied full update bodies. The source feeds are cached privately.

## Recommended real demo case

Use the September 3 Jira US-East incident as a historical review case, evaluated as of October 8. The status-update interval and the postmortem interval differ substantially; neither is customer request-error evidence. Preserve both intervals and identify which one generated an estimate. [Official incident](https://jira-software.status.atlassian.com/incidents/ws0393rxcgyl)

The September compensation window has closed in current-time mode. The replay date must be visible. A simulated invoice or support ticket cannot turn the source into a real approved credit. [Claim procedure](https://support.atlassian.com/subscriptions-and-billing/docs/service-level-agreement-for-atlassian-cloud-products/)

For a current-period real refusal, use the October 1 Confluence scheduling incident. Its reported impact concerns calendar reminders, and its postmortem rules out interactive product impact. That is outside the selected Confluence browser experiences in the agreement. [Incident](https://confluence.status.atlassian.com/incidents/n0kn9r01zyd4), [agreement](https://www.atlassian.com/legal/sla)

The October 21 Jira sidebar incident can demonstrate missing evidence. Its historical component status was degraded performance, so a major-outage-only adapter must not silently count it as contractual downtime. [Official incident](https://jira-software.status.atlassian.com/incidents/1yh59bb4lw20)

## Implementation implications

- Separate incident-level impact severity from historical component status. A current component value is not its outage history.
- Preserve `created_at`, `display_at`, and postmortem timing independently.
- The current GitHub SLA differs from the deprecated source, including tier percentages. Use the current URL in the catalog. [Current terms](https://github.com/customer-terms/github-online-services-sla)
- Generic percentage caps and monthly duration calculations cannot represent every agreement here. Unsupported formulas should yield review.
- Source text extraction is a basic HTML parser pass, not a validated legal-document parser. Review source spans before treating any extracted value as verified.
- Claim timezone, full-period customer coverage, fee allocation, and evidence of prerequisites remain separate inputs. Do not fill them from assumptions.

## Where this research is used

- `data/sources/` holds verbatim line excerpts of the Atlassian SLA and compensation procedure, rebuilt by `scripts/build_source_excerpts.py` from the cached raw pages. `make verify-sources` re-checks them against the recorded snapshot and against the newest `make fetch-sources` download.
- `scripts/create_demo_fixtures.py` normalizes the September 3 and October 21 incidents from `real-incidents.json` with `shark.incidents.statuspage.normalize_incident`.
- The October 1 Confluence incident is listed in the incident feed but is not a demo scenario.
