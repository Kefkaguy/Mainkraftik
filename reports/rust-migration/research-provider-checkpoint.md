# Native research provider acquisition and normalization

The full migration remains active and incomplete. This component is local and is not wired into a deployed worker. Existing paper accounts, credentials, policies, models, and Phase 7/8 research evidence are unchanged.

`research_providers.rs` implements the existing research bar, corporate-action, SEC companyfacts/submissions, and company-identity acquisition operations. Requests retain the original endpoint paths, query values, feed/adjustment identity, page ceilings, duplicate-token refusals, four-attempt HTTP status retry budget, retry delays, timeouts, contact-header requirements, and absence of feed substitution. SEC submissions precede facts with the original 550 ms pause. The returned corporate-action request retains its final pagination token, whereas the bar manifest excludes it, matching the existing Python behavior.

The research bar validator uses `BigDecimal` comparisons and retains all five original decimal amounts for eventual PostgreSQL NUMERIC insertion. Canonical record identities still use the original Python Decimal-to-float convention. This distinction matters: an invalid price just beyond a decimal high can collapse to the same f64 and would pass a floating-point-only validator. Input hashes also preserve signed zero. Completed-session selection uses the exchange calendar and the original close-plus-15-minute availability convention.

SEC availability preserves aware timestamp offsets and fractional seconds, treats naive acceptance times according to New York's original fold=0 semantics, and uses the next calendar day's local midnight when acceptance time is unavailable. This includes DST overlaps and gaps. Fact normalization preserves supported GAAP tags and their order, expected units, original form/required-field exclusions, period checks, accession/source URLs, values, fiscal metadata, and content hashes before acquisition time is added.

The native HTTP transport is limited to HTTPS on the three existing read-only provider hosts, refuses redirects, and supports gzip/deflate responses. Credentials are never included in Debug output or upstream error-body logs. Successful decoded bodies have an explicit 64 MiB ceiling, including streamed responses without Content-Length; oversize data is rejected rather than truncated or substituted. This ceiling is a new documented operational limit, not a trading-policy change. At most four requests can be active, with one SEC facts/submissions batch at a time. Cancelling callers drops their HTTP futures and releases capacity.

## Reproducible checks

The unchanged Python implementation generated immutable references for 12 decimal/bar cases, 11 filing-time cases, 3 GAAP payloads (34 accepted fact rows in total), and 18 acquisition/failure scenarios covering 71 requests. Native tests compare returned payloads, precise request URLs/queries/headers, delays, failure reasons, normalization rows, and canonical hashes. Additional frozen references cover negative-zero JSON/decimal identities. A twelve-caller concurrency test proves the four-request ceiling, cancellation cleanup, and subsequent reuse of capacity. Host validation rejects deceptive hosts, user information, alternate ports, HTTP, and both paper/live brokerage hosts.

These tests use injected fake transport and synthetic source data. They do not contact SEC, Alpaca data, or brokerage endpoints. Full native HTTP wire behavior, Linux execution, and production data-volume coverage remain unverified. Malformed provider payload/type contracts outside these references also remain a release gate.

## Remaining integration

The source-document transactions, price/fact/corporate-action persistence, disk/database growth gates, refresh-run status handling, information enrichment, outcome resolution, and real research-job dispatch still need native implementation and isolated database comparisons. The exact-decimal amounts are available for that persistence layer; it must not insert the rounded canonical JSON numbers as a substitute. No completed-ingestion or full research workflow claim is made here.

This work establishes acquisition/normalization parity only. No profitability result or whole-system performance improvement is inferred from it.

Final checkpoint: `reports/rust-migration/checkpoints/20261120T040332853066Z/manifest.json` — PASS. 30 ordinary workspace tests, 10 explicitly invoked PostgreSQL suites, and 2 explicitly invoked native sentiment suites passed, along with format and strict all-feature Clippy. All 7300 existing archive records verified read-only; all frozen research and original Python source checks passed. Source/output hashes were rechecked after the checkpoint finished.
