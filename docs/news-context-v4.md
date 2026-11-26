# Balanced news context v4

The prior Groq prompt silently removed candidates from its tail when its 12,000-character
budget was exceeded. A deterministic replay of the saved v3 server preview found that
only 20 of 40 screened stocks and 16 distinct headline sources reached the model.
The pre-truncation prompt was 23,321 characters.

The new context retains all screened candidates, up to 40. It represents up to three
recent source references per stock, deduplicates headline text shared across stocks,
and uses only compact sentiment probabilities/flags instead of forwarding full
per-symbol dictionaries. A common headline-character limit applies to every article;
remaining space can carry summaries. If extreme source-ID overhead requires fewer
sources, the per-stock limit is lowered uniformly. If even one usable source per
stock cannot fit, the request fails visibly instead of dropping stocks.

The same saved preview now presents all 40 stocks and 73 distinct headline sources
in 11,995 characters. This is improved input coverage, not evidence of better return
predictions. Truncated text carries an explicit ellipsis; headline and summary truncation remain versioned. All
presented stock symbols, article IDs, per-stock references, eligible article counts,
truncation limits/counts and omitted-reference counts are preserved in model metadata.

Groq can cite only references actually presented for that stock; real but unseen
article IDs are rejected. Sentiment remains text classification, not return
probability. Strict structured outputs, no order tools, 20-pick cap, three attempts
per day and 60 per month remain unchanged. The HF research version is
news-research-pilot-v4-hf-balanced-context. No model threshold, execution guard,
forecast implementation or frozen MSI/AXON code was modified by this change.

Six new context tests and the existing news/sentiment tests passed (20 total).
They cover 40-candidate coverage, three-headline preservation, deduplication,
Unicode/escaping budget pressure, fail-visible overflow, choosing a tail candidate,
and rejection of a real source not presented as that stock's evidence.
The replay used a previously restored off-host snapshot on the local PC, with no
new Groq request, broker request, VPS operation or executable forecast.
