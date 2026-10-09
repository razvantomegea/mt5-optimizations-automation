"""Tests for max FIFO holding-days metric and validation gate."""

from __future__ import annotations

import argparse

import pytest

from mt5_batch_optimize import (
    DEFAULT_MAX_HOLDING_DAYS,
    ValidationThresholds,
    _holding_period_reject_reason,
    _validation_passes,
    _validation_thresholds_from_args,
)
from mt5_equity_metrics import (
    EquityQualityMetrics,
    compute_max_holding_days_from_html,
)
from portfolio_test_helpers import deal_row, sample_deals_report_html


def _equity(
    *,
    calmar: float = 2.0,
    max_holding_days: float | None = 10.0,
) -> EquityQualityMetrics:
    return EquityQualityMetrics(
        lr_correlation=0.9,
        lr_std_error=0.1,
        cagr_pct=20.0,
        calmar=calmar,
        k_ratio_proxy=1.0,
        ulcer_index=1.0,
        max_stagnation_days=1,
        time_under_water_pct=10.0,
        initial_balance=100_000.0,
        final_balance=120_000.0,
        test_years=2.0,
        max_holding_days=max_holding_days,
    )


def test_max_holding_days_closed_trade() -> None:
    html = sample_deals_report_html(
        deal_row(time="2020.01.01 00:00:00", direction="in", balance="99990"),
        deal_row(
            time="2020.01.11 00:00:00",
            direction="out",
            balance="100500",
            deal_type="sell",
        ),
    )
    assert compute_max_holding_days_from_html(html) == 10.0


def test_max_holding_days_partial_close_uses_fifo_slices() -> None:
    html = sample_deals_report_html(
        deal_row(
            time="2020.01.01 00:00:00",
            direction="in",
            balance="99990",
            volume="0.20",
        ),
        deal_row(
            time="2020.01.06 00:00:00",
            direction="out",
            balance="100200",
            deal_type="sell",
            volume="0.10",
        ),
        deal_row(
            time="2020.02.10 00:00:00",
            direction="out",
            balance="100500",
            deal_type="sell",
            volume="0.10",
        ),
    )
    # First slice 5d, second slice 40d → max 40
    assert compute_max_holding_days_from_html(html) == 40.0


def test_max_holding_days_open_lot_uses_last_deal_time() -> None:
    html = sample_deals_report_html(
        deal_row(time="2019.01.01 00:00:00", direction="in", balance="99990"),
        deal_row(
            time="2020.01.06 00:00:00",
            direction="in",
            balance="99980",
            volume="0.05",
        ),
    )
    # Open since 2019.01.01 until last deal 2020.01.06 = 370 days
    assert compute_max_holding_days_from_html(html) == 370.0


def test_max_holding_days_empty_deals_returns_none() -> None:
    assert compute_max_holding_days_from_html("<html>no deals</html>") is None


def test_max_holding_days_reversal_closes_opposite_side_and_opens_residual() -> None:
    html = sample_deals_report_html(
        deal_row(
            time="2020.01.01 00:00:00",
            direction="in",
            balance="99990",
            deal_type="sell",
        ),
        deal_row(
            time="2020.01.10 00:00:00",
            direction="in/out",
            balance="100100",
            deal_type="buy",
            volume="0.20",
        ),
        deal_row(
            time="2020.01.20 00:00:00",
            direction="out",
            balance="100500",
            deal_type="sell",
        ),
    )
    assert compute_max_holding_days_from_html(html) == 10.0


def test_max_holding_days_out_by_closes_opposite_side() -> None:
    html = sample_deals_report_html(
        deal_row(time="2020.01.01 00:00:00", direction="in", balance="99990"),
        deal_row(
            time="2020.01.11 00:00:00",
            direction="out by",
            balance="100500",
            deal_type="sell",
        ),
    )
    assert compute_max_holding_days_from_html(html) == 10.0


def test_validation_passes_rejects_holding_too_long() -> None:
    thresholds = ValidationThresholds(max_holding_days=DEFAULT_MAX_HOLDING_DAYS)
    assert not _validation_passes(
        risk_scaling_pass=True,
        ohlc_dd_pass=True,
        real_ticks_dd_pass=True,
        val_sharpe=1.5,
        val_equity=_equity(max_holding_days=400.0),
        thresholds=thresholds,
    )


def test_validation_passes_when_holding_within_limit() -> None:
    thresholds = ValidationThresholds(max_holding_days=DEFAULT_MAX_HOLDING_DAYS)
    assert _validation_passes(
        risk_scaling_pass=True,
        ohlc_dd_pass=True,
        real_ticks_dd_pass=True,
        val_sharpe=1.5,
        val_equity=_equity(max_holding_days=100.0),
        thresholds=thresholds,
    )


def test_validation_passes_at_exact_holding_limit() -> None:
    thresholds = ValidationThresholds(max_holding_days=DEFAULT_MAX_HOLDING_DAYS)
    assert _validation_passes(
        risk_scaling_pass=True,
        ohlc_dd_pass=True,
        real_ticks_dd_pass=True,
        val_sharpe=1.5,
        val_equity=_equity(max_holding_days=DEFAULT_MAX_HOLDING_DAYS),
        thresholds=thresholds,
    )


def test_validation_passes_skips_holding_gate_when_none() -> None:
    thresholds = ValidationThresholds(max_holding_days=DEFAULT_MAX_HOLDING_DAYS)
    assert _validation_passes(
        risk_scaling_pass=True,
        ohlc_dd_pass=True,
        real_ticks_dd_pass=True,
        val_sharpe=1.5,
        val_equity=_equity(max_holding_days=None),
        thresholds=thresholds,
    )


def test_default_max_holding_days_is_one_year() -> None:
    assert DEFAULT_MAX_HOLDING_DAYS == 180


def test_holding_period_reject_reason_uses_contract_token() -> None:
    thresholds = ValidationThresholds(max_holding_days=DEFAULT_MAX_HOLDING_DAYS)
    assert (
        _holding_period_reject_reason(
            _equity(max_holding_days=400.0),
            thresholds,
        )
        == "holding_too_long"
    )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 0.0, -1.0])
def test_validation_thresholds_reject_invalid_max_holding_days(value: float) -> None:
    args = argparse.Namespace(
        min_sharpe="1.0",
        min_validation_calmar="1.0",
        max_equity_dd=16.8,
        max_holding_days=value,
    )
    with pytest.raises(ValueError, match="--max-holding-days must be finite and > 0"):
        _validation_thresholds_from_args(args)
