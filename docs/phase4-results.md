# Phase 4 deployment and activation

Completed November 13, 2026, Pacific time, after the regular market close.

The independent application is deployed at https://keftrade.duckdns.org/intelligence.
Single-user sessions use Secure, HttpOnly, SameSite=Strict cookies and CSRF checks.
Backend and database ports remain private. Research and paper workers run on the VPS.
The original MSI/AXON code fingerprints and frozen prospective protocol remain unchanged.
No old trader configuration or protected game resources were modified.

The supplied new paper credentials authenticated successfully. Its actual account ID
differs from the pinned old trader account. Initial positions and open orders were empty;
cash was sufficient. AMD, MSFT, GOOG, AMZN, TSM and META were verified as active,
tradable, fractional US equities. Historical prices for all six and SPY cover the
November 13 completed session. TSM IFRS financial facts remain explicitly unsupported.

The pilot is enabled through its private control interface. Alpaca's clock/calendar
confirmed the next regular session is Monday, November 16, 09:30–16:00 New York.
Forecast cutoff is 06:15 Pacific, issuance 06:15–06:25, entry 06:35–07:00. Only
eligible signals can submit orders; missing readiness/data records a skip. No setup
orders were submitted, and Friday activity is not fabricated or backdated.

Limits remain $100 per stock, $600 combined, $90 initial entry, $12 daily-loss
breaker, long-only, no borrowing. Every broker mutation verifies the pinned account;
fresh open-order reservations, holdings and cash are checked before purchases.
Sell quantity cannot exceed available long inventory. Durable client IDs prevent
blind retries. Emergency disable leaves existing positions explicit and cancels
only owned pending orders.

Verification: 93 backend tests passed; frontend production builds succeeded on
Windows and Linux. Auth, CSRF, private bridge/SSE and worker restart checks passed.
Both live VPS databases were exported and restored off-host. Images were built
locally. The 10 GiB disk reserve and bounded growth gates remain enforced. No old
VPS data cleanup or Docker pruning was used. Scheduled evidence/performance reports
run at 20:45 New York on weekdays, and the scoped HTTPS renewal timer is installed.

The models remain experimental. No predictive improvement or profitable trading
result is claimed. Broker fills, live-session behavior and prospective accuracy
can only be measured during future eligible sessions. Account deposits and resets
are not treated as strategy profit. Calibration, interval coverage, execution
quality, exposure-adjusted results and dependence-aware descriptive uncertainty
are included in the reporting code; insufficient evidence stays inconclusive.

Secrets remain outside the website in mode-600 server configuration. Local login
details and account-acceptance evidence are in ignored `.local` files. Rotating API
keys within the same new account requires updating `paper.env` and recreating only
the paper service to reload them; changing account identity requires verification.
