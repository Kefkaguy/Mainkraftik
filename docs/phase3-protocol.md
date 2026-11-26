# Phase 3 declaration, before data-dependent tuning

Research stays local, personal and noncommercial; no redistribution, purchase,
execution privileges, VPS access or changes to existing trading services.

Preserve the baseline and all previous negative reports. New exploratory analysis
uses only sessions before the sealed original boundary (2025-11-14). That history
is already exposed development data, never independent confirmation.

Inputs: versioned Alpaca news metadata (no full article bodies), as-filed SEC facts
and filing metadata; official IR metadata with conservative acquisition-time
availability if edits cannot be dated; same-feed SPY/XLK/XLI prices and the fixed
MSI/AXON counterpart pair. ETFs are named context proxies, not reconstructed sector
membership. Missing information remains missing. Updated news cannot be assigned
to cutoffs preceding its updated timestamp. Historical metadata reconstructed from
today's archive has revision/survivorship limitations and is exploratory only.

Outer chronological split: 60/20/20 with five-session purges and contained labels.
Optuna selection uses an inner 60/20/20 split entirely within outer training.
Outer calibration fits probability mapping and residual intervals; outer evaluation
never selects parameters or the nominated candidate. Shared boundaries across stocks.

Budget: four trials for each of two families (ridge/logistic paired heads and small
histogram gradient boosting), two horizons, and six feature groups (price, sector/
counterpart context, news, SEC, IR, all eligible information): at most 96 trials.
Each trial trains separate MSI and AXON heads, not a pooled model. IR or other
groups lacking historical evidence are recorded as blocked, not silently substituted.
No extra searches after seeing outer scores. Failures/interrupted slots consume budget.

Objective: average over both stocks of half normalized MAE and half normalized
Brier against their training mean/base-rate references, using inner validation only.
Nominate the family/group with the lowest mean inner objective across both horizons,
subject to prospective data availability. Nomination confers experimental status only.

Fit the nominated family/configuration once on all eligible pre-enrollment history,
using its final 20% as disjoint calibration with a five-session purge. Freeze JSON
parameters, source/feature versions, code/runtime identity and membership. Start no
earlier than the next exchange session after enrollment. Issue alongside the unchanged
reference using the exact next-open targets and actual 20:00 cutoff/20:30 issuance.
Missing inputs skip the shadow only; never alter or backdate the reference.

Confirmation: the first 126 distinct eligible paired origin dates, each with both
stocks and both fully matured horizons. One frozen verdict, four primary comparisons
(MAE/Brier for both horizons), 2,000 paired 20-session date blocks, 98.75% intervals.
Resample both stocks together by date. All four lower improvement bounds must be
positive and integrity checks must pass. No sequential pass hunting or automatic
promotion; fewer dates is inconclusive. Descriptive reliability may update separately.
