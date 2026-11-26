# Phase 3: source expansion, bounded tuning and frozen prospective shadow

The reference model is preserved. No candidate established a predictive advantage. A single experimental shadow is enrolled; it has no validated status or trading authority.

## Actual coverage

| Provider | Stock | Unique records | Versions | First recorded date | Last recorded date |
|---|---|---:|---:|---|---|
| alpaca_news | MSI | 471 | 471 | 2021-07-01 | 2026-11-11 |
| alpaca_news | AXON | 725 | 725 | 2021-07-06 | 2026-11-12 |
| official_ir | MSI | 4 | 8 | 2026-10-05 | 2026-11-12 |
| official_ir | AXON | 5 | 10 | 2026-10-21 | 2026-11-12 |

The 1,188 unique news articles include overlapping stock tags (471 MSI, 725 AXON). All 24 response pages completed. Store creation/update times, article IDs, symbols, source/author, private headlines, source hashes and acquisition times, not full bodies. Historical features exclude versions updated after their cutoff; archive completeness/original vintages remain unproven.

There are 312 as-filed SEC event records: 146 MSI and 166 AXON, including 39 and 43 8-K item 2.02 earnings-filing proxies respectively. These are not a fabricated earnings calendar or analyst-surprise dataset. Existing 4,405 financial-fact versions remain intact. XLK and XLI each have 1,308 SIP split-adjusted sessions, 2021-08-30 through 2026-11-12. They are fixed context proxies, not historical sector-membership evidence.

Official IR has nine unique observed announcements (five AXON, four MSI). Exact MSI publication metadata was appended where available; unknown edit histories retain actual acquisition as availability. IR historical ablation is blocked. Coverage is a current-page capture, not a complete historical archive.

## Features and tuning

Thirty-six versioned fields cover fourteen existing technical variables; ETF/counterpart returns and volatility; lagged news counts/activity; as-filed revenue, operating-margin and operating-cash-flow changes; filing/earnings-filing timing; and conservative IR guidance/contract/acquisition flags. Unavailable values stay null in source snapshots. Models use training medians plus missing indicators. News windows end at 19:00 New York; observed live inputs must be acquired by 20:00.

The declared maximum was 96 trials. Eighty completed across twenty studies; sixteen IR trial slots were blocked rather than searched. Ridge/logistic paired heads and histogram boosting each had four trials per horizon/eligible feature group. An inner 60/20/20 split entirely inside outer training selected parameters. Outer 60/20/20 blocks have five-session purges and contained labels. Nomination used only the inner objective, before outer-result inspection. Frozen replay preserved all eighty trials and forty-eight result records (forty fitted, eight blocked).

All outer evidence is exposed historical development, before the sealed 2025-11-14 boundary. No development result is independent confirmation. No comparison interval had a positive lower improvement bound. There are 199 one-day / 195 five-day evaluation dates per stock, about nine 20-session blocks and 39 non-overlapping five-day windows. Exact effective independent sample size is unknown. Four-comparison 98.75% intervals do not correct the whole model search.

## Frozen nominee: histogram boosting with market context

| Stock | Horizon | Reference MAE | Candidate MAE | Reference Brier | Candidate Brier | Candidate log loss | Interval coverage |
|---|---:|---:|---:|---:|---:|---:|---:|
| AXON | 1 | 1.9878% | 1.9850% | 0.24784 | 0.25369 | 0.70054 | 77.9% |
| AXON | 5 | 5.2054% | 5.1914% | 0.24726 | 0.25431 | 0.70238 | 82.6% |
| MSI | 1 | 0.8900% | 0.9074% | 0.24929 | 0.24958 | 0.69230 | 75.9% |
| MSI | 5 | 2.4779% | 2.5234% | 0.25146 | 0.27014 | 0.73585 | 75.9% |

The nominee is the best under the declared inner-selection rule, not a demonstrated improvement over the reference. Its Brier point estimates were worse on all four outer stock/horizon slices. Small AXON MAE gains were not statistically established.

## All fitted feature comparisons

