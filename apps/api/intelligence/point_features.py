"""One shared, versioned feature path for historical research and live shadow inference."""

from datetime import timedelta

import numpy as np

from intelligence.calendar import cutoff_for
from intelligence.experiments import enriched_panel
from intelligence.financials import period_kind, select_versions
from intelligence.forecasting import continuity
from intelligence.research_models import FEATURES as PRICE_NAMES
from intelligence.storage import bars_at, jsonb
from intelligence.util import digest

VERSION = "information-daily-v3"
NAMES = [
    *PRICE_NAMES,
    "xlk_return_5",
    "xlk_return_20",
    "xli_return_5",
    "xli_return_20",
    "sector_excess_5",
    "counterpart_return_5",
    "counterpart_return_20",
    "counterpart_volatility_20",
    "news_count_1d_log",
    "news_count_5d_log",
    "unusual_news_activity",
    "days_since_news",
    "revenue_yoy",
    "operating_margin",
    "operating_margin_yoy_change",
    "cash_flow_yoy_change",
    "days_since_filing",
    "earnings_filing_last_7d",
    "guidance_direction_30d",
    "contracts_30d",
    "acquisitions_30d",
    "official_announcements_7d",
]
GROUPS = {
    "price": list(range(14)),
    "market": list(range(22)),
    "news": [*range(14), *range(22, 26)],
    "sec": [*range(14), *range(26, 32)],
    "ir": [*range(14), *range(32, 36)],
    "all": list(range(32)),
}


def information_at(rows, provider, symbol, cutoff, observed=False):
    selected = {}
    for row in rows:
        if row["provider"] != provider or symbol not in row["symbols"]:
            continue
        if row["available_at"] > cutoff or row["created_at"] > cutoff:
            continue
        if observed and row["acquired_at"] > cutoff:
            continue
        key = row["external_id"]
        if key not in selected or row["updated_at"] > selected[key]["updated_at"]:
            selected[key] = row
    return list(selected.values())


def financial_values(facts, events, cutoff, observed=False):
    selected = select_versions(facts, cutoff, observed_only=observed)
    quarters = sorted(
        [r for r in selected if period_kind(r) == "quarter"], key=lambda r: r["period_end"], reverse=True
    )

    def latest(metric):
        return next((r for r in quarters if r["metric"] == metric), None)

    def prior(current, metric):
        return next(
            (
                r
                for r in quarters
                if current
                and r["metric"] == metric
                and 330 <= (current["period_end"] - r["period_end"]).days <= 400
            ),
            None,
        )

    revenue = latest("revenue")
    previous = prior(revenue, "revenue")
    growth = (
        float(revenue["value"]) / float(previous["value"]) - 1
        if revenue and previous and previous["value"] > 0
        else None
    )

    def margin(rev):
        op = next(
            (
                r
                for r in quarters
                if rev
                and r["metric"] == "operating_income"
                and (r["period_start"], r["period_end"], r["unit"])
                == (rev["period_start"], rev["period_end"], rev["unit"])
            ),
            None,
        )
        return float(op["value"]) / float(rev["value"]) if op and rev["value"] > 0 else None

    current_margin, previous_margin = margin(revenue), margin(previous)
    ocfs = sorted(
        [
            r
            for r in selected
            if r["metric"] == "operating_cash_flow"
            and period_kind(r) in {"annual", "quarter", "year_to_date"}
        ],
        key=lambda r: r["period_end"],
        reverse=True,
    )
    current = ocfs[0] if ocfs else None
    old = next(
        (
            r
            for r in ocfs
            if current
            and 330 <= (current["period_end"] - r["period_end"]).days <= 400
            and abs(
                (current["period_end"] - current["period_start"]).days
                - (r["period_end"] - r["period_start"]).days
            )
            <= 15
        ),
        None,
    )
    ocf_change = (
        (float(current["value"]) - float(old["value"])) / abs(float(old["value"]))
        if current and old and old["value"] != 0
        else None
    )
    eligible = [
        r for r in events if r["published_at"] <= cutoff and (not observed or r["acquired_at"] <= cutoff)
    ]
    filing_days = (
        (cutoff - max(r["published_at"] for r in eligible)).total_seconds() / 86400 if eligible else None
    )
    # Zero only means no event within the observed recent filing array; historical completeness is disclosed.
    earnings = (
        sum(
            "2.02" in r["items"]
            and r["form"].startswith("8-K")
            and r["published_at"] >= cutoff - timedelta(days=7)
            for r in eligible
        )
        if eligible
        else None
    )
    return (
        [
            growth,
            current_margin,
            current_margin - previous_margin
            if current_margin is not None and previous_margin is not None
            else None,
            ocf_change,
            filing_days,
            float(earnings) if earnings is not None else None,
        ],
        [r["id"] for r in selected],
        [r["id"] for r in eligible],
    )


