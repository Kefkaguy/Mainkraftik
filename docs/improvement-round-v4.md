# Improvement round v4: fixed numerical test and execution repairs

The single cross-market challenger failed to establish an edge and remains disabled.
This round deploys better news coverage, correct pre-cutoff evidence selection and
an execution-cost veto; it does not claim demonstrated trading profitability.

The fixed candidate added QQQ-minus-SPY 5/20-session returns, IWM-minus-SPY 5/20
and TLT 5/20 to the original14 features. Ridge alpha100/logistic C0.1 were unchanged.
The predeclared cohort was the latest20-stock news shortlist, not a historically
unbiased universe. Seventeen stocks were usable; two lacked mature histories and
NBIS had missing sessions. Price snapshots and private experiment artifacts are
retained under .local/context-research with hashes. No historical news was fabricated.

Shared calendar60/20/20 blocks had5-session purges and contained labels. There were
249 one-day evaluation dates and245 five-day dates (about49 nonoverlapping5-session
windows and12.25 twenty-session blocks). Stock rows are correlated, not independent
sample counts. Previously accessible revised histories/current selection make the
results exploratory; no independent-confirmation verdict is granted.

| Horizon | Model | MAE | Brier | Log loss | Interval coverage |
|---|---|---|---|---|---|
| 1 | mean_available | 1.6721% | 0.250763 | 0.694674 | 77.9% |
| 1 | technical14 | 1.7012% | 0.251450 | 0.696074 | 78.2% |
| 1 | market20 | 1.7079% | 0.251243 | 0.695647 | 77.9% |
| 5 | mean_available | 4.4533% | 0.251252 | 0.695668 | 77.3% |
| 5 | technical14 | 4.7198% | 0.251880 | 0.697156 | 76.7% |
| 5 | market20 | 4.7526% | 0.253162 | 0.700963 | 77.1% |

Versus the matched-information historical mean, the candidate worsened MAE by
3.58bp at1d (98.75% improvement interval -5.16 to -1.24bp) and29.93bp at5d
(interval -46.30 to -15.65bp). Brier comparisons were inconclusive. None of the
four predeclared primary comparisons passed. Secondary comparisons with the14-feature
algorithm also showed no established improvement. Uncertainty used2,000 paired
20-session blocks with stocks resampled together by date. Per-stock/year metrics,
calibration and fixed cost sensitivities are in market-context-shadow-results.json.
No evaluated price-only target return is represented as actual fill-to-exit P/L.

A reporting-array type error required a deterministic replay before any loss figures
were printed. This repeated68 configured fits:136 actual fit calls, exceeding the
120-call compute allowance. The failure and replay deviation are explicitly retained.
No parameters, features, thresholds or acceptance rules changed after results were
viewed; the candidate remains rejected. Optional live shadow code is available but
the server flag stays false, avoiding unnecessary daily work for this failed candidate.

The shipping changes are:

- Balanced news prompt: all40 candidates, up to3 cited headlines each, deduplicated
  sources and explicit truncation metadata; saved-preview coverage improved from
  20 stocks/16 sources to40 stocks/73 sources within12,000 characters.
- Immutable readiness observations during09:13–09:15NY prevent a post-cutoff status
  update from hiding genuine verified evidence. A later pre-cutoff failure remains
  authoritative. Observation time records completion, not tick start.
- Select the latest price version acquired before cutoff. Future acquisitions cannot
  hide older eligible evidence or enter features.
- Execution-cost veto: require gross expected return to exceed the observed IEX
  round-trip spread plus10bp slippage allowance and5bp net margin, as well as all
  existing probability, return, quote, account, position and cash gates. These are
  planning allowances, not known future fees or a guarantee of net profit.
- Preserve all historical records and distinguish forecast quality by model version.

The numerical70% requirement, $25 entries,20-position limit,$100 per-stock cap,
$600 total exposure and$12 daily-loss trigger remain. No new paid services, model
weights, broker execution capability, UI inspection, old-trader changes or protected
game access are part of this round. The next useful numerical experiment is an
execution-aligned09:35-fill-to-exit target with historical intraday observations and
realistic costs; daily open-to-close probabilities are not probabilities of profitable
execution under stop/take-profit rules.

## Live-provider validation and follow-up

The first deployed v4 preview acquired/scored real news, then Groq returned an
unknown or duplicate stock. Strict validation blocked that shortlist; no execution
selection or orders were created. The attempt consumed the third actual request
for that New York day. The model-output failure is a runtime finding, not a passing
end-to-end test. The follow-up v4.1 selector constrains the schema to supplied
symbols and rejects unknown picks, all occurrences of duplicate symbols, and bad
citations per stock. Unrelated fully valid picks may survive; all-invalid nonempty
responses still fail closed. Top-level malformed/oversized responses remain blocked.

The final recovery behavior is tested without bypassing the request budget. The
next actual provider response using the tightened schema will occur during the
next scheduled research run after the New York daily budget resets. No additional
paid calls, retrospective forecasts or budget overrides are used.

## Final deployment acceptance

The server is running paper image phase7.1-research with balanced-news-context-v4.1.
It is healthy, has zero restarts/OOMs, and account reconciliation confirms automatic
paper execution is enabled. The HF worker remains healthy. The cost gate is enabled;
the failed market-context candidate is disabled. Free disk was11,309,477,888 bytes,
above10 GiB.148 backend tests passed, with one existing TestClient deprecation warning.
The frozen MSI/AXON implementation hashes remain unchanged. No UI/browser checks
were performed. Updated off-host database exports were restored into fresh local
clusters; receipts are retained under.local/off-host. Only a195,358,191-byte duplicate
image-transport archive was removed after matching its SHA-256 to the retained local
copy. Rollback images, data and protected services were preserved.

The real server replay retained40 candidates and73 headline sources in11,995
characters. Thirteen saved picks satisfied the new citation visibility rules; seven
were excluded. Injected duplicate/unknown fixture picks were safely isolated. This
is a saved-data test, not a new provider response, a live selection or a forecast.
The fresh tightened-schema provider run remains scheduled for the next daily budget.
All prices, targets and reported error comparisons retain their previously stated
exploratory limitations; there is no validated-profit claim.