| Family | Group | Stock | Horizon | MAE | Brier | Log loss | Coverage |
|---|---|---|---:|---:|---:|---:|---:|
| histboost | all | AXON | 1 | 2.0180% | 0.25558 | 0.70437 | 77.9% |
| histboost | all | AXON | 5 | 5.3703% | 0.25649 | 0.70650 | 80.0% |
| histboost | all | MSI | 1 | 0.9047% | 0.24955 | 0.69225 | 75.4% |
| histboost | all | MSI | 5 | 2.5002% | 0.26900 | 0.73319 | 79.5% |
| histboost | market | AXON | 1 | 1.9850% | 0.25369 | 0.70054 | 77.9% |
| histboost | market | AXON | 5 | 5.1914% | 0.25431 | 0.70238 | 82.6% |
| histboost | market | MSI | 1 | 0.9074% | 0.24958 | 0.69230 | 75.9% |
| histboost | market | MSI | 5 | 2.5234% | 0.27014 | 0.73585 | 75.9% |
| histboost | news | AXON | 1 | 2.0002% | 0.25728 | 0.70781 | 77.4% |
| histboost | news | AXON | 5 | 5.1719% | 0.25431 | 0.70238 | 82.1% |
| histboost | news | MSI | 1 | 0.9068% | 0.25013 | 0.69341 | 76.4% |
| histboost | news | MSI | 5 | 2.4746% | 0.27480 | 0.74608 | 76.9% |
| histboost | price | AXON | 1 | 1.9955% | 0.25516 | 0.70352 | 78.9% |
| histboost | price | AXON | 5 | 5.1719% | 0.25431 | 0.70238 | 82.1% |
| histboost | price | MSI | 1 | 0.9068% | 0.25013 | 0.69341 | 76.4% |
| histboost | price | MSI | 5 | 2.4746% | 0.27480 | 0.74608 | 76.9% |
| histboost | sec | AXON | 1 | 2.0255% | 0.25545 | 0.70414 | 78.4% |
| histboost | sec | AXON | 5 | 5.3560% | 0.25649 | 0.70650 | 78.5% |
| histboost | sec | MSI | 1 | 0.9038% | 0.24985 | 0.69285 | 75.4% |
| histboost | sec | MSI | 5 | 2.4916% | 0.26913 | 0.73349 | 76.9% |
| linear | all | AXON | 1 | 2.0235% | 0.25779 | 0.70879 | 75.9% |
| linear | all | AXON | 5 | 5.1742% | 0.24463 | 0.68238 | 82.1% |
| linear | all | MSI | 1 | 0.9052% | 0.25737 | 0.70795 | 74.9% |
| linear | all | MSI | 5 | 2.6732% | 0.27568 | 0.74860 | 74.9% |
| linear | market | AXON | 1 | 1.9886% | 0.25637 | 0.70553 | 77.9% |
| linear | market | AXON | 5 | 5.0283% | 0.24474 | 0.68245 | 81.0% |
| linear | market | MSI | 1 | 0.9057% | 0.25230 | 0.69794 | 76.4% |
| linear | market | MSI | 5 | 2.6487% | 0.27108 | 0.73780 | 75.4% |
| linear | news | AXON | 1 | 1.9915% | 0.25653 | 0.70620 | 78.9% |
| linear | news | AXON | 5 | 5.0605% | 0.24340 | 0.67975 | 82.1% |
| linear | news | MSI | 1 | 0.8955% | 0.24979 | 0.69274 | 76.9% |
| linear | news | MSI | 5 | 2.5637% | 0.27587 | 0.74851 | 72.8% |
| linear | price | AXON | 1 | 1.9899% | 0.25701 | 0.70704 | 78.4% |
| linear | price | AXON | 5 | 5.0544% | 0.24355 | 0.68001 | 81.5% |
| linear | price | MSI | 1 | 0.8958% | 0.24973 | 0.69261 | 77.4% |
| linear | price | MSI | 5 | 2.5552% | 0.27521 | 0.74700 | 72.8% |
| linear | sec | AXON | 1 | 2.0235% | 0.26463 | 0.72282 | 76.4% |
| linear | sec | AXON | 5 | 5.1942% | 0.24564 | 0.68448 | 80.0% |
| linear | sec | MSI | 1 | 0.8960% | 0.25645 | 0.70610 | 75.4% |
| linear | sec | MSI | 5 | 2.5744% | 0.26809 | 0.73164 | 75.4% |

News and SEC features did not demonstrate incremental predictive value under this budget. Some news-augmented boosted outputs were unchanged from price-only outputs. SEC-only additions often worsened point metrics. These are results for this selected sample, target and search budget; they do not prove the information is universally useless. IR could not be tested historically. Raw reports contain calibration bins, bias/RMSE, probability dispersion, fitted parameters, year breakdowns, all uncertainty intervals, and the separately derived descriptive quarter slices.

## Prospective protocol and remaining evidence

The frozen protocol starts with origin session **2026-11-16**. Issuance is 20:30 America/New_York using only actual pre-20:00 acquisitions. The next open is an outcome input only. The unchanged reference runs alongside the experimental shadow. Missing required context skips the shadow; no retrospective insertion is allowed.

The first 126 paired origin dates must include both stocks and both fully matured horizons. One final analysis uses 2,000 paired 20-session blocks, resampling stocks together, and four 98.75% improvement intervals. All four must improve and integrity checks must pass. There is no sequential pass hunting or automatic promotion. Current paired-date count is zero.

Recommended next experiment: collect the frozen prospective evidence without retuning. In parallel, archive original news revisions and timestamped official announcement metadata prospectively before considering guidance/text features. FRED/ALFRED remains a later group requiring vintage/release-time and series-rights review plus an authorized key. Pooled historical universes and YOLO remain blocked. The PC and worker must remain running. No VPS deployment or original trading/Minecraft access occurred.

See [predeclaration](phase3-protocol.md), [source review](phase3-sources.md), and [complete numerical evidence](phase3-results.json).
