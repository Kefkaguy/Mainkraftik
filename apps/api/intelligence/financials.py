"""Versioned GAAP facts; period-compatible metrics with traceable derivations."""

from datetime import date

from intelligence.providers import filing_availability
from intelligence.util import digest

VERSION = "gaap-metrics-v3"
TAGS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "eps": ["EarningsPerShareDiluted"],
    "operating_income": ["OperatingIncomeLoss"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
    "current_debt": ["LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent"],
    "long_term_debt": [
        "LongTermDebtNoncurrent",
        "LongTermDebtAndFinanceLeaseObligationsNoncurrent",
        "LongTermDebtAndCapitalLeaseObligationsNoncurrent",
    ],
    "diluted_shares": ["WeightedAverageNumberOfDilutedSharesOutstanding"],
}


def normalize_facts(symbol, facts, submissions, acquired):
    out = []
    taxonomy = (facts.get("facts") or {}).get("us-gaap") or {}
    cik = str(facts.get("cik", "")).lstrip("0")
    for metric, tags in TAGS.items():
        for priority, tag in enumerate(tags):
            for unit, rows in (taxonomy.get(tag, {}).get("units") or {}).items():
                expected = (
                    "USD/shares" if metric == "eps" else "shares" if metric == "diluted_shares" else "USD"
                )
                if unit != expected:
                    continue
                for r in rows:
                    if r.get("form") not in {"10-K", "10-Q", "10-K/A", "10-Q/A"} or not all(
                        k in r for k in ["end", "filed", "accn", "val"]
                    ):
                        continue
                    start = date.fromisoformat(r["start"]) if r.get("start") else None
                    end = date.fromisoformat(r["end"])
                    if start and start > end:
                        continue
                    item = {
                        "symbol": symbol,
                        "metric": metric,
                        "tag": tag,
                        "unit": unit,
                        "value": r["val"],
                        "period_start": start,
                        "period_end": end,
                        "fiscal_year": r.get("fy"),
                        "fiscal_period": r.get("fp"),
                        "form": r["form"],
                        "accession": r["accn"],
                        "filed_date": date.fromisoformat(r["filed"]),
                        "available_at": filing_availability(r["filed"], r["accn"], submissions),
                        "source_url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{r['accn'].replace('-', '')}/",
                    }
                    item["content_hash"] = digest(item)
                    item["acquired_at"] = acquired
                    out.append(item)
    return out


def period_kind(row):
    start = row.get("period_start")
    if not start:
        return "instant"
    duration = (row["period_end"] - start).days + 1
    if 70 <= duration <= 105:
        return "quarter"
    if 350 <= duration <= 380:
        return "annual"
    if 150 <= duration <= 300:
        return "year_to_date"
    return "other"


def select_versions(rows, asof, observed_only=False):
    selected = {}
    for row in rows:
        # Keep superseded source facts immutable, but exclude tags whose
        # measurement meaning is no longer supported by this calculation version.
        if row["metric"] not in TAGS or row["tag"] not in TAGS[row["metric"]]:
            continue
        if row["available_at"] > asof or (observed_only and row["acquired_at"] > asof):
            continue
        key = (row["metric"], row.get("period_start"), row["period_end"])
        priority = TAGS[row["metric"]].index(row["tag"])
        score = (row["available_at"], -priority, row["accession"])
        if key not in selected or score > selected[key][0]:
            selected[key] = (score, row)
    return [r for _, r in selected.values()]


def compatible(a, b):
    return (a.get("period_start"), a["period_end"], a["unit"]) == (
        b.get("period_start"),
        b["period_end"],
        b["unit"],
    )


