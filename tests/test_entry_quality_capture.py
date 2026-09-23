from __future__ import annotations

import copy
import json
import sqlite3
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from observability import entry_quality
from observability.entry_quality import (
    build_entry_quality_context,
    build_fill_provenance,
    capture_enabled,
    emit_fill_reconciliation,
    validate_completeness_status,
    validate_fill_provenance_status,
)
from observability.trading_context import emit_trading_context


def _feedback_cursor() -> sqlite3.Cursor:
    connection = sqlite3.connect(":memory:")
    cursor = connection.cursor()
    cursor.execute(
        """CREATE TABLE us_analysis_performance_tracker (
               trigger_type TEXT, was_traded INTEGER,
               return_7d REAL, return_14d REAL, return_30d REAL
           )"""
    )
    cursor.execute(
        """CREATE TABLE us_trading_history (
               id INTEGER, trigger_type TEXT, profit_rate REAL, sell_date TEXT
           )"""
    )
    cursor.executemany(
        "INSERT INTO us_analysis_performance_tracker VALUES (?, 0, ?, ?, ?)",
        (
            ("Volume Surge", 0.01, 0.02, 0.03),
            ("Volume Surge", -0.02, -0.01, -0.04),
        ),
    )
    cursor.executemany(
        "INSERT INTO us_trading_history VALUES (?, ?, ?, ?)",
        (
            (1, "Volume Surge", 5.0, "2026-08-01"),
            (2, "Volume Surge", -10.0, "2026-08-02"),
        ),
    )
    return cursor


def test_capture_is_on_by_default_and_has_one_explicit_kill_switch(monkeypatch) -> None:
    monkeypatch.delenv("ENTRY_QUALITY_CAPTURE_ENABLED", raising=False)
    assert capture_enabled() is True

    monkeypatch.setenv("ENTRY_QUALITY_CAPTURE_ENABLED", "0")
    assert capture_enabled() is False
    assert capture_enabled("false") is False
    assert capture_enabled("1") is True


def test_context_uses_only_structured_local_facts_and_keeps_missing_explicit() -> None:
    observed = datetime(2026, 8, 29, 1, 2, 3, tzinfo=timezone.utc)
    context = build_entry_quality_context(
        scenario={
            "entry_checklist_passed": 5,
            "momentum_signal_count": 3,
            "additional_confirmation_count": 2,
            "trading_scenarios": {
                "key_levels": {
                    "primary_support": 95,
                    "secondary_support": 90,
                    "primary_resistance": 115,
                    "secondary_resistance": 125,
                }
            },
        },
        current_price=100,
        cursor=_feedback_cursor(),
        trigger_type="Volume Surge",
        as_of=observed,
        captured_at=observed,
    )

    assert context["context_schema_version"] == 1
    assert context["status"] == "MISSING"
    assert context["missing_components"] == [
        "event_risk",
        "setup_quality.daily",
        "setup_quality.weekly",
    ]
    setup = context["setup_quality"]
    assert setup["status"] == "OK"
    assert setup["entry_position"]["distances_from_entry_pct"] == {
        "primary_support_distance_pct": -5.0,
        "secondary_support_distance_pct": -10.0,
        "primary_resistance_distance_pct": 15.0,
        "secondary_resistance_distance_pct": 25.0,
    }
    assert setup["daily"]["status"] == "MISSING"
    assert setup["weekly"]["status"] == "MISSING"
    assert context["event_risk"]["status"] == "MISSING"
    assert context["trigger_prior"]["status"] == "OK"
    assert context["trigger_prior"]["candidate"]["n"] == 2
    assert context["trigger_prior"]["actual"]["n"] == 2
    assert context["trigger_prior"]["actual"]["median_return_pct"] == -2.5
    assert context["trigger_prior"]["actual"]["profit_factor"] == 0.5


def test_trigger_prior_ignores_prism_us_tracking_package_shadow(monkeypatch) -> None:
    shadow = types.ModuleType("tracking")
    shadow.__path__ = [
        str(Path(__file__).resolve().parents[1] / "prism-us" / "tracking")
    ]
    monkeypatch.setitem(sys.modules, "tracking", shadow)
    monkeypatch.delitem(sys.modules, "tracking.performance_feedback", raising=False)
    entry_quality._load_performance_feedback_module.cache_clear()
    monkeypatch.delitem(
        sys.modules, entry_quality._FEEDBACK_MODULE_NAME, raising=False
    )

    prior = entry_quality.trigger_prior_snapshot(
        _feedback_cursor(), "Volume Surge"
    )

    assert prior["status"] == "OK"
    assert prior["candidate"]["n"] == 2
    assert prior["actual"]["profit_factor"] == 0.5
    loaded = sys.modules[entry_quality._FEEDBACK_MODULE_NAME]
    assert Path(loaded.__file__).resolve() == (
        Path(__file__).resolve().parents[1]
        / "tracking"
        / "performance_feedback.py"
    ).resolve()


