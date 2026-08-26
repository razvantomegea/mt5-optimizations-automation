"""Unit tests for linear RISK scaling and equity DD parsing (no MT5 required)."""

from __future__ import annotations

from pathlib import Path

from mt5_batch_optimize import (
    DEFAULT_MAX_EQUITY_DD,
    DEFAULT_MIN_SCALED_RISK,
    DEFAULT_TARGET_EQUITY_DD,
    compute_scaled_risk,
    extract_backtest_equity_dd_pct,
    finalize_scaled_risk,
    parse_equity_drawdown_relative_pct,
)


def test_compute_scaled_risk_scales_down_to_half() -> None:
    """RISK 1 at 10% DD with target 5% → RISK 0.5."""
    assert (
        compute_scaled_risk(
            baseline_risk=1.0,
            baseline_dd_pct=10.0,
            target_dd_pct=5.0,
            risk_round_decimals=1,
        )
        == 0.5
    )


def test_compute_scaled_risk_scales_down_ten_to_four() -> None:
    """Operator example: RISK 1 at 10% DD with target 4% → RISK 0.4."""
    assert (
        compute_scaled_risk(
            baseline_risk=1.0,
            baseline_dd_pct=10.0,
            target_dd_pct=4.0,
            risk_round_decimals=1,
        )
        == 0.4
    )


def test_compute_scaled_risk_scales_up_two_to_four() -> None:
    """RISK 1 at 2% DD with target 4% → RISK 2.0."""
    assert (
        compute_scaled_risk(
            baseline_risk=1.0,
            baseline_dd_pct=2.0,
            target_dd_pct=4.0,
            risk_round_decimals=1,
        )
        == 2.0
    )


def test_compute_scaled_risk_returns_none_when_baseline_dd_nonpositive() -> None:
    assert (
        compute_scaled_risk(
            baseline_risk=1.0,
            baseline_dd_pct=0.0,
            target_dd_pct=5.0,
            risk_round_decimals=1,
        )
        is None
    )
    assert (
        compute_scaled_risk(
            baseline_risk=1.0,
            baseline_dd_pct=-1.0,
            target_dd_pct=5.0,
            risk_round_decimals=1,
        )
        is None
    )


def test_finalize_scaled_risk_keeps_half_when_above_floor() -> None:
    """Scale-down below 1 is allowed; 0.5 is not rejected when above the floor."""
    assert finalize_scaled_risk(0.5, min_scaled_risk=0.1) == 0.5


def test_finalize_scaled_risk_clamps_to_configured_floor() -> None:
    """A higher floor still clamps (does not reject)."""
    assert finalize_scaled_risk(0.5, min_scaled_risk=1.0) == 1.0


def test_finalize_scaled_risk_clamps_below_floor_to_min() -> None:
    """Rounding to 0.0 would disable risk sizing; clamp to floor and continue."""
    # 0.04 rounded to 1 decimal is 0.0; clamp to min 0.1
    rounded = round(0.04, 1)
    assert rounded == 0.0
    assert finalize_scaled_risk(rounded, min_scaled_risk=0.1) == 0.1
    assert finalize_scaled_risk(0.04, min_scaled_risk=0.1) == 0.1


def test_finalize_scaled_risk_returns_none_for_invalid_inputs() -> None:
    assert finalize_scaled_risk(None, min_scaled_risk=0.1) is None
    assert finalize_scaled_risk(-0.5, min_scaled_risk=0.1) is None


def test_default_min_scaled_risk_is_technical_floor() -> None:
    assert DEFAULT_MIN_SCALED_RISK == 0.1


def test_default_max_equity_dd_is_target_times_headroom() -> None:
    assert DEFAULT_MAX_EQUITY_DD == DEFAULT_TARGET_EQUITY_DD * 1.12
    assert DEFAULT_MAX_EQUITY_DD == 16.8


def test_parse_equity_drawdown_relative_pct_percent_first() -> None:
    assert parse_equity_drawdown_relative_pct("12.34% (1 234.56)") == 12.34


