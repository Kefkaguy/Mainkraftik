from datetime import UTC, date, datetime

from intelligence.financials import calculate_metrics, normalize_facts, select_versions


def fact(
    metric,
    value,
    tag,
    start=date(2025, 1, 1),
    end=date(2025, 3, 31),
    available=datetime(2025, 5, 1, tzinfo=UTC),
):
    return {
        "id": 1,
        "symbol": "MSI",
        "metric": metric,
        "value": value,
        "tag": tag,
        "period_start": start,
        "period_end": end,
        "unit": "USD",
        "available_at": available,
        "acquired_at": available,
        "accession": str(available),
        "source_url": "https://www.sec.gov/Archives/example",
    }


def test_restatement_is_withheld_before_its_filing():
    old = fact("revenue", 100, "Revenues")
    restated = fact("revenue", 90, "Revenues", available=datetime(2026, 5, 1, tzinfo=UTC))
    assert select_versions([old, restated], datetime(2025, 6, 1, tzinfo=UTC))[0]["value"] == 100
    assert select_versions([old, restated], datetime(2026, 6, 1, tzinfo=UTC))[0]["value"] == 90


def test_matching_periods_and_source_inputs():
    rows = [
        fact("revenue", 100, "Revenues"),
        fact("operating_income", 25, "OperatingIncomeLoss"),
        fact("operating_cash_flow", 30, "NetCashProvidedByUsedInOperatingActivities"),
        fact("capex", 10, "PaymentsToAcquirePropertyPlantAndEquipment"),
    ]
    result = calculate_metrics(rows, datetime(2025, 6, 1, tzinfo=UTC))["metrics"]
    assert result["operating_margin"]["value"] == 0.25
    assert result["free_cash_flow"]["value"] == 20
    assert len(result["free_cash_flow"]["inputs"]) == 2


def test_quarter_and_ytd_never_mixed():
    rows = [
        fact("operating_cash_flow", 30, "NetCashProvidedByUsedInOperatingActivities"),
        fact("capex", 10, "PaymentsToAcquirePropertyPlantAndEquipment", start=date(2024, 10, 1)),
    ]
    assert (
        calculate_metrics(rows, datetime(2025, 6, 1, tzinfo=UTC))["metrics"]["free_cash_flow"]["value"]
        is None
    )


def test_instant_debt_requires_noncurrent_component():
    rows = [fact("current_debt", 5, "LongTermDebtCurrent", start=None)]
    assert calculate_metrics(rows, datetime(2025, 6, 1, tzinfo=UTC))["metrics"]["debt"]["value"] is None


def test_currency_and_non_gaap_are_not_substituted():
    payload = {
        "cik": 68505,
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "EUR": [
                            {
                                "val": 100,
                                "end": "2025-03-31",
                                "filed": "2025-05-01",
                                "accn": "x",
                                "form": "10-Q",
                            }
                        ]
                    }
                }
            },
            "custom": {"AdjustedEarnings": {"units": {"USD": []}}},
        },
    }
    assert normalize_facts("MSI", payload, {}, datetime(2025, 6, 1, tzinfo=UTC)) == []


def test_filed_date_without_exact_acceptance_is_conservative():
    payload = {
        "cik": 68505,
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {
                                "val": 100,
                                "start": "2025-01-01",
                                "end": "2025-03-31",
                                "filed": "2025-05-01",
                                "accn": "x",
                                "form": "10-Q",
                            }
                        ]
                    }
                }
            }
        },
    }
    row = normalize_facts("MSI", payload, {}, datetime(2025, 6, 1, tzinfo=UTC))[0]
    assert row["available_at"] == datetime(2025, 5, 2, 4, tzinfo=UTC)


def test_observed_only_withholds_late_acquisition():
    row = fact("revenue", 100, "Revenues")
    row["acquired_at"] = datetime(2026, 1, 1, tzinfo=UTC)
    assert select_versions([row], datetime(2025, 6, 1, tzinfo=UTC), observed_only=True) == []


def test_superseded_ambiguous_debt_fact_is_retained_but_not_used():
    row = fact("long_term_debt", 100, "LongTermDebt", start=None)
    assert select_versions([row], datetime(2025, 6, 1, tzinfo=UTC)) == []