def test_context_rejects_future_as_of() -> None:
    captured = datetime(2026, 8, 29, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="future"):
        build_entry_quality_context(
            scenario={},
            current_price=100,
            as_of=captured + timedelta(seconds=1),
            captured_at=captured,
        )


def test_strict_status_enums_reject_unknown_values() -> None:
    assert validate_completeness_status("missing") == "MISSING"
    assert validate_fill_provenance_status("confirmed") == "CONFIRMED"
    with pytest.raises(ValueError):
        validate_completeness_status("PASS")
    with pytest.raises(ValueError):
        validate_fill_provenance_status("FILLED_LIKELY")


def test_fill_provenance_never_promotes_submission_to_confirmed() -> None:
    submitted = build_fill_provenance(
        {
            "success": True,
            "intent_status": "SUBMITTED",
            "intent_broker": "KIS",
            "order_no": "external-order-id",
        }
    )
    queued = build_fill_provenance({"success": True, "intent_status": "QUEUED"})
    rejected = build_fill_provenance({"success": False, "intent_status": "FAILED"})

    assert submitted["status"] == "SUBMITTED_ONLY"
    assert submitted["confirmed_fill_price"] is None
    assert queued["status"] == "UNKNOWN"
    assert rejected["status"] == "REJECTED"


def test_candidate_context_is_optional_and_fill_event_is_fail_open(
    monkeypatch, tmp_path
) -> None:
    spool = tmp_path / "events.jsonl"
    monkeypatch.setenv("PRISM_OBSERVABILITY_SPOOL", str(spool))
    monkeypatch.setenv("ENTRY_QUALITY_CAPTURE_ENABLED", "1")
    quality = build_entry_quality_context(
        scenario={}, current_price=100, trigger_type=None
    )
    candidate = emit_trading_context(
        "candidate.evaluated",
        market="US",
        ticker="AAA",
        decision_id="decision-1",
        entry_quality_context=quality,
    )
    fill = emit_fill_reconciliation(
        market="US",
        ticker="AAA",
        decision_id="decision-1",
        position_id="legacy:US:7",
        intent_id="intent-secret-value",
        result={
            "success": True,
            "intent_status": "SUBMITTED",
            "order_no": "external-order-id",
        },
    )

    assert candidate is not None and fill is not None
    assert candidate["attributes"]["entry_quality_context"]["status"] == "MISSING"
    assert fill["attributes"]["fill_provenance"]["status"] == "SUBMITTED_ONLY"
    assert fill["event_id"] == emit_fill_reconciliation(
        market="US",
        ticker="AAA",
        decision_id="decision-1",
        position_id="legacy:US:7",
        intent_id="intent-secret-value",
        result={"success": True, "intent_status": "SUBMITTED"},
    )["event_id"]
    raw = spool.read_text(encoding="utf-8")
    assert "external-order-id" not in raw
    assert "intent-secret-value" not in raw
    assert len([json.loads(line) for line in raw.splitlines()]) == 3

    monkeypatch.setenv("ENTRY_QUALITY_CAPTURE_ENABLED", "0")
    before = spool.read_text(encoding="utf-8")
    assert emit_fill_reconciliation(
        market="US",
        ticker="AAA",
        decision_id="decision-2",
        position_id=None,
        intent_id="intent-2",
        result={"success": True},
    ) is None
    assert spool.read_text(encoding="utf-8") == before


