"""Unit tests for Skip Robustness gate helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mt5_skip_robustness import (
    EXPECTED_SKIP_COMBINATIONS,
    EXIT_INCOMPLETE,
    SUMMARY_CSV_FIELDNAMES,
    _write_csv_rows,
    apply_skip_robustness_outcome,
    evaluate_skip_robustness_gate,
    load_optimization_equity_dds,
    select_auto_stress_survivors,
    skip_robustness_ceiling,
)
from mt5_stable_result_id import stable_result_id


def test_ceiling_adds_one_pp() -> None:
    assert skip_robustness_ceiling(11.4) == 12.4


def test_gate_passes_when_all_under_ceiling() -> None:
    dds = [10.0] * EXPECTED_SKIP_COMBINATIONS
    gate = evaluate_skip_robustness_gate(combo_equity_dds=dds, baseline_dd_pct=11.0)
    assert gate.passed
    assert gate.reject_reason == ""


def test_gate_fails_when_one_over_ceiling() -> None:
    dds = [10.0] * (EXPECTED_SKIP_COMBINATIONS - 1) + [12.5]
    gate = evaluate_skip_robustness_gate(combo_equity_dds=dds, baseline_dd_pct=11.0)
    assert not gate.passed
    assert gate.reject_reason == "robustness_failed"


def test_gate_fails_when_too_few_combos() -> None:
    gate = evaluate_skip_robustness_gate(
        combo_equity_dds=[1.0] * 10,
        baseline_dd_pct=11.0,
    )
    assert not gate.passed
    assert gate.incomplete
    assert gate.reject_reason == "robustness_incomplete"
    assert EXIT_INCOMPLETE == 3


def test_select_auto_top_five() -> None:
    rows = [
        {"keep": True, "validation_score": 1, "validation_recovery": 1, "pass_id": 1},
        {"keep": True, "validation_score": 9, "validation_recovery": 1, "pass_id": 2},
        {"keep": True, "validation_score": 5, "validation_recovery": 1, "pass_id": 3},
        {"keep": False, "validation_score": 99, "validation_recovery": 1, "pass_id": 4},
        {"keep": True, "validation_score": 8, "validation_recovery": 1, "pass_id": 5},
        {"keep": True, "validation_score": 7, "validation_recovery": 1, "pass_id": 6},
        {"keep": True, "validation_score": 6, "validation_recovery": 1, "pass_id": 7},
        {"keep": True, "validation_score": 4, "validation_recovery": 1, "pass_id": 8},
    ]
    selected = select_auto_stress_survivors(rows, limit=5)
    assert [r["pass_id"] for r in selected] == [2, 5, 6, 7, 3]


def test_apply_outcome_fail_clears_keep() -> None:
    gate = evaluate_skip_robustness_gate(
        combo_equity_dds=[20.0] * EXPECTED_SKIP_COMBINATIONS,
        baseline_dd_pct=11.0,
    )
    updated = apply_skip_robustness_outcome(
        {"keep": True, "validation_pass": True, "reject_reason": ""},
        gate,
    )
    assert updated["keep"] is False
    assert updated["validation_pass"] is False
    assert "robustness_failed" in updated["reject_reason"]
    assert updated["skip_robustness_run"] is True


def test_write_csv_rows_empty_writes_header(tmp_path: Path) -> None:
    path = tmp_path / "best_survivors.csv"
    _write_csv_rows(path, [])
    text = path.read_text(encoding="utf-8")
    assert text.strip()
    header = text.strip().splitlines()[0]
    for name in SUMMARY_CSV_FIELDNAMES[:5]:
        assert name in header


def test_load_optimization_equity_dds_requires_equity_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mapping = MagicMock()
    mapping.equity_dd = None
    monkeypatch.setattr(
        "mt5_skip_robustness.worksheet_rows",
        lambda _path: ("t", ["Pass"], [{}]),
    )
    monkeypatch.setattr(
        "mt5_skip_robustness.resolve_column_mapping",
        lambda _headers, _overrides: mapping,
    )
    with pytest.raises(RuntimeError, match="Equity DD column unresolved"):
        load_optimization_equity_dds(Path("missing.xml"))


def test_stable_result_id_matches_ts_fixture() -> None:
    # Must stay in sync with lib/optimizer/reporter-store.test.ts
    assert (
        stable_result_id(run_id="run-1", job_index=1, pass_id=8635)
        == "721a59b503a9df6aa0b92c4bb0a80a1d3dfdb3016d63b4ab6f92da2b3050693f"
    )