def information_values(symbol, cutoff, information, coverage, facts, events, observed=False):
    news_cutoff = cutoff - timedelta(hours=1)
    covered = any(
        c["provider"] == "alpaca_news"
        and c["status"] == "complete"
        and symbol in c["symbols"]
        and c["started_at"] <= news_cutoff - timedelta(days=25)
        and c["ended_at"] >= news_cutoff
        and (not observed or c["acquired_at"] <= cutoff)
        for c in coverage
    )
    news = information_at(information, "alpaca_news", symbol, news_cutoff, False)
    if observed:
        news = [r for r in news if r["acquired_at"] <= cutoff]
    counts = [sum(r["created_at"] > news_cutoff - timedelta(days=d) for r in news) for d in (1, 5, 25)]
    latest = max((r["created_at"] for r in news), default=None)
    news_values = (
        [
            float(np.log1p(counts[0])),
            float(np.log1p(counts[1])),
            counts[0] / (1 + (counts[2] - counts[1]) / 20),
            min(365, (news_cutoff - latest).total_seconds() / 86400) if latest else 365,
        ]
        if covered
        else [None] * 4
    )
    sec, fact_ids, event_ids = financial_values(facts, events, cutoff, observed)
    ir = information_at(information, "official_ir", symbol, cutoff, observed)
    recent = [r for r in ir if r["created_at"] > cutoff - timedelta(days=30)]
    directions = [
        r["payload"]["flags"]["guidance_direction"]
        for r in recent
        if r["payload"]["flags"]["guidance_direction"] is not None
    ]
    ir_values = (
        [
            float(sum(directions)) if directions else None,
            float(sum(r["payload"]["flags"]["contract"] for r in recent)),
            float(sum(r["payload"]["flags"]["acquisition"] for r in recent)),
            float(sum(r["available_at"] > cutoff - timedelta(days=7) for r in recent)),
        ]
        if ir
        else [None] * 4
    )
    return [*news_values, *sec, *ir_values], {
        "news_ids": [r["id"] for r in news if r["created_at"] > news_cutoff - timedelta(days=25)],
        "news_coverage": covered,
        "fact_ids": fact_ids,
        "filing_ids": event_ids,
        "ir_ids": [r["id"] for r in recent],
        "news_lag_minutes": 60,
        "historical_news": "Current archive censored by version availability, not original-vintage completeness",
    }


def build_features(streams, symbol, information, coverage, facts, events, observed=False, latest_only=False):
    for stream in streams.values():
        if stream and not continuity(stream)[0]:
            raise ValueError("A context price stream has missing sessions or mixed adjustment policy")
    own, spy = streams[symbol], streams["SPY"]
    # ETF/counterpart snapshots may have different acquisition dates; each stream must be internally coherent.
    panel = enriched_panel(own, spy)
    if latest_only:
        panel = panel[-1:]
    maps = {s: {b["session_date"]: i for i, b in enumerate(rows)} for s, rows in streams.items()}
    peer = "AXON" if symbol == "MSI" else "MSI"
    for row in panel:
        day = row["date"]

        def ret(s, n):
            i = maps.get(s, {}).get(day, -1)
            return float(streams[s][i]["close"]) / float(streams[s][i - n]["close"]) - 1 if i >= n else None

        values = [ret("XLK", 5), ret("XLK", 20), ret("XLI", 5), ret("XLI", 20)]
        relative = (
            row["x"][1] - (values[0] + values[2]) / 2
            if values[0] is not None and values[2] is not None
            else None
        )
        j = maps.get(peer, {}).get(day, -1)
        vol = (
            float(np.std(np.diff(np.log([float(b["close"]) for b in streams[peer][j - 20 : j + 1]]))))
            if j >= 20
            else None
        )
        context = [*values, relative, ret(peer, 5), ret(peer, 20), vol]
        extra, lineage = information_values(
            symbol, cutoff_for(day), information, coverage, facts, events, observed
        )
        row["x"] = [*row["x"], *context, *extra]
        row["lineage"] = {
            **lineage,
            "price_bar_ids": [b["id"] for b in own[max(0, row["index"] - 60) : row["index"] + 1]],
            "market_bar_ids": {
                s: [b["id"] for b in rows[max(0, maps[s].get(day, 0) - 60) : maps[s].get(day, -1) + 1]]
                for s, rows in streams.items()
            },
        }
    return panel


def load_context(conn, cutoff=None, observed=False):
    streams = {
        s: bars_at(conn, s, cutoff, cutoff if observed else None)
        for s in ["MSI", "AXON", "SPY", "XLK", "XLI"]
    }
    info = conn.execute("SELECT * FROM information_versions").fetchall()
    coverage = conn.execute("SELECT * FROM information_fetches").fetchall()
    facts = conn.execute("SELECT * FROM financial_facts").fetchall()
    events = conn.execute("SELECT * FROM filing_events").fetchall()
    return streams, info, coverage, facts, events


def persist_panel(conn, symbol, panel, mode):
    ids = []
    for row in panel:
        payload = {
            "symbol": symbol,
            "date": row["date"],
            "version": VERSION,
            "mode": mode,
            "x": row["x"],
            "lineage": row["lineage"],
        }
        identity = digest(payload)
        conn.execute(
            "INSERT INTO feature_snapshots(id,symbol,origin_session,cutoff,version,mode,values,lineage) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (
                identity,
                symbol,
                row["date"],
                cutoff_for(row["date"]),
                VERSION,
                mode,
                jsonb(row["x"]),
                jsonb(row["lineage"]),
            ),
        )
        ids.append(identity)
    return ids
