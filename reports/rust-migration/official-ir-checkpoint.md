# Native official publications and information refresh

The full Rust migration remains active and incomplete. All work is local. No production process, trading policy, account, key, model, prediction, or archived research record was changed. No external IR, data-provider, or brokerage endpoint was called by the test workflows.

## Implemented

`official_ir.rs` implements the existing three-host HTTPS allowlist, explicit redirect validation, four-request redirect budget, allowed 200/404 responses, 3 MiB page ceiling, listing/article collection, robots decisions, twelve-link collection limit, and exact publication enrichment. User-agent fallback, request order, page pauses, source hashes, metadata identities, and original unavailable/complete results are preserved in the tested workflows. Native HTTP uses no automatic redirects and enforces the size ceiling on streamed decoded bodies as well as declared length. No brokerage credentials are sent through this transport.

The native information refresh now composes news, archived SEC filings, official IR, and XLK/XLI price refresh in the original order. Both ordinary 45-day news refresh and explicit historical backfill are compared against Python, including original resource projections, provider requests, corporate-action unavailability, returned results, and persisted source/market data.

The HTML adapter uses html5gum 0.9.1 token events and raw source spans with legacy stack/extraction behavior. It does not substitute a browser DOM tree. Frozen entity tables preserve the reference attribute/text distinction, duplicate attributes keep the last value, raw newlines survive decoding, ASCII-only whitespace collapse is separate from text stripping, and script/style/textarea/title handling matches the covered Python 3.14 behavior. Text extraction is restricted to each element's stored subtree range. Parser work runs in two bounded blocking slots whose permits survive caller cancellation.

The robots adapter preserves Python RobotFileParser's wildcard-agent behavior, first matching rule, first default group, blank-line conventions, percent/query normalization, and literal path treatment. It does not substitute a longest-match/wildcard policy from a different robots implementation. URL joining preserves Python 3.14 identity details including explicit default ports, raw Unicode/spaces, duplicate path separators, and empty query/fragment markers.

HTML entity reference data, Python/BeautifulSoup notices, and the tokenizer license are retained under `third-party`. Official pages store headlines, structured metadata, page hashes, and guidance-evidence hashes; paragraphs and article HTML are not stored in source documents.

## Verification

Twenty-three legacy HTML projection cases include nested/unclosed elements, comments, CDATA, entities, duplicate attributes, text-container exclusions, raw CRLF, Unicode whitespace, script escape sequences and out-of-range numeric attributes. Seventeen also compare publication-time extraction. Another 192 robots decisions and 22 URL joins match the original Python implementation.

In separate schemas of one disposable localhost PostgreSQL database, the unchanged Python application and Rust execute twenty captured operations. These cover listing ingestion/replay/edit, publication enrichment/replay, robots blocking, allowed/forbidden redirects, missing/oversize pages, exact/over-limit bodies, UTF-8/Latin-1/Windows-1252 decoding, default refresh, and backfill. The reference includes 161 IR requests and ten market/news requests. Results, request order, headers/queries, pauses, resource projections, source/metadata identities, availability fields, and four persisted evidence families are compared. Synthetic privacy markers are absent from stored records.

An initial parser mismatch identified Python's textarea treatment; another identified ASCII-only raw whitespace collapse. A separate retained encoding failure exposed Python's replacement of undefined Windows-1252 bytes. Additional standalone probes against the built native library found a script escape discrepancy and an out-of-range numeric-attribute discrepancy after the first complete checkpoint. Both were independently compared against Python, retained in `ir-boundary-first-failures.json`, fixed, and added as new frozen cases. Existing expectations were not changed. The complete checkpoint was rerun after these fixes.

A listing transaction fault after earlier article writes rolls the source and all new listing metadata back. Retry records the recovered version once. Twelve callers competing for two parser slots are cancelled while blocking work remains active; a subsequent caller cannot overlap those two workers. Capacity recovers after the blocking work actually ends. The test's release guard also prevents a failed assertion from leaving blocked test threads alive.

## Remaining limits and release gates

The original ingestion timestamp is sampled after the listing fetch and before article fetches. This convention is preserved for parity; it is not proof of each article's completion time. Publication enrichment samples its own later acquisition time, and future publication timestamps remain unavailable until that future time. A stricter article-completion convention requires a separately versioned correction and validation rather than rewriting existing history or silently changing this port.

The integration transport is fake. Actual HTTP/TLS/compression/header behavior, all possible malformed HTML or timestamp formats, less common character encodings, Linux execution, and full-service resource measurements remain unverified. The original backfill resource gate also remains per operation, not a demonstrated per-page growth bound. Further validation and data-quality hardening are required before release.

Durable job-dispatch assembly, frozen prospective dependencies, research/training workflows, backup/restore/rollback, complete runtime roles, full benchmarks, and the final cutover remain unfinished. Phase 7/8's negative trading-edge conclusions remain authoritative; these tests provide no profitability evidence.

Final corrected checkpoint: `reports/rust-migration/checkpoints/20261120T052015129455Z/manifest.json` — PASS. 34 ordinary workspace tests, 14 explicitly invoked PostgreSQL suites and 2 native sentiment suites passed. Formatting and strict all-feature Clippy passed. All 7300 archived records, 40 frozen Phase7 files, 38 research-preservation files and 106 original Python source hashes verified unchanged. Original IR workflow oracles are retained in the immutable checkpoints. Source/output hashes were independently rechecked after completion. The earlier pre-boundary checkpoint remains retained as intermediate evidence.
