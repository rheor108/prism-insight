from __future__ import annotations

import ast
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tools import export_observability_insights as exporter

NOW = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


def _candidate(identity="candidate", market="KR", **extra):
    return {
        "event_id": identity, "event_type": "candidate.evaluated",
        "timestamp": NOW.isoformat(), "market": market, "decision_id": identity,
        "attributes": {"entry_quality_context": {"status": "MISSING"}, **extra},
    }


def _spool(tmp_path, events):
    path = tmp_path / "events.jsonl"
    path.write_text("".join(json.dumps(event) + "\n" for event in events))
    return path


def test_local_export_preserves_market_capture_counts_without_raw_data(tmp_path):
    kr = _candidate(api_key="must-not-leak", rationale="private prompt")
    path = _spool(tmp_path, [kr, kr, _candidate("us-1", "US")])
    original = path.read_bytes()
    output = tmp_path / "public" / "observability_insights.json"
    snapshot = exporter.export_local_snapshot(path, output, now=NOW)
    assert json.loads(output.read_text()) == snapshot
    assert snapshot["source"]["kind"] == "local_jsonl"
    assert snapshot["data_quality"]["total_events"] == 2
    for market in ("KR", "US"):
        assert snapshot["markets"][market]["entry_quality_capture"]["captured_count"] == 1
    assert "must-not-leak" not in output.read_text()
    assert "private prompt" not in output.read_text()
    assert str(path) not in output.read_text()
    assert path.read_bytes() == original


def test_local_reader_filters_retention_future_and_partial_lines(tmp_path):
    old = _candidate("old")
    old["timestamp"] = (NOW - timedelta(days=181)).isoformat()
    future = _candidate("future")
    future["timestamp"] = (NOW + timedelta(seconds=1)).isoformat()
    path = _spool(tmp_path, [_candidate(), old, future, [], {"event_id": "bad"}])
    with path.open("ab") as stream:
        stream.write(b'not-json\n{"event_id": "partial')
    snapshot = exporter.export_local_snapshot(path, tmp_path / "out.json", now=NOW)
    assert snapshot["data_quality"]["total_events"] == 1
    assert snapshot["source"] == {
        "kind": "local_jsonl", "invalid_lines": 3, "incomplete_tail_lines": 1,
    }


@pytest.mark.parametrize("bad_input", ["missing", "invalid"])
def test_input_failure_retains_last_good_snapshot(tmp_path, bad_input):
    source = tmp_path / "events.jsonl"
    if bad_input == "invalid":
        source.write_text("invalid-json\n")
    output = tmp_path / "out.json"
    output.write_text('{"last_good": true}')
    with pytest.raises((FileNotFoundError, ValueError)):
        exporter.export_local_snapshot(source, output, now=NOW)
    assert json.loads(output.read_text()) == {"last_good": True}


def test_snapshot_serialization_failure_keeps_previous_and_cleans_temp(tmp_path):
    output = tmp_path / "out.json"
    exporter.write_snapshot(output, {"value": 1})
    with pytest.raises(ValueError):
        exporter.write_snapshot(output, {"value": float("nan")})
    assert json.loads(output.read_text()) == {"value": 1}
    assert sorted(path.name for path in tmp_path.iterdir()) == ["out.json"]


def test_concurrent_snapshot_writers_publish_complete_json(tmp_path):
    output = tmp_path / "out.json"
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda index: exporter.write_snapshot(output, {"value": index}), range(8)))
    assert json.loads(output.read_text())["value"] in range(8)
    assert sorted(path.name for path in tmp_path.iterdir()) == ["out.json"]


def test_local_cli_does_not_query_clickhouse_and_cannot_overwrite_spool(tmp_path, monkeypatch):
    source = _spool(tmp_path, [_candidate()])
    monkeypatch.setattr(exporter, "load_clickhouse_events", MagicMock(side_effect=AssertionError("network forbidden")))
    output = tmp_path / "out.json"
    assert exporter.main(["--input", str(source), "--output", str(output)]) == 0
    assert json.loads(output.read_text())["source"]["kind"] == "local_jsonl"
    before = source.read_bytes()
    with pytest.raises(ValueError, match="overwrite"):
        exporter.export_local_snapshot(source, source)
    assert source.read_bytes() == before


@pytest.mark.parametrize("script,cls", [
    ("generate_dashboard_json.py", "DashboardDataGenerator"),
    ("generate_us_dashboard_json.py", "USDashboardDataGenerator"),
])
@pytest.mark.parametrize("with_local_input", [False, True])
def test_dashboard_main_opt_in_export_survives_portfolio_failure(
    tmp_path, monkeypatch, script, cls, with_local_input
):
    # Exercise the real CLI main without importing its live broker/model setup.
    tree = ast.parse((ROOT / "examples" / script).read_text())
    main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
    fake_generator = MagicMock()
    fake_generator.return_value.generate.side_effect = RuntimeError("portfolio unavailable")
    namespace = {
        "Path": Path, "SCRIPT_DIR": tmp_path,
        "_cfg": {"default_mode": "demo"}, "logger": logging.getLogger(__name__),
        cls: fake_generator,
    }
    exec(compile(ast.Module(body=[main], type_ignores=[]), script, "exec"), namespace)
    output = tmp_path / "dashboard" / "public" / "observability_insights.json"
    output.parent.mkdir(parents=True)
    output.write_text('{"remote_snapshot": true}')
    source = _spool(tmp_path, [_candidate()])
    args = [script, "--no-translation"]
    if with_local_input:
        args.extend(["--observability-input", str(source)])
    monkeypatch.setattr(sys, "argv", args)
    with pytest.raises(SystemExit):
        namespace["main"]()
    snapshot = json.loads(output.read_text())
    if with_local_input:
        assert snapshot["source"]["kind"] == "local_jsonl"
    else:
        assert snapshot == {"remote_snapshot": True}


def test_nullable_legacy_contexts_do_not_prevent_local_export(tmp_path):
    event = _candidate(policy_context={
        "journal_influence_context": {"status": "MISSING", "deterministic_effect": None},
        "journal_reflection": None,
    })
    event["attributes"]["entry_quality_context"]["setup_quality"] = None
    no_policy = _candidate("no-policy", policy_context=None)
    fill = {**_candidate("fill"), "event_type": "entry.fill_reconciled",
            "attributes": {"fill_provenance": None}}
    snapshot = exporter.export_local_snapshot(
        _spool(tmp_path, [event, no_policy, fill]), tmp_path / "out.json", now=NOW,
    )
    assert snapshot["data_quality"]["total_events"] == 3
    journal = snapshot["markets"]["KR"]["journal_influence_capture"]
    assert journal["llm_referenced_count"] == 0
    assert journal["deterministic_adjustment_count"] == 0