@pytest.mark.parametrize("market", ["KR", "US"])
def test_market_specific_prior_is_read_only_and_never_crosses_markets(market):
    cursor = _feedback_cursor()
    cursor.execute("""CREATE TABLE analysis_performance_tracker (
        trigger_type TEXT, was_traded INTEGER, tracked_7d_return REAL,
        tracked_14d_return REAL, tracked_30d_return REAL)""")
    cursor.execute("""CREATE TABLE trading_history (
        id INTEGER, trigger_type TEXT, profit_rate REAL, sell_date TEXT)""")
    cursor.execute(
        "INSERT INTO analysis_performance_tracker VALUES ('Volume Surge', 0, .1, .2, .3)"
    )
    cursor.execute(
        "INSERT INTO trading_history VALUES (1, 'Volume Surge', 15, '2026-08-01')"
    )
    cursor.connection.commit()
    writes_before = cursor.connection.total_changes
    statements = []
    cursor.connection.set_trace_callback(statements.append)
    scenario = {"buy_score": 8, "decision": "Enter", "trading_scenarios": {
        "key_levels": {"primary_support": 95, "primary_resistance": 110}
    }}
    original = copy.deepcopy(scenario)
    at = datetime(2026, 9, 23, tzinfo=timezone.utc)
    context = build_entry_quality_context(
        market=market, scenario=scenario, current_price=100, cursor=cursor,
        trigger_type="Volume Surge", as_of=at, captured_at=at,
    )
    assert context["trigger_prior"]["actual"]["median_return_pct"] == (
        15.0 if market == "KR" else -2.5
    )
    assert context["trigger_prior"]["candidate"]["median_30d_pct"] == (
        30.0 if market == "KR" else -0.5
    )
    assert context["extractor_version"] == f"{market.lower()}-local-facts-v1"
    assert context["source"] == f"existing_{market.lower()}_scenario_and_local_feedback"
    assert context["status"] == "MISSING"
    assert scenario == original
    assert cursor.connection.total_changes == writes_before
    assert statements and all(sql.lstrip().upper().startswith("SELECT") for sql in statements)
    cursor.connection.close()


def test_kr_capture_does_not_fall_back_to_us_history():
    cursor = _feedback_cursor()
    context = build_entry_quality_context(
        market="KR", scenario={}, current_price=70000,
        cursor=cursor, trigger_type="Volume Surge",
    )
    assert context["trigger_prior"]["status"] == "MISSING"
    assert context["trigger_prior"]["reason_code"] == "NO_MATURED_TRIGGER_HISTORY"
    cursor.connection.close()


@pytest.mark.parametrize("price,expected", [
    (66500, 66500), ("66,500", 66500), ("66,000 ~ 67,000", 66500),
    ("66000~67000", 66500), ("약 66,500원 지지", None), ("66,50", None),
    ("67000~66000", None), (0, None), (-1, None), (True, None),
    (float("nan"), None), (float("inf"), None), (None, None),
])
def test_kr_structured_price_formats_and_missing_evidence(price, expected):
    context = build_entry_quality_context(
        market="KR", scenario={"trading_scenarios": {
            "key_levels": {"primary_support": price}
        }}, current_price=70000,
    )
    setup = context["setup_quality"]
    assert setup["entry_position"]["levels"].get("primary_support") == expected
    assert setup["status"] == ("OK" if expected else "MISSING")
    if expected:
        assert setup["entry_position"]["distances_from_entry_pct"] == {
            "primary_support_distance_pct": -5.0
        }
    assert setup["daily"]["status"] == "MISSING"
    assert context["event_risk"]["status"] == "MISSING"


def test_market_provenance_keeps_us_default_stable_and_separates_kr_hash():
    at = datetime(2026, 9, 23, tzinfo=timezone.utc)
    args = dict(scenario={}, current_price=100, as_of=at, captured_at=at)
    legacy = build_entry_quality_context(**args)
    explicit_us = build_entry_quality_context(market="US", **args)
    kr = build_entry_quality_context(market=" kr ", **args)
    assert legacy == explicit_us
    assert legacy["input_hash"] == "ca9eff399db9003f16293743"
    assert kr["input_hash"] != explicit_us["input_hash"]
    with pytest.raises(ValueError, match="unsupported"):
        build_entry_quality_context(market="invalid", **args)


def test_kr_capture_flows_to_existing_evidence_packet(monkeypatch, tmp_path):
    from tools.build_entry_quality_evidence_packet import build_evidence_packet

    monkeypatch.setenv("PRISM_OBSERVABILITY_SPOOL", str(tmp_path / "events.jsonl"))
    context = build_entry_quality_context(
        market="KR", scenario={"trading_scenarios": {
            "key_levels": {"primary_support": "66,500", "primary_resistance": "77,000"}
        }}, current_price=70000,
    )
    event = emit_trading_context(
        "candidate.evaluated", market="KR", ticker="005930",
        decision_id="kr-capture-test", entry_quality_context=context,
        decision_context={"decision": "No Entry", "buy_score": 6},
    )
    packet = build_evidence_packet([event], market="KR")
    assert packet["market"] == "KR"
    assert packet["prospective_cohort"]["candidate_count"] == 1
    assert packet["coverage"]["captured_count"] == 1
    assert packet["missingness"]["quality_status_distribution"] == {"MISSING": 1}
    assert packet["data_quality"]["anti_leakage_exclusion_count"] == 0
    assert packet["analysis_rows"][0]["outcomes"]["confirmed_actual_return_pct"] is None
