# Gold labels and the evaluation manifest

`manifest.json` freezes the extraction evaluation set: eight distinct agreements, six for tuning and two held out (Google Cloud DNS and Expo EAS). Each entry records the source URL, the retrieval time, the SHA-256 of the raw page, and the SHA-256 of the text the repository's parser (`shark.extract.fetch.html_to_text`, trafilatura) produces from it. Raw pages stay in the ignored `.cache/`; the evaluator refuses a raw page or parsed text whose hash differs from the manifest.

**No document has human-reviewed gold labels.** Live extraction has been run: three runs for each of the eight documents, with the configuration frozen before the held-out batch (`frozen_config` in `manifest.json`, runs in `data/extractions/`). Without reviewed gold, `make eval` reports label-free measures (quote verification, run-to-run consistency) and agreement with the Atlassian draft labels, and marks every document *not scored*. `atlassian-jira-premium.draft.json` preserves the original machine-drafted Jira Premium terms. The other seven documents are `not_labeled`.

The [November 10 AI-assisted audit](../../docs/AI_REFERENCE_REVIEW.md) corrected three fields in the application fixtures and saved a separate proposal in `data/reviews/`. It did not replace these original draft labels or mark them human-reviewed. Before the demo corrections, the four evaluation scenarios were captured in `atlassian-scenarios.pre-audit.json`; the manifest records its hash, and historical decision comparisons read it instead of changing demo fixtures. The original saved evaluation report is preserved.

## Adding a gold document

1. Save an `SLATerms` JSON (schema in `shark/models.py`) for one product and plan. Leave terms the source does not state as `value: null`; mark a term the source plainly lacks (for example, no minimum credit) with `absent: true` and a `missing_reason`.
2. Have a person check every value, tier boundary, prerequisite, and quote against the full page, then set `review_status` to `human_reviewed` and `gold_path` to the file. Keep all plans from one document in the same split.
3. Save three independent live extractions (`shark extract ... --output`) as `extraction_paths`, and optional `scenario_paths` for decision agreement.

## Running the held-out batch

Tune only on the six tuning documents. Then set `frozen_config` (`frozen: true`, `model`, `prompt_version`, `schema_hash`) and run one held-out batch of three runs per held-out document, keeping every run, including failures. Do not tune after seeing held-out scores. `python -m eval.run_eval` scores saved outputs without calling a model and reports, per document: worst field-value accuracy, worst quote-match rate (stated terms only), exact credit tables, priced decision agreement, agreement where both paths returned review (counted separately), and false-positive eligible decisions. Runs answered by a fallback model are rejected as runs of the frozen model.

## Notes on the saved runs

- The 18 tuning runs were saved before `raw_output` (each request's raw response) was added to the record; the six held-out runs have it. Cloudflare runs 1 and 2 also carry an older, less specific conversion-note format. Their inputs (prompt `cited-terms-v4`, schema hash, model, effort) are identical to the frozen configuration.
- `eval-report.json` is the evaluator's output with the raw vendor pages cached; `tests/test_eval.py` checks it still matches a fresh run whenever those pages are present.
