"""Portfolio accounting tests: balance, equity, and trade stats are independent."""

from __future__ import annotations

from datetime import datetime

import pytest

from mt5_portfolio_merge import merge_strategy_series
from mt5_synthetic_report import (
    build_synthetic_report_metrics,
    max_drawdown_pct,
    sharpe_from_series,
)
from portfolio_test_helpers import deal, series, trade


def test_non_overlapping_trades_two_strategies() -> None:
    merged = merge_strategy_series(
        [
            series(
                result_id="a",
                trades=(
                    trade(
                        time=datetime(2020, 1, 1),
                        profit=1_000,
                        equity_before=100_000,
                        result_id="a",
                    ),
                ),
            ),
            series(
                result_id="b",
                trades=(
                    trade(
                        time=datetime(2020, 1, 3),
                        profit=500,
                        equity_before=100_000,
                        result_id="b",
                    ),
                ),
            ),
        ],
        initial_deposit=100_000,
    )

    assert merged.total_trades == 2
    assert merged.equity_curve[-1]["balance"] == pytest.approx(101_505.0)
    assert merged.report_metrics["metrics"]["Profit factor"] == "1505.00"


def test_overlapping_open_periods_equity_differs_from_balance() -> None:
    entry = datetime(2020, 1, 1)
    exit_time = datetime(2020, 1, 5)
    merged = merge_strategy_series(
        [
            series(
                trades=(
                    trade(
                        time=exit_time,
                        profit=-5_000,
                        equity_before=100_000,
                    ),
                ),
                deals=(
                    deal(
                        time=entry,
                        balance_delta=-10,
                        equity_before=100_000,
                        direction="in",
                        is_closed_trade=False,
                        equity_after=99_000,
                    ),
                    deal(
                        time=exit_time,
                        balance_delta=-5_000,
                        equity_before=99_990,
                        direction="out",
                        is_closed_trade=True,
                        equity_after=94_000,
                    ),
                ),
                equity_at_deals=((entry, 99_000), (exit_time, 94_000)),
            )
        ],
        initial_deposit=100_000,
    )

    entry_points = [p for p in merged.equity_curve if p["time"] == entry.isoformat()]
    assert entry_points
    assert entry_points[-1]["balance"] == pytest.approx(99_990.0)
    assert entry_points[-1]["equity"] < entry_points[-1]["balance"]


def test_multiple_events_same_timestamp_one_strategy() -> None:
    same_time = datetime(2020, 3, 15, 12, 0, 0)
    merged = merge_strategy_series(
        [
            series(
                trades=(
                    trade(
                        time=same_time,
                        profit=300,
                        equity_before=100_000,
                    ),
                    trade(
                        time=same_time,
                        profit=-100,
                        equity_before=100_300,
                    ),
                ),
                deals=(
                    deal(
                        time=same_time,
                        balance_delta=300,
                        equity_before=100_000,
                        direction="out",
                        is_closed_trade=True,
                    ),
                    deal(
                        time=same_time,
                        balance_delta=-100,
                        equity_before=100_300,
                        direction="out",
                        is_closed_trade=True,
                    ),
                ),
            )
        ],
        initial_deposit=100_000,
    )

    assert merged.total_trades == 2
    assert merged.equity_curve[-1]["balance"] == pytest.approx(100_200.299, rel=1e-4)


def test_same_timestamp_across_strategies_is_deterministic() -> None:
    crash_time = datetime(2020, 3, 15)
    loss_a = trade(
        time=crash_time,
        profit=-10_000,
        equity_before=100_000,
        result_id="a",
    )
    loss_b = trade(
        time=crash_time,
        profit=-10_000,
        equity_before=100_000,
        result_id="b",
    )
    strategies_ab = [
        series(trades=(loss_a,), result_id="a"),
        series(trades=(loss_b,), result_id="b"),
    ]
    strategies_ba = [
        series(trades=(loss_b,), result_id="b"),
        series(trades=(loss_a,), result_id="a"),
    ]

    merged_ab = merge_strategy_series(strategies_ab, initial_deposit=100_000)
    merged_ba = merge_strategy_series(strategies_ba, initial_deposit=100_000)

    assert merged_ab.equity_curve[-1]["balance"] == merged_ba.equity_curve[-1]["balance"]
    assert merged_ab.summary["max_balance_drawdown_relative_pct"] == pytest.approx(
        merged_ba.summary["max_balance_drawdown_relative_pct"]
    )


