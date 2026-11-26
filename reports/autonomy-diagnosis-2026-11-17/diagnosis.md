# Mainkraft autonomy diagnosis — November 17, 2026 Pacific

## Exact cause of zero orders

Read-only VPS inspection confirmed 35 legacy decisions, zero intents, zero recorded order versions, and zero new Mainkraft strategy signals. These are application-ledger counts, not a new direct broker-history audit.

The legacy decision ledger contains 28 HOLDs and seven SKIPs. All 28 HOLDs fail the active discovery-policy probability requirement of 70%; their observed probabilities range from about 38.9% to 55.4%. Six November 13 SKIPs lack account readiness before the 09:15 cutoff. November 16 NBIS is skipped because required exchange sessions are missing. No BUY reached the downstream intent/submission path.

The reconstructed, overlapping failures across all 35 rows are: confidence 34, gross expected return <=15bp 21, annualized volatility >80% three, non-bullish news three, readiness six, missing data one. All 28 HOLDs have local sentiment assessment `review`; no negative-sentiment veto explains them. See candidate-rejections.csv for every candidate and decision-evidence.json for projected source evidence. The summary records its SHA-256.

Cost-adjusted return, entry-window, cash, exposure, and broker submission checks were downstream and not reached by an eligible BUY. They cannot be assigned as historical rejection causes or marked passed. Zero intents provides no evidence of an attempted broker submission failure. The stored generic HOLD reason is insufficient; specific failures above are reconstructed from stored numerical evidence and the legacy conjunction, rather than contemporaneous reason events.

## Pipeline and policy identity

Alpaca SIP daily prices -> prior-session features -> ridge/logistic return and probability heads -> sourced Groq news stance and local financial sentiment veto -> immutable decision -> quote/cost and risk gates -> durable client-ID intent -> dedicated Alpaca Paper broker -> reconciliation, positions, exits and reports.

The 35 decisions belong to the older policy. Mainkraft day/swing policy was activated after November 17 trading. Its strategy tables are empty; its absence of signals before its first eligible session is not a generation failure. Current Mainkraft entries use 09:35–09:36 New York, not the legacy 09:35–10:00 window. Its ordinary probability floors are 50% day and 55% swing. A previously deployed 20-session swing expectancy trial starts November 18 and uses an $800 paper notional with narrow flat-book authorization. Preserve that existing policy exactly; this diagnosis does not authorize changing it.

## Implemented versus verified

Implemented: scheduled monitoring and refresh, daily news selection, numerical models, pinned local sentiment, isolated challenger, cutoff checks, immutable signals, quote and cost checks, risk arbitration, durable intents, broker acknowledgement recovery, position ownership, stop/take/time exits, reporting, historical model lab, and local strategy simulator. Research and paper have independent databases. The research worker is running an older phase5 image than the API; model/version provenance must be checked per process.

Observed functioning: scheduled legacy selections and forecasts, deterministic HOLD/SKIP results, readiness observations, and zero submission attempts for ineligible signals. Not established: complete autonomous lifecycle under restarts, broker fills, and equivalence of research replay to runtime.

Focused local verification: test_mainkraft, test_strategy_lab, test_paperpilot, test_premarket_readiness and test_execution_costs: 49 passed, two failed. Both failures are test_freeze_to_paper_intent_and_virtual_outcome: expected 0.25 simulated shares after a qualifying fixture, observed zero. Root cause is not yet isolated. Tests use temporary local PostgreSQL databases and a mocked broker. See lifecycle-tests.txt and lifecycle-tests.xml.

Existing .local/strategy-lab/simulator-audit.json explicitly reports component_reconciled_full_engine_pending and deployment_gate_passed=false. Its remaining scope is full partial-fill notifications and restart persistence, shared runtime/replay acceptance across schedules, and the difference between five-minute price bars and executable historical quotes. Component agreement must not be presented as complete lifecycle verification.

## Defects and missing work

- Legacy HOLD explanations omit individual failed gates. Downstream checks frequently stop at the first reason, so untested gates need explicit status.
- Full local lifecycle fixtures currently fail; the cause could be the engine or fixture environment. Diagnose before changing behavior.
- Strategy issuance records its daily attempt even after a handled transient failure or an early return with no persisted session. That suppresses in-process retry; restart behavior differs. Confirm with deterministic tests before choosing bounded retries.
- Full simulator lifecycle and shared runtime/replay reconciliation are unfinished.
- A separate multitimeframe candle-sequence research module with joint return, direction, target-before-stop, downside and uncertainty evaluation has not been established by this inspection. Existing linear/boosted price-feature experiments are partial foundations.
- Research orchestration exists, but continuous performance deterioration detection and improvement proposals require explicit validation and an execution-isolated controller.
- Existing dashboards need a complete audit trail across predictions, eligibility and execution, with separate simulation, dry-run and broker evidence.

## Smallest safe implementation order for approval

1. Reproduce and isolate the two lifecycle failures; pin clocks, local resource fixtures and fake broker transport. Test order creation, partial fills, unknown acknowledgements, stop/take/time exits, daily risk, restart recovery and no duplicate orders. Keep application policy unchanged.
2. Add a versioned, structured all-gate decision trace, recording pass/fail/not-evaluated and source times. Expose it in the current dashboard. Preserve original immutable decisions; backfills are labelled reconstructions.
3. Finish full simulator lifecycle and shared live/replay fixture acceptance. Publish hashes, source versions, fills, positions, cash, costs and known modeling differences. Deployment remains blocked on failures or unexplained differences.
4. Add an execution-isolated Chart Intelligence experiment: aligned causal multitimeframe candle windows, purged chronological development/calibration/confirmation, simple statistical and current-model baselines, and bounded sequence models. Derive target/stop labels conservatively, record ambiguity, evaluate probability calibration, downside and costs. Freeze trials and retain untouched confirmation. No brokerage imports or promotion authority.
5. Extend the existing research controller to schedule fresh data, predictions, outcome resolution, drift/calibration monitoring and bounded offline proposals. No automatic promotion or risk changes.
6. Integrate monitored universe, predictions, opportunities and reasons, actual paper orders/positions/P&L, simulated results, accuracy and deployment status in the existing web app. Every result carries evidence type and policy/model identity.

This first deliverable adds only a read-only export/reconstruction script and local reports. No deployment, policy, broker credentials, position limits, or unrelated VPS services were changed. Major implementation awaits approval of this concrete plan.
