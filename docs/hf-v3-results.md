# Hugging Face evaluator v3 — actual deployment result

Installed and enabled for personal experimental paper research on 2026-11-15
Pacific. The isolated quantized DistilRoBERTa worker and new paper image are healthy,
with zero restarts/OOM events. The website and old research service images were
not changed. The existing trading system and protected game resources were untouched.

The real research preview processed 1,000 news articles (pagination capped), screened
40 liquid candidates and classified 75 unique recent articles: 26 positive, 33 neutral,
16 negative. Seven candidates received a negative-news AVOID flag. The Groq shortlist
contained 20 names. Seventeen had usable numerical histories; CBRS and SPCX lacked
sufficient history, and NBIS failed the missing-session integrity check. No missing
prices were interpolated. Neither preview nor the diagnostics created orders or a
Monday execution selection.

All seventeen usable 1-/5-day heads trained and produced finite outputs. Their
one-day estimates were approximately 44–53%; none passed the combined 70% entry
gate. The numerical outputs are unchanged by sentiment: positive text classification
never becomes a stock-return probability. Zero qualifying buys is a valid result.
These are preview diagnostics, not actual Monday issuance or evidence of profitability.

Research took 33.59 seconds. The reused-history numerical diagnostic took 5.64
seconds. The worker's observed peak was 213,045,248 bytes under its 384 MiB cap.
Free disk after deployment was 11,143,110,656 bytes, above the 10 GiB reserve.
Both independent databases had successful off-host restore checks before rollout;
post-rollout exports/restore receipts are retained in the receiving PC's ignored
.local/off-host directory. Model weights and their pinned hashes are retained off-host.

107 backend tests passed, including sentiment provenance, tamper rejection and
proof that the local model cannot override the return threshold. Three synthetic
text fixtures and a 120-text bounded runtime batch passed. These are runtime/safety
checks, not validated sentiment accuracy on current news or validated trading gains.
The original MSI/AXON experiment implementation hashes are unchanged. No browser
or UI inspection was performed.

The schedule remains 04:30 Pacific research, 06:00 shortlist freeze, 06:15–06:25
numerical issuance and 06:35–07:00 qualifying entries. Retain the 70% estimate,
$25 entry, $100 stock cap, $600 total exposure, 20-position cap and $12 daily-loss
trigger. There is no profitable-trading guarantee; all execution is paper-only.

See hf-news-evaluator-v3.md for model identity, license/data-use limitations,
isolation, negative-news policy and rollback. See hf-v3-live-validation.json for
source IDs and individual numerical/text assessments. No fresh independent
performance improvement has been demonstrated. Next research should compare
frozen v2/v3 prospective cohorts, including skipped names and transaction outcomes,
rather than shopping for models that merely emit larger confidence scores.