def test_missing_equity_snapshots_balance_only_metrics() -> None:
    merged = merge_strategy_series(
        [
            series(
                trades=(
                    trade(
                        time=datetime(2020, 1, 2),
                        profit=-5_000,
                        equity_before=100_000,
                    ),
                ),
            )
        ],
        initial_deposit=100_000,
    )

    assert merged.summary["equity_metrics_available"] is False
    assert merged.summary["max_equity_drawdown_relative_pct"] is None
    assert merged.summary["max_balance_drawdown_relative_pct"] == pytest.approx(5.0, abs=0.01)
    assert merged.report_metrics["metrics"]["Equity Drawdown Relative"] == "N/A"


def test_duplicate_closed_trades_same_timestamp() -> None:
    same_time = datetime(2020, 6, 1)
    merged = merge_strategy_series(
        [
            series(
                trades=(
                    trade(time=same_time, profit=400, equity_before=100_000),
                    trade(time=same_time, profit=600, equity_before=100_400),
                ),
                deals=(
                    deal(
                        time=same_time,
                        balance_delta=400,
                        equity_before=100_000,
                        is_closed_trade=True,
                    ),
                    deal(
                        time=same_time,
                        balance_delta=600,
                        equity_before=100_400,
                        is_closed_trade=True,
                    ),
                ),
            )
        ],
        initial_deposit=100_000,
    )

    assert merged.total_trades == 2
    assert merged.equity_curve[-1]["balance"] == pytest.approx(100_997.610, rel=1e-4)
    assert merged.report_metrics["metrics"]["Total trades"] == "2"


def test_equity_drawdown_differs_from_balance_drawdown() -> None:
    entry = datetime(2020, 1, 1)
    exit_time = datetime(2020, 1, 2)
    merged = merge_strategy_series(
        [
            series(
                trades=(
                    trade(
                        time=exit_time,
                        profit=-10_000,
                        equity_before=100_000,
                    ),
                ),
                deals=(
                    deal(
                        time=entry,
                        balance_delta=-10,
                        equity_before=100_000,
                        direction="in",
                        is_closed_trade=False,
                        equity_after=99_000,
                    ),
                    deal(
                        time=exit_time,
                        balance_delta=-10_000,
                        equity_before=99_990,
                        direction="out",
                        is_closed_trade=True,
                        equity_after=89_000,
                    ),
                ),
                equity_at_deals=((entry, 99_000), (exit_time, 89_000)),
            )
        ],
        initial_deposit=100_000,
    )

    balance_dd = merged.summary["max_balance_drawdown_relative_pct"]
    equity_dd = merged.summary["max_equity_drawdown_relative_pct"]
    assert balance_dd is not None
    assert equity_dd is not None
    assert equity_dd > balance_dd


def test_sharpe_equity_vs_balance_returns() -> None:
    curve = [
        {"time": "2020-01-01T00:00:00", "balance": 100_000, "equity": 100_000},
        {"time": "2020-01-02T00:00:00", "balance": 101_000, "equity": 99_000},
        {"time": "2020-01-03T00:00:00", "balance": 102_000, "equity": 98_000},
        {"time": "2020-01-04T00:00:00", "balance": 103_000, "equity": 97_000},
    ]
    balances = [point["balance"] for point in curve]
    equities = [point["equity"] for point in curve]

    equity_sharpe = sharpe_from_series(equities)
    balance_sharpe = sharpe_from_series(balances)
    assert equity_sharpe is not None
    assert balance_sharpe is not None
    assert equity_sharpe != balance_sharpe

    equity_result = build_synthetic_report_metrics(
        initial_deposit=100_000,
        equity_curve=curve,
        trade_profits=[1_000, 1_000, 1_000],
        equity_metrics_available=True,
        sharpe_source="equity",
    )
    balance_result = build_synthetic_report_metrics(
        initial_deposit=100_000,
        equity_curve=curve,
        trade_profits=[1_000, 1_000, 1_000],
        equity_metrics_available=True,
        sharpe_source="balance",
    )
    assert (
        equity_result.report_metrics["metrics"]["Sharpe Ratio"]
        != balance_result.report_metrics["metrics"]["Sharpe Ratio"]
    )


def test_max_drawdown_from_balance_and_equity_curves() -> None:
    balances = [100_000, 110_000, 95_000]
    equities = [100_000, 105_000, 90_000]
    assert max_drawdown_pct(balances) == pytest.approx(
        (110_000 - 95_000) / 110_000 * 100
    )
    assert max_drawdown_pct(equities) == pytest.approx(
        (105_000 - 90_000) / 105_000 * 100
    )
