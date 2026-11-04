"""KPI correctness against independently computed expected values, plus edge cases."""

from datetime import date
from decimal import Decimal

import pytest

from app.catalog import get_catalog
from app.data_provider import SyntheticDataAdapter
from app.runtime import Container
from tests.conftest import EXPECTED

E = EXPECTED["demo_sme_credit"]


@pytest.fixture(scope="module")
def c():
    return Container()


def q(c, metrics, dims=(), grain=None, start=date(2025, 1, 1), end=date(2025, 12, 1), filters=(), adapter=None):
    cat = c.catalog
    ds = cat.dataset("demo_sme_credit")
    return (adapter or c.data).query(ds, [cat.metric(m) for m in metrics], list(dims), grain, start, end, list(filters))


def test_dataset_loaded(c):
    assert c.data.get_data_freshness("demo_sme_credit")["row_count"] == E["row_count"]


def test_total_outstanding_is_period_end_not_summed(c):
    r = q(c, ["total_outstanding"])
    assert r.rows[0]["total_outstanding"] == E["total_outstanding_2025_12"]


def test_outstanding_by_region(c):
    r = q(c, ["total_outstanding"], ["region"])
    assert {row["region"]: row["total_outstanding"] for row in r.rows} == E["total_outstanding_by_region_2025_12"]


def test_outstanding_by_month(c):
    r = q(c, ["total_outstanding"], grain="month")
    assert {row["period"]: row["total_outstanding"] for row in r.rows} == E["total_outstanding_by_month_2025"]


def test_disbursement_is_flow_summed(c):
    assert q(c, ["total_disbursement"]).rows[0]["total_disbursement"] == E["total_disbursement_2025"]


def test_target_achievement_ratio(c):
    v = q(c, ["target_achievement_pct"]).rows[0]["target_achievement_pct"]
    assert v == pytest.approx(E["target_achievement_pct_2025_12"], abs=1e-4)


def test_growth_over_range_uses_month_before_start(c):
    v = q(c, ["outstanding_growth_pct"]).rows[0]["outstanding_growth_pct"]
    assert v == pytest.approx(E["outstanding_growth_pct_2025_vs_2024_12"], abs=1e-4)


def test_monthly_growth_first_month_uses_prior_data(c):
    rows = q(c, ["outstanding_growth_pct"], grain="month").rows
    by = {r["period"]: r["outstanding_growth_pct"] for r in rows}
    months = E["total_outstanding_by_month_2025"]
    expected_feb = (months["2025-02-01"] / months["2025-01-01"] - 1) * 100
    assert by["2025-02-01"] == pytest.approx(expected_feb, abs=1e-6)
    assert by["2025-01-01"] is not None  # Dec 2024 exists in the dataset


def test_growth_blank_when_no_prior_period(c):
    r = q(c, ["outstanding_growth_pct"], grain="month", start=date(2024, 1, 1), end=date(2024, 3, 1))
    by = {row["period"]: row["outstanding_growth_pct"] for row in r.rows}
    assert by["2024-01-01"] is None
    assert any("previous period is missing" in w for w in r.warnings)


def test_filters_restrict_rows(c):
    from app.schema import FilterSpec

    r = q(c, ["total_outstanding"], ["region"], filters=[FilterSpec(field="region", values=["Jakarta"])])
    assert [row["region"] for row in r.rows] == ["Jakarta"]
    assert r.rows[0]["total_outstanding"] == E["total_outstanding_by_region_2025_12"]["Jakarta"]


def test_quarter_grain_period_end(c):
    r = q(c, ["total_outstanding"], grain="quarter")
    by = {row["period"]: row["total_outstanding"] for row in r.rows}
    assert by["2025-10-01"] == E["total_outstanding_2025_12"]  # Q4 balance = December balance
    assert len(by) == 4


# --- edge-case fixture: nulls, missing months, zero denominators, duplicates, empty ------
def _row(m, region, out, target=100, disb=10):
    return {"report_month": date(2025, m, 1), "region": region, "branch_code": "SYN-X", "product_segment": "Kecil",
            "outstanding_amount": None if out is None else Decimal(out), "disbursement_amount": None if disb is None else Decimal(disb),
            "target_amount": None if target is None else Decimal(target), "account_count": 1}


@pytest.fixture
def edge(c):
    rows = [
        _row(1, "A", 100), _row(2, "A", 110), _row(3, "A", 121),
        _row(1, "B", 50), _row(3, "B", 60),                      # B missing February
        _row(1, "C", 10, target=0), _row(2, "C", 0, target=0), _row(3, "C", 20, target=0),  # zero target / zero base
        _row(3, "D", None, disb=None),                            # nulls
        _row(3, "E", 5), _row(3, "E", 5),                         # duplicate rows are summed as data
    ]
    return SyntheticDataAdapter.from_rows(get_catalog(), "demo_sme_credit", rows)


def test_edge_missing_month_and_growth(c, edge):
    r = q(c, ["outstanding_growth_pct"], ["region"], grain="month", start=date(2025, 2, 1), end=date(2025, 3, 1), adapter=edge)
    by = {(x["region"], x["period"]): x["outstanding_growth_pct"] for x in r.rows}
    assert by[("A", "2025-03-01")] == pytest.approx(10.0)
    assert ("B", "2025-02-01") not in by            # no data -> no row (not a fabricated zero)
    assert by[("B", "2025-03-01")] is None          # previous month missing -> blank
    assert by[("C", "2025-03-01")] is None          # previous value zero -> blank


def test_edge_zero_denominator_is_blank_with_warning(c, edge):
    r = q(c, ["target_achievement_pct"], ["region"], start=date(2025, 3, 1), end=date(2025, 3, 1), adapter=edge)
    by = {x["region"]: x["target_achievement_pct"] for x in r.rows}
    assert by["C"] is None and by["A"] == pytest.approx(121.0)
    assert any("denominator is zero" in w for w in r.warnings)


def test_edge_nulls_excluded_and_reported(c, edge):
    r = q(c, ["total_outstanding"], ["region"], start=date(2025, 3, 1), end=date(2025, 3, 1), adapter=edge)
    by = {x["region"]: x["total_outstanding"] for x in r.rows}
    assert by["D"] is None
    assert by["E"] == 10.0
    assert r.null_values_excluded >= 1


def test_edge_period_end_uses_latest_available_month_with_warning(c, edge):
    r = q(c, ["total_outstanding"], ["region"], start=date(2025, 1, 1), end=date(2025, 2, 1), adapter=edge)
    by = {x["region"]: x["total_outstanding"] for x in r.rows}
    assert by["B"] == 50.0  # January is B's latest month in the range
    assert any("latest available month" in w for w in r.warnings)


def test_edge_empty_result(c, edge):
    r = q(c, ["total_outstanding"], start=date(2024, 1, 1), end=date(2024, 2, 1), adapter=edge)
    assert r.rows == []
    assert any("No data matched" in w for w in r.warnings)
