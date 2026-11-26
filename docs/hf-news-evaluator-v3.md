# Hugging Face financial-news evaluator — v3 paper protocol

Declared 2026-11-15 Pacific, before the next regular session and before any v3
executable selection or orders. Existing MSI/AXON frozen experiments remain unchanged.
This is a paper-only research expansion, not evidence of improved returns.

## Model and artifacts

- Original: mrm8488/distilroberta-finetuned-financial-news-sentiment-analysis,
  revision ae0eab9ad336d7d548e0efe394b07c04bcaf6e91.
- Quantized ONNX: Xenova/distilroberta-finetuned-financial-news-sentiment-analysis,
  revision e6953e30167c7fc3d1ad62f3df31617dd712fbc1.
- Weight SHA-256: 8fcf4873bf5e1679c77205ce621acecdd2cd8dd5baf18e930f10428008ed48ec.
- Quantized weights: 82,763,251 bytes; deployed artifact set approximately 85 MB.
- Upstream model card declares Apache-2.0. Preserve its model card, license text and
  attribution with the pinned conversion. Training uses Financial PhraseBank;
  its noncommercial dataset terms require review before any commercial use.
  This installation is limited to personal experimental paper research and makes
  no commercial-rights claim. ProsusAI/finbert was not selected: current weights
  explicitly use CC BY-NC-SA 4.0 and are larger.

The published sentiment-classification score is not a stock-return prediction score
and is not used as validation of this strategy. No remote model code, pickle,
PyTorch/CUDA stack or automatic weight downloads are needed at runtime.

## Research and entry policy

Keep the v2 timing, numerical models, 70% estimated positive-return threshold,
$25 entries, $100 stock cap, $600 total exposure, 20-position cap and $12 daily-loss
trigger. No borrowing, shorts, options or live account endpoint.

For each of up to 40 liquid news-supported candidates, select the first three
actually acquired recent articles in source order. Deduplicate article IDs across
stocks and score at most 120 texts, using headline plus summary (1,500 characters,
128 tokenizer tokens maximum). All source creation/update/acquisition timestamps
remain in immutable source records. Store original news source ID, model revision,
all artifact hashes, input hashes, article scores and per-symbol aggregates.

Arithmetic means of negative/neutral/positive text probabilities summarize each
candidate's selected articles. When mean negative >=60% and exceeds mean positive
by >=20 percentage points, mark AVOID. This is a predeclared conservative research
veto, not a trained financial-return probability. Otherwise mark REVIEW; REVIEW
is not a buy recommendation. Flag articles mentioning multiple symbols because
article-level tone need not apply to every company discussed.

Pass the sourced sentiment assessment to Groq as research context. News remains
untrusted input; Groq has no order tools and cannot invent symbols/citations or
return probabilities. Keep its bounded prompt, request budget and strict schema.
For HF-enabled v3, missing/invalid local model evidence blocks daily selection.
A genuinely issued BUY additionally requires REVIEW, bullish sourced Groq stance,
the existing numerical probability/return thresholds, volatility, live quote and
all cash/account/execution gates. AVOID or unavailable evidence vetoes entry in
both signal generation and the final entry gate. Sentiment never raises the
numerical probability or lowers its threshold.

Version scheduled research as news-research-pilot-v3-hf and forecasts as
experimental-hf-news-screened-ridge-logistic-v3. Historical previews remain
nonexecutable. Freeze only a genuine premarket selection before its deadline.
Compare prospective outcomes with the preserved reference; do not attribute
any change to sentiment without sufficient independent evidence. Existing
statistical acceptance criteria and minimum evidence remain unchanged.

## Isolation and resource checks

The worker uses a separate internal network shared only with the paper service.
It has no broker/API keys, production database access, published host port or
internet route. Use one process, one inference job, batches of four, one CPU,
384 MiB memory, read-only filesystem/model bind, 16 MiB tmpfs, dropped capabilities
and no-new-privileges. Worker startup verifies artifact hashes before loading.

Local verification: 107 backend tests passed; three explicit synthetic sentiment
fixtures had expected labels. A maximum-sized 120-text batch completed in about
3.73 seconds at approximately 197 MiB, without OOM. This verifies runtime behavior,
not prediction accuracy. Model weights were compared with the Hub LFS hash.

Build images locally and retain prior paper image/config for rollback. Back up
and restore-test both independent databases off-host before rollout. The measured
projection includes 211,185,664 expanded bytes, 69,734,675 compressed new layer
bytes, 84,895,253 model artifact bytes, 206,131,721 peak streaming-import staging
bytes and 134,217,728 working/data allowance. Total projected extra space:
706,165,041 bytes. The gate passed with projected 10,814,006,991 bytes remaining,
above the 10 GiB reserve. Recheck actual space and runtime health after rollout.
No old data cleanup or Docker pruning. Protected game resources and old KefTrade
services are outside the operation.

Rollback the paper image/environment to the saved pre-sentiment configuration and
restart only the owned paper service. Keep all research evidence. Rollback or
emergency disable does not assume positions were liquidated.
