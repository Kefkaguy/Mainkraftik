# Phase 2: actual forecasting results

Nine configurations, two selected stocks and two horizons; 36 frozen experiments. 7,092 development predictions are retained. No candidate was promoted.

Development test origins start 2025-01-30. One-day targets end by 2025-11-13; five-day origins stop earlier so their targets also end before the sealed 2025-11-14 period. There are 199 one-day / 195 five-day test dates, approximately nine 20-session blocks and 39 non-overlapping five-day windows. These counts do not prove independence.

All new validation statuses are inconclusive because this is previously available development history. None of the 98.75% diagnostic improvement intervals had a positive lower bound. The four-comparison procedure does not correct selection across nine candidates. Fresh prospective confirmation is required; there is no supported forecasting breakthrough.

## Original model diagnosis

| Stock | Horizon | MAE | Reference MAE | Brier | Reference Brier | Interval coverage |
|---|---:|---:|---:|---:|---:|---:|
| AXON | 1 | 2.4049% | 2.4596% | 0.24258 | 0.25139 | 67.1% |
| AXON | 5 | 7.4080% | 7.4878% | 0.25332 | 0.25833 | 69.4% |
| MSI | 1 | 0.9586% | 0.9399% | 0.26528 | 0.25022 | 69.9% |
| MSI | 5 | 2.5730% | 2.5221% | 0.24346 | 0.24158 | 70.6% |

MSI’s point estimates were worse on all four declared comparisons. AXON had small point improvements but all corrected intervals crossed zero. This fails the improvement gate without proving that no signal could exist. Original logistic output was uncalibrated; its probabilities did vary over time. Observed interval coverage of 67–71% undercovered the nominal middle 80%.

Train-to-test degradation is consistent with weak generalization and distribution changes, but alone cannot distinguish overfitting from regime shift. AXON’s one-day in-sample Brier was 0.24566 versus 0.24258 in evaluation, while MAE rose from 1.547% to 2.405%; it is incorrect to attribute every failure to overfitting. MSI one-day Brier rose from 0.24604 in sample to 0.26528 in evaluation. These are descriptive diagnostics, not causal findings.

The audit reconstructed forecasts from stored fitted parameters and reconciled the original MAE. Tests reconcile the original heads against scikit-learn, validate train-only scaling, purged label containment, coherent feed/snapshot selection and causal features. No future open enters a feature. The reference remains mean return / positive-return frequency; the new report also records the training-median MAE diagnostic.

## New bounded experiments

