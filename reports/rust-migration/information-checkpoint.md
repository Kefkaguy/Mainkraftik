# Native private news and filing ingestion

The full migration remains active and incomplete. No trading service, paper policy, brokerage credential, account, or deployed website was changed. This module has no order-execution authority.

## Implemented

`information.rs` ports private research news ingestion, news metadata/flags, immutable information identities, and filing-event ingestion from archived SEC submissions. The research news request remains distinct from the paper engine's discovery request: fixed MSI/AXON filter, ascending historical interval, original endpoint/headers/query values, forty-second timeout, existing provider retries, 120 ms page pause, repeated-token refusal, and 600-page ceiling. Default intervals and explicit interval validation are preserved.

Each valid page commits its source document and metadata in one transaction. Earlier committed pages survive a later provider/metadata failure, which records partial rather than complete coverage. A failed first page records unavailable coverage. A database failure propagates rather than being mislabeled as a completed provider fetch. Metadata replays and revisions preserve their original content identities; acquisition does not rewrite an existing information version.

No full article bodies or summaries are retained in source documents or information rows. Sources hold the original request, extracted metadata, and page hash; each article retains its original payload hash, private headline truncated to 1000 Unicode characters, source/author/URL, timestamps, symbols, and flags. The synthetic oracle includes body/summary marker strings specifically to verify their absence from persisted documents.

Text flags preserve the existing earnings/contract/acquisition patterns, guidance precedence and negation, whitespace normalization, and Python's letter/number/underscore word-boundary behavior. Matching bounds each guidance search to the original 55-character gap plus its target/boundary. It does not repeatedly scan an entire long headline for every matching verb.

Archived filing ingestion preserves the first eligible MSI/AXON ticker, supported SEC forms, acceptance timestamps, conservative next-New-York-midnight fallback, original acceptance string, earnings item flag, acquisition/source reference, version identity, and original versions-seen counter. Replaying submissions does not duplicate immutable filing rows or pretend older archive coverage is complete.

The `official_ir` metadata persistence identity also matches the original: headline/flag/publication identity is separate from acquisition/update timestamps. Tests compare both repeated metadata and a changed publication identity. This does **not** implement the full official-page HTTP/HTML/robots workflow yet.

## Evidence

251 original text-flag fixtures and nine news metadata/refusal fixtures pass exact comparisons. They include Unicode boundaries, guidance priority, headline truncation, nanosecond input truncation to Python microseconds, missing keys, and invalid/naive/reversed timestamps.

In a disposable localhost PostgreSQL database, the unchanged Python application and Rust run against independent schemas. Twelve news cases compare results, exact requests, delays, and resource-gate calls; they cover replay, revision, repeated tokens, authorization/connection failure, missing metadata, partial metadata failure, empty pages, default bounds, invalid intervals, and the 600-page ceiling. Four persisted evidence families are compared: source documents, information versions, fetch coverage, and filing events. Filing replay and official metadata identity cases are included. Random source UUIDs are compared through source content hashes; equivalent timestamp representations are allowed.

A second-article database exception rolls back the source and first article from that page, with no false fetch-completion record; clean retry succeeds. Cancellation while page two waits preserves page one's committed data without adding a complete-coverage record. Restarting replays the first page safely and finishes with two unique article versions. Original immutable triggers remain enforced.

## Remaining gates

Official IR redirect/size handling, robots semantics, HTML extraction, publication enrichment, market-context refresh, and complete research-job dispatcher assembly still need native implementation and differential evidence. Nonstandard ISO timestamp formats, malformed external payload/type/error contracts, and long-backfill storage growth require further validation. The original per-operation 64 MiB resource projection remains in place; this checkpoint does not establish a per-page database growth bound. Native HTTP wire behavior and Linux verification also remain open.

All inputs are synthetic and transport is fake. No SEC, Alpaca data, or broker endpoints were contacted. Original Python sources, archived predictions/models, and authoritative Phase 7/8 negative results are preserved. These checks establish component behavior, not profitable trading performance.

Final checkpoint: `reports/rust-migration/checkpoints/20261120T044619702926Z/manifest.json` — PASS. 32 ordinary workspace tests, 13 explicitly invoked PostgreSQL suites and 2 native sentiment suites passed. Formatting and strict all-feature Clippy passed. All 7300 archived records, 40 frozen Phase7 files, 38 research-preservation files and 106 original Python source hashes verified unchanged. The original information oracle is retained in this checkpoint. Source/output hashes were independently rechecked after completion.
