"""Deterministic spec validation and result validation."""

import copy

import pytest

from app.policy import validate_results, validate_spec
from app.runtime import Container
from app.schema import DashboardSpec
from tests.conftest import user

BASE = {
    "schema_version": "1.0",
    "title": "SME Credit Performance",
    "dataset_id": "demo_sme_credit",
    "time_range": {"field": "report_month", "start": "2025-01-01", "end": "2025-12-01", "grain": "month"},
    "metrics": [{"metric_id": "total_outstanding", "aggregation": "approved_formula"}],
    "dimensions": ["region", "report_month"],
    "filters": [],
    "visualizations": [
        {"id": "kpi", "type": "kpi_card", "title": "Total", "metric_ids": ["total_outstanding"]},
        {"id": "monthly_trend", "type": "line", "title": "Trend", "metric_ids": ["total_outstanding"], "dimension_ids": ["report_month"]},
        {"id": "regional_comparison", "type": "bar", "title": "By region", "metric_ids": ["total_outstanding"], "dimension_ids": ["region"], "sort": "desc"},
    ],
    "layout": "executive_overview",
    "assumptions": [],
    "warnings": [],
}


@pytest.fixture(scope="module")
def c():
    return Container()


def check(c, spec, role="business_user"):
    return validate_spec(spec, c.catalog, user(role), cardinality=c.cardinality, coverage=c.coverage("demo_sme_credit"))


def mutate(**changes):
    s = copy.deepcopy(BASE)
    for path, val in changes.items():
        cur = s
        keys = path.split("__")
        for k in keys[:-1]:
            cur = cur[int(k)] if k.isdigit() else cur[k]
        last = keys[-1]
        if last.isdigit():
            cur[int(last)] = val
        else:
            cur[last] = val
    return s


def codes(rep):
    return {e["code"] for e in rep.errors}


def test_prd_shaped_spec_is_valid(c):
    rep, spec = check(c, BASE)
    assert rep.valid, rep.errors


def test_extra_keys_rejected(c):
    s = copy.deepcopy(BASE)
    s["sql"] = "SELECT * FROM customers"
    rep, _ = check(c, s)
    assert "SCHEMA_INVALID" in codes(rep)
    s = copy.deepcopy(BASE)
    s["metrics"][0]["formula"] = "SUM(x)*2"
    assert "SCHEMA_INVALID" in codes(check(c, s)[0])


def test_unknown_metric_rejected(c):
    rep, _ = check(c, mutate(metrics=[{"metric_id": "npl_ratio", "aggregation": "approved_formula"}]))
    assert "METRIC_NOT_APPROVED" in codes(rep)


def test_metric_from_other_dataset_rejected(c):
    rep, _ = check(c, mutate(metrics=[{"metric_id": "total_deposit_balance", "aggregation": "approved_formula"}]))
    assert "METRIC_DATASET_MISMATCH" in codes(rep)


def test_measure_field_cannot_be_dimension(c):
    rep, _ = check(c, mutate(dimensions=["outstanding_amount"]))
    assert "DIMENSION_NOT_ALLOWED" in codes(rep)


def test_unentitled_dataset_rejected(c):
    s = mutate(dataset_id="demo_deposit_funding", metrics=[{"metric_id": "total_deposit_balance", "aggregation": "approved_formula"}])
    rep, _ = check(c, s, role="business_user")
    assert "DATASET_NOT_ENTITLED" in codes(rep)


def test_filter_value_allowlist(c):
    rep, _ = check(c, mutate(filters=[{"field": "region", "op": "in", "values": ["Atlantis"]}]))
    assert "FILTER_VALUE_NOT_ALLOWED" in codes(rep)
    rep, _ = check(c, mutate(filters=[{"field": "outstanding_amount", "op": "in", "values": ["1"]}]))
    assert "FILTER_NOT_ALLOWED" in codes(rep)


def test_chart_compatibility(c):
    rep, _ = check(c, mutate(visualizations__1__dimension_ids=["region"]))  # line without time axis
    assert "CHART_INCOMPATIBLE" in codes(rep)
    rep, _ = check(c, mutate(visualizations__0__dimension_ids=["region"]))  # KPI card with breakdown
    assert "CHART_INCOMPATIBLE" in codes(rep)
    rep, _ = check(c, mutate(visualizations__1__highlight_top_n=3))  # highlight on line
    assert "CHART_INCOMPATIBLE" in codes(rep)


def test_too_many_series(c):
    s = mutate(dimensions=["branch_code", "report_month"], visualizations__1__dimension_ids=["report_month", "branch_code"],
               visualizations__2__dimension_ids=["branch_code"])
    rep, _ = check(c, s)
    assert "TOO_MANY_SERIES" in codes(rep)
    s["visualizations"][1]["top_n"] = 5
    assert check(c, s)[0].valid


def test_undeclared_metric_in_viz(c):
    rep, _ = check(c, mutate(visualizations__2__metric_ids=["total_disbursement"]))
    assert "VIZ_METRIC_UNDECLARED" in codes(rep)


def test_time_range_rules(c):
    rep, _ = check(c, mutate(time_range={"field": "region", "start": "2025-01-01", "end": "2025-12-01", "grain": "month"}))
    assert "TIME_FIELD_INVALID" in codes(rep)
    rep, _ = check(c, mutate(time_range={"field": "report_month", "start": "2025-12-01", "end": "2025-01-01", "grain": "month"}))
    assert "TIME_RANGE_INVALID" in codes(rep)


def test_result_validation_and_reconciliation(c):
    rep, spec = check(c, BASE)
    results = c.data.execute_validated_plan(spec, user("business_user"))
    rr = validate_results(spec, results, c.catalog, max_rows=5000, max_series=12)
    assert rr.valid, rr.errors
    names = {ch["name"] for ch in rr.checks}
    assert "reconcile:kpi~monthly_trend" in names and "reconcile:kpi~regional_comparison" in names


def test_reconciliation_detects_tampered_results(c):
    _, spec = check(c, BASE)
    results = c.data.execute_validated_plan(spec, user("business_user"))
    results[2].rows[0]["total_outstanding"] += 1_000_000
    rr = validate_results(spec, results, c.catalog, max_rows=5000, max_series=12)
    assert "RECONCILIATION_FAILED" in codes(rr)


def test_result_type_check(c):
    _, spec = check(c, BASE)
    results = c.data.execute_validated_plan(spec, user("business_user"))
    results[1].rows[0]["total_outstanding"] = float("nan")
    rr = validate_results(spec, results, c.catalog, max_rows=5000, max_series=12)
    assert "RESULT_TYPE" in codes(rr)


def test_spec_model_roundtrip():
    assert DashboardSpec.model_validate(BASE).model_dump(mode="json")["visualizations"][2]["sort"] == "desc"