| Stock | Horizon | Configuration | MAE | Brier | Log loss | Direction accuracy | Interval coverage |
|---|---:|---|---:|---:|---:|---:|---:|
| AXON | 1 | ensemble-market-v2 | 1.9937% | 0.26265 | 0.71937 | 49.7% | 77.9% |
| AXON | 1 | forest-market-v2 | 2.0105% | 0.25855 | 0.71047 | 45.2% | 79.4% |
| AXON | 1 | histboost-market-v2 | 2.0020% | 0.25821 | 0.71071 | 50.3% | 80.9% |
| AXON | 1 | linear-market-v2 | 2.0219% | 0.26083 | 0.71482 | 45.7% | 76.9% |
| AXON | 1 | linear-price-v2 | 2.0037% | 0.25542 | 0.70434 | 50.3% | 78.4% |
| AXON | 1 | linear-volatility-v2 | 2.0065% | 0.25554 | 0.70422 | 48.2% | 78.4% |
| AXON | 1 | mean-v2 | 1.9878% | 0.24784 | 0.68882 | 55.3% | 79.4% |
| AXON | 1 | original-linear-v2 | 2.0132% | 0.24954 | 0.69191 | 53.3% | 77.9% |
| AXON | 1 | volatility-v2 | 1.9877% | 0.24790 | 0.68894 | 55.3% | 78.9% |
| AXON | 5 | ensemble-market-v2 | 5.4298% | 0.24406 | 0.68118 | 56.9% | 83.6% |
| AXON | 5 | forest-market-v2 | 5.2997% | 0.24707 | 0.68761 | 60.0% | 83.1% |
| AXON | 5 | histboost-market-v2 | 5.5334% | 0.25799 | 0.71062 | 51.8% | 81.5% |
| AXON | 5 | linear-market-v2 | 5.5356% | 0.24391 | 0.68075 | 55.9% | 84.6% |
| AXON | 5 | linear-price-v2 | 5.1763% | 0.24796 | 0.68916 | 55.4% | 82.6% |
| AXON | 5 | linear-volatility-v2 | 5.6295% | 0.24892 | 0.69107 | 55.4% | 83.6% |
| AXON | 5 | mean-v2 | 5.2054% | 0.24726 | 0.68765 | 55.4% | 80.5% |
| AXON | 5 | original-linear-v2 | 5.2826% | 0.27554 | 0.76433 | 48.7% | 82.6% |
| AXON | 5 | volatility-v2 | 5.3258% | 0.26115 | 0.71574 | 41.5% | 82.1% |
| MSI | 1 | ensemble-market-v2 | 0.9525% | 0.25039 | 0.69392 | 51.3% | 74.4% |
| MSI | 1 | forest-market-v2 | 0.9108% | 0.25149 | 0.69613 | 47.2% | 75.4% |
| MSI | 1 | histboost-market-v2 | 0.9675% | 0.25080 | 0.69474 | 47.7% | 74.9% |
| MSI | 1 | linear-market-v2 | 0.9644% | 0.24980 | 0.69275 | 53.8% | 71.9% |
| MSI | 1 | linear-price-v2 | 0.8983% | 0.24997 | 0.69310 | 50.8% | 75.4% |
| MSI | 1 | linear-volatility-v2 | 0.9061% | 0.25017 | 0.69349 | 49.2% | 74.4% |
| MSI | 1 | mean-v2 | 0.8900% | 0.24929 | 0.69173 | 54.3% | 76.4% |
| MSI | 1 | original-linear-v2 | 0.9279% | 0.25836 | 0.71145 | 48.7% | 73.9% |
| MSI | 1 | volatility-v2 | 0.8904% | 0.24875 | 0.69064 | 54.8% | 77.4% |
| MSI | 5 | ensemble-market-v2 | 2.7879% | 0.28432 | 0.76834 | 51.3% | 73.3% |
| MSI | 5 | forest-market-v2 | 2.5940% | 0.27213 | 0.74019 | 51.3% | 79.5% |
| MSI | 5 | histboost-market-v2 | 2.6625% | 0.28627 | 0.77297 | 51.3% | 73.8% |
| MSI | 5 | linear-market-v2 | 3.0649% | 0.27683 | 0.75072 | 51.3% | 66.7% |
| MSI | 5 | linear-price-v2 | 2.4876% | 0.27721 | 0.75180 | 51.3% | 72.3% |
| MSI | 5 | linear-volatility-v2 | 2.5833% | 0.27719 | 0.75176 | 51.3% | 71.8% |
| MSI | 5 | mean-v2 | 2.4779% | 0.25146 | 0.69609 | 51.3% | 74.4% |
| MSI | 5 | original-linear-v2 | 2.7295% | 0.28281 | 0.76934 | 45.1% | 69.7% |
| MSI | 5 | volatility-v2 | 2.5265% | 0.25686 | 0.70732 | 51.8% | 75.9% |

Adding volatility and market features did not establish incremental directional or return information. Calibrated price-only linear heads sometimes reduced development Brier versus the original heads, but calibrated candidates did not establish improvement over the reference. Fixed volatility-scaled intervals improved some coverages and still undercovered other slices. The raw JSON includes year/regime slices, probability dispersion, bias, RMSE, calibration bins, interval score and complete comparison intervals.

Decision: retain the scheduled mean/base-rate reference, preserve all negative studies, and do not repeat this search on the exposed evaluation. The next technical milestone is a frozen prospective shadow protocol for one nominated candidate and at least 126 new eligible origin dates, with matured labels and one declared four-comparison verdict. That protocol is not enrolled in this delivery; only the reference baseline is scheduled. Separately audit timestamped earnings/filing events if seeking new information rather than additional transformations of the same prices.

See [research declaration](research-phase-2.md), [source feasibility](data-source-feasibility.md), [raw results](research-phase-2-results.json), and [original audit](original-model-audit.json).