def test_parse_equity_drawdown_relative_pct_money_first() -> None:
    assert parse_equity_drawdown_relative_pct("1 234.56 (12.34%)") == 12.34
    assert parse_equity_drawdown_relative_pct("18432.10 (18.43%)") == 18.43


def test_parse_equity_drawdown_relative_pct_plain_number() -> None:
    assert parse_equity_drawdown_relative_pct("15.5") == 15.5
    assert parse_equity_drawdown_relative_pct(9.1) == 9.1


def test_extract_backtest_equity_dd_pct_percent_first_html(tmp_path: Path) -> None:
    report = tmp_path / "pct_first.htm"
    report.write_text(
        "Equity Drawdown Relative:</td><td align=right><b>12.34% (1 234.56)</b>",
        encoding="utf-8",
    )
    assert extract_backtest_equity_dd_pct(report) == 12.34


def test_extract_backtest_equity_dd_pct_money_first_html(tmp_path: Path) -> None:
    report = tmp_path / "money_first.htm"
    report.write_text(
        "Equity Drawdown Relative:</td><td align=right><b>18 432.10 (18.43%)</b>",
        encoding="utf-8",
    )
    assert extract_backtest_equity_dd_pct(report) == 18.43


def test_extract_backtest_equity_dd_pct_ignores_balance_zero_on_same_row(
    tmp_path: Path,
) -> None:
    """Balance Relative 0.00% must not win over Equity Relative on the same row."""
    report = tmp_path / "same_row.htm"
    report.write_text(
        "<tr>"
        "<td nowrap colspan=3>Balance Drawdown Relative:</td>"
        "<td nowrap><b>0.00% (0.00)</b></td>"
        "<td nowrap colspan=2>Equity Drawdown Relative:</td>"
        "<td nowrap><b>5.55% (5 550.00)</b></td>"
        "</tr>",
        encoding="utf-8",
    )
    assert extract_backtest_equity_dd_pct(report) == 5.55


def test_extract_backtest_equity_dd_pct_without_bold_tags(tmp_path: Path) -> None:
    report = tmp_path / "no_bold.htm"
    report.write_text(
        "<td>Equity Drawdown Relative:</td><td>4.90% (4 900.00)</td>",
        encoding="utf-8",
    )
    assert extract_backtest_equity_dd_pct(report) == 4.90


def test_assert_backtest_report_usable_rejects_empty_stub(tmp_path: Path) -> None:
    from mt5_batch_optimize import assert_backtest_report_usable
    import pytest

    report = tmp_path / "stub.htm"
    report.write_text(
        "<td>Initial deposit:</td><td><b>0</b></td>"
        "<td>Bars:</td><td><b>0</b></td>"
        "<td>Total Trades:</td><td><b>0</b></td>"
        "<td>Equity Drawdown Relative:</td><td><b>0% (0)</b></td>",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="Empty MT5 backtest stub"):
        assert_backtest_report_usable(report, expected_deposit="100000")


def test_assert_backtest_report_usable_accepts_real_report(tmp_path: Path) -> None:
    from mt5_batch_optimize import assert_backtest_report_usable

    report = tmp_path / "ok.htm"
    report.write_text(
        "<td>Initial deposit:</td><td><b>100 000.00</b></td>"
        "<td>Bars:</td><td><b>294181</b></td>"
        "<td>Total Trades:</td><td><b>2679</b></td>"
        "<td>Equity Drawdown Relative:</td><td><b>5.55% (8 103.01)</b></td>",
        encoding="utf-8",
    )
    assert_backtest_report_usable(report, expected_deposit="100000")


def test_assert_backtest_report_usable_rejects_deposit_mismatch(tmp_path: Path) -> None:
    from mt5_batch_optimize import assert_backtest_report_usable
    import pytest

    report = tmp_path / "mismatch.htm"
    report.write_text(
        "<td>Initial deposit:</td><td><b>50 000.00</b></td>"
        "<td>Bars:</td><td><b>294181</b></td>"
        "<td>Total Trades:</td><td><b>100</b></td>"
        "<td>Equity Drawdown Relative:</td><td><b>2.00% (1 000.00)</b></td>",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="expected deposit"):
        assert_backtest_report_usable(report, expected_deposit="100000")
