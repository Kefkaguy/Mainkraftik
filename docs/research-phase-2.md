# Research phase 2: bounded development and future confirmation

Goal: test incremental predictive information for MSI/AXON without trading authority.
Owner: the private research workspace. A failed candidate stays visible; the scheduled
historical baseline remains the fallback. No accuracy or profitability target is promised.

## Declaration before new experiments

The original technical-v1 evaluation (starting 2025-11-14 in the current archive)
has already been inspected repeatedly. It is exposed evidence, not a fresh holdout.
Preserve it and seal it against new candidate evaluation. New development uses only
sessions strictly before the earliest original evaluation boundary, with targets also
ending before that boundary. All securities use common chronological boundaries.

Budget: one frozen development dataset, nine fixed configurations, MSI and AXON,
two horizons: 36 candidate/stock/horizon evaluations, with no parameter search.
Candidates: training mean/base rate; shrunk volatility buckets; original linear
heads; calibrated linear heads on price, price+volatility, and market features;
small histogram gradient boosting; shallow random forest; fixed equal-weight
linear/boosted ensemble. The latter three justify one established scikit-learn
dependency instead of separate XGBoost/LightGBM runtimes. Fixed seeds, one CPU
thread, 64 trees/iterations, conservative leaf sizes; no random validation split.

Each development experiment uses 60/20/20 temporal partitions, five-session
origin purges, and label containment. Preprocessing fits on training only.
Probability sigmoid calibration and volatility-scaled residual intervals fit on
calibration only. Test data never fits thresholds, scalers, or parameters.
Record every prediction, model/config/data/code identity, partition, and failure.

Compare MAE and Brier with the historical mean/base rate at each horizon using
2,000 paired 20-session moving-block samples and 98.75% intervals. Also report
RMSE, bias, log loss, direction accuracy, calibration, interval coverage/width,
year/regime slices, train-to-test degradation, probability dispersion, and a
training-median MAE diagnostic. Development intervals are diagnostic: nine-model
selection and previously seen data invalidate confirmatory interpretation.
Every new candidate remains **inconclusive for validation**, even when a
development comparison looks favorable. No automatic promotion.

Original negative results are immutable. Replays return frozen results; a code or
configuration change cannot silently reuse the same experiment identity. Missing
data and runtime failures consume the declared slot and remain in the ledger.

## Confirmation and boundaries

Fresh prospective observations are necessary for any new validation verdict.
Freeze candidate, parameters, feature definition, cutoff and membership before
collecting them; require at least 126 genuinely issued evaluation dates and a
single predeclared four-comparison analysis. Do not reuse this development test
as final confirmation or repeatedly peek for a passing date.

Financials/earnings, sectors and pooled models stay blocked until historical
availability, membership and delisting evidence is audited. Watchlist edits cannot
change frozen research membership. YOLO stays licensing/runtime blocked. No VPS,
Minecraft, paid subscriptions, broker credentials or execution integrations.