def calculate_metrics(rows, asof):
    facts = select_versions(rows, asof)
    history = sorted(
        facts, key=lambda r: (r["period_end"], period_kind(r) == "quarter", r["available_at"]), reverse=True
    )
    result = {}
    labels = {
        "revenue": "Revenue",
        "net_income": "GAAP net income",
        "eps": "Diluted EPS",
        "operating_income": "Operating income",
        "operating_cash_flow": "Operating cash flow",
        "capex": "Reported cash capital investment",
        "cash": "Cash and equivalents",
        "diluted_shares": "Diluted shares",
    }
    for metric, label in labels.items():
        allowed = (
            {"quarter", "annual", "instant", "year_to_date"}
            if metric in {"operating_cash_flow", "capex"}
            else {"quarter", "annual", "instant"}
        )
        matches = [r for r in history if r["metric"] == metric and period_kind(r) in allowed]
        result[metric] = (
            _metric(label, matches[0])
            if matches
            else _missing(label, "No supported USD GAAP fact with a complete quarter or fiscal year")
        )
    for name, label, a_key, b_key, divide in [
        ("operating_margin", "Operating margin", "operating_income", "revenue", True),
        ("free_cash_flow", "Free cash flow", "operating_cash_flow", "capex", False),
    ]:
        pairs = [
            (a, b)
            for a in history
            if a["metric"] == a_key and period_kind(a) in {"quarter", "annual", "year_to_date"}
            for b in history
            if b["metric"] == b_key and compatible(a, b)
        ]
        if pairs:
            a, b = pairs[0]
            denominator = float(b["value"])
            if divide and denominator <= 0:
                result[name] = _missing(label, "Revenue must be positive")
            else:
                value = float(a["value"]) / denominator if divide else float(a["value"]) - denominator
                result[name] = {
                    **_metric(label, a),
                    "value": value,
                    "unit": "ratio" if divide else "USD",
                    "formula": "Operating income / revenue"
                    if divide
                    else "Operating cash flow âˆ’ cash capital expenditure",
                    "inputs": [_reference(a), _reference(b)],
                }
        else:
            result[name] = _missing(
                label,
                "Matching reporting periods and units are required; quarters and year-to-date values are not mixed",
            )
    debt_pairs = [
        (a, b)
        for a in history
        if a["metric"] == "current_debt"
        for b in history
        if b["metric"] == "long_term_debt" and compatible(a, b)
    ]
    if debt_pairs:
        a, b = debt_pairs[0]
        result["debt"] = {
            **_metric("Reported current + long-term debt", a),
            "value": float(a["value"]) + float(b["value"]),
            "inputs": [_reference(a), _reference(b)],
            "formula": "Reported current debt + reported noncurrent debt; leases depend on source tags",
        }
    else:
        result["debt"] = _missing(
            "Debt", "Both current and noncurrent components for the same date are required"
        )
    quarterly_revenues = [r for r in history if r["metric"] == "revenue" and period_kind(r) == "quarter"]
    changes = []
    for current in quarterly_revenues[:4]:
        previous = next(
            (r for r in quarterly_revenues if 330 <= (current["period_end"] - r["period_end"]).days <= 400),
            None,
        )
        if previous and float(previous["value"]) != 0:
            changes.append(
                {
                    "metric": "Revenue year-over-year",
                    "period_end": current["period_end"],
                    "value": float(current["value"]) / float(previous["value"]) - 1,
                    "inputs": [_reference(current), _reference(previous)],
                }
            )
    return {
        "version": VERSION,
        "as_of": asof,
        "metrics": result,
        "changes": changes,
        "history": [
            _metric(labels.get(r["metric"], r["metric"]), r) | {"metric": r["metric"]} for r in history[:120]
        ],
        "limitations": [
            "GAAP only. No consensus estimates or non-GAAP substitutions. Capital investment can include software and intangible assets; inspect source tags.",
            "Operating cash flow and capex can be year-to-date; their periods are shown explicitly.",
            "Historical availability reconstructed from filing metadata is not observed delivery latency.",
        ],
    }


def _reference(row):
    return {
        k: row.get(k)
        for k in [
            "id",
            "tag",
            "value",
            "unit",
            "period_start",
            "period_end",
            "accession",
            "available_at",
            "source_url",
        ]
    }


def _metric(label, row):
    return {
        "label": label,
        "value": float(row["value"]),
        "unit": row["unit"],
        "period_kind": period_kind(row),
        "period_start": row.get("period_start"),
        "period_end": row["period_end"],
        "source_url": row["source_url"],
        "tag": row["tag"],
        "accession": row["accession"],
        "available_at": row["available_at"],
        "inputs": [_reference(row)],
        "unavailable_reason": None,
    }


def _missing(label, reason):
    return {"label": label, "value": None, "unit": None, "unavailable_reason": reason}
