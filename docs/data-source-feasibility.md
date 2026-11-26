# Additional information and cohort feasibility

Reviewed during phase 2 on November 12, 2026 (America/Los_Angeles). No subscription
was bought and no new external dataset was ingested. Expected predictive value
below is a research hypothesis, not a measured improvement.

| Source / signal | Coverage and timing | Quality / leakage concerns | Rights, limits, cost and decision |
|---|---|---|---|
| Existing Alpaca SIP daily bars, including possible ETF proxies | Verified locally: MSI/AXON/SPY, 1,308 sessions each. Additional-symbol coverage is unverified. Acquisition times are observed; historical availability is reconstructed. | Split adjustments and ticker identity must be explicit. An ETF is an observable proxy, not evidence of historical sector membership. Existing returns, volume, volatility and SPY context were tested; no established improvement. | Existing authorized feed only. Respect provider response rate-limit headers; never change feed or buy a plan to resolve a 403. Extra daily proxies should be cheap to process, but need a separately declared experiment. [Official bars contract](https://docs.alpaca.markets/us/reference/stockbarsingle-1). |
| SEC submissions and XBRL facts; earnings-filing events | APIs expose submissions and reported financial facts. Recent submission arrays do not cover all historical filings; older archive files may be needed. Exact acceptance times require verification. | Original accession and revision history are necessary. A filing date is not an earnings announcement timestamp. SEC facts alone do not supply historical analyst consensus, guidance interpretation, or an unbiased price universe. | Public read API; identify the client, cache, and stay below the stated ten requests/second maximum. Existing client is slower. Low CPU cost; event/fundamental training remains blocked pending a historical-availability audit. [SEC API](https://www.sec.gov/search-filings/edgar-application-programming-interfaces), [fair access](https://www.sec.gov/about/webmaster-frequently-asked-questions). |
| FRED/ALFRED macro vintages | Vintage-date API identifies revision/release dates; individual series coverage varies and a registered API key is required. No key configured here. | Latest macro values backfilled through history would leak revisions. Intraday release times and per-series rights still need review. Daily/weekly direction benefit is unknown. | Potentially low CPU and no new subscription, subject to series terms and applicable rate limits. Do not ingest until an authorized key and series-level timing/rights are recorded. [Vintage-date API](https://fred.stlouisfed.org/docs/api/fred/series_vintagedates.html). |
| Cboe VIX daily history | Official page offers daily closing values from 1990 onward, updated daily. | Implied volatility adds a different market-risk measurement; it is not itself a direction predictor. Download history does not establish when each revision became available. | Public download is not blanket redistribution permission. Review applicable content terms and archive actual acquisition timestamps before use. Small CPU/storage cost; incremental value remains untested. [Official history](https://www.cboe.com/tradable-products/vix/vix-historical-data). |

## Important price-field clarification

Alpaca's request `asof` parameter identifies the entity behind a ticker across name
changes. It does **not** provide a point-in-time vintage of corporate-action
adjustments. In this application's existing schema, `adjustment_asof` groups the
locally captured split-adjusted snapshot by acquisition date. It must never be
interpreted as vendor proof of what adjusted prices were available historically.
Live records additionally require actual acquisition before cutoff. Old historical
experiments remain reconstructed and exploratory. Raw source requests and payloads
are retained. This limitation is material to every historical validation claim.

## Training universe

The database currently has an independently versioned, **blocked** pooled universe.
Phase-2 membership is explicitly frozen to MSI/AXON and is not changed by watchlist
edits. It is a selected-survivor development sample, not a universal stock model.

A usable future cohort needs immutable security IDs, ticker-interval mappings,
membership effective dates and knowledge dates, source-document hashes, delisting
dates/final observable prices, adjustment/feed policy, and a coverage audit of both
included and excluded securities. Current SEC tickers or a current index list do
not establish this history. Alpaca symbol mapping helps identity resolution but
does not establish historical index membership or complete delisting returns.
No adequate constituent/delisting evidence has been verified within the present
budget. Pooled modeling remains blocked. Prospectively archiving a fixed cohort is
feasible, but it cannot manufacture an unbiased historical cohort retroactively.

## Algorithm feasibility

Histogram boosting and shallow random forests were implemented with one established
scikit-learn dependency, fixed small capacities, deterministic seeds and one thread.
Early stopping is disabled to avoid a default random validation subset; sigmoid
calibration uses a later disjoint block. [Boosting parameters](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.HistGradientBoostingRegressor.html),
[calibration guidance](https://scikit-learn.org/stable/modules/calibration.html).

Separate XGBoost/LightGBM libraries would duplicate this experiment's tree family
without supplying more independent dates. Bayesian shrinkage and volatility/time-series
models may help distributions but require new predeclared tests; a shrunk volatility
bucket reference was tested here. Deep networks, GPU workloads and a broad tuning
search are not justified by roughly one thousand development origins per stock.
