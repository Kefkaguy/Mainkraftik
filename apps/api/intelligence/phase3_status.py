"""Read-only source coverage and experiment summaries for the authenticated workspace."""

from intelligence.db import connection
from intelligence.prospective import progress_and_verdict
from intelligence.storage import bars_at


def phase3_status(symbol=None):
    with connection() as conn:
        sources = []
        for provider in ("alpaca_news", "official_ir"):
            for stock in ("MSI", "AXON"):
                row = conn.execute(
                    """SELECT count(*) versions,count(DISTINCT external_id) articles,
                    min(created_at) first_created,max(created_at) last_created,max(acquired_at) acquired_at,
                    count(*) FILTER(WHERE updated_at>created_at) revised_or_observation_dated
                    FROM information_versions WHERE provider=%s AND symbols ? %s""",
                    (provider, stock),
                ).fetchone()
                sources.append({"provider": provider, "symbol": stock, **row})
        filings = conn.execute("""SELECT symbol,count(*) versions,count(DISTINCT accession) filings,
            count(*) FILTER(WHERE form LIKE '8-K%' AND items LIKE '%%2.02%%') earnings_filings,
            min(published_at) first_available,max(published_at) last_available FROM filing_events GROUP BY symbol""").fetchall()
        market = []
        for s in ("SPY", "XLK", "XLI"):
            bars = bars_at(conn, s)
            market.append(
                {
                    "symbol": s,
                    "sessions": len(bars),
                    "first": bars[0]["session_date"] if bars else None,
                    "last": bars[-1]["session_date"] if bars else None,
                }
            )
        return {
            "sources": sources,
            "filings": filings,
            "markets": market,
            "news_coverage": conn.execute(
                "SELECT * FROM information_fetches ORDER BY acquired_at DESC LIMIT 5"
            ).fetchall(),
            "plans": conn.execute(
                "SELECT id,created_at,code_hash,specification FROM tuning_plans ORDER BY created_at"
            ).fetchall(),
            "trials": conn.execute(
                "SELECT plan_id,trial_key,family,feature_group,horizon,number,status,parameters,objective FROM tuning_trials ORDER BY feature_group,family,horizon,number"
            ).fetchall(),
            "candidates": conn.execute(
                "SELECT id,plan_id,family,feature_group,symbol,horizon,status,report-'artifact' AS report FROM candidate_results WHERE (%s::text IS NULL OR symbol=%s) ORDER BY symbol,horizon,feature_group,family",
                (symbol, symbol),
            ).fetchall(),
            "protocol": conn.execute(
                "SELECT * FROM shadow_protocols ORDER BY created_at DESC LIMIT 1"
            ).fetchone(),
            "progress": progress_and_verdict(),
            "limitations": [
                "Historical news is a current-vintage reconstruction censored by update time; original-version completeness is unproven",
                "IR page edit times are unverified: available only from actual acquisition, excluded from historical training",
                "Historical SEC timestamps use as-filed accessions; this is not a historical analyst-consensus dataset",
                "XLK and XLI are fixed context proxies; no retrospective sector/index membership assumed",
                "All development history is exposed. Optuna results do not establish independent predictive improvement",
            ],
        }
