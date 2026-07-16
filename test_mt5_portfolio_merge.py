"""Unit tests for trade-by-trade portfolio merge."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from mt5_portfolio_merge import (
    ALL_FAVORITES_PORTFOLIO_ID,
    StrategyDeal,
    StrategySeries,
    StrategyTrade,
    _derive_report_stem,
    deals_from_equity_curve,
    deals_from_report,
    extract_initial_deposit,
    load_deal_equity_sidecar,
    load_strategy_series,
    merge_strategy_series,
    resolve_deal_equity_sidecar_path,
    resolve_strategy_report_path,
    trades_from_equity_curve,
    trades_from_report,
)
from mt5_equity_metrics import parse_deal_events
from portfolio_test_helpers import deal, deal_row, sample_deals_report_html, series, trade
from mt5_synthetic_report import (
    build_synthetic_report_metrics,
    max_drawdown_pct,
    profit_factor,
)


def _trade(
    *,
    time: datetime,
    profit: float,
    equity_before: float,
    result_id: str = "strategy-a",
    symbol: str = "EURUSD",
    timeframe: str = "M15",
) -> StrategyTrade:
    return StrategyTrade(
        time=time,
        profit=profit,
        equity_before=equity_before,
        result_id=result_id,
        symbol=symbol,
        timeframe=timeframe,
    )


def _deal(
    *,
    time: datetime,
    balance_delta: float,
    equity_before: float,
    direction: str = "out",
    is_closed_trade: bool = True,
    result_id: str = "strategy-a",
    symbol: str = "EURUSD",
    timeframe: str = "M15",
) -> StrategyDeal:
    return StrategyDeal(
        time=time,
        balance_delta=balance_delta,
        equity_before=equity_before,
        direction=direction,
        is_closed_trade=is_closed_trade,
        result_id=result_id,
        symbol=symbol,
        timeframe=timeframe,
    )


def _series(
    *,
    trades: tuple[StrategyTrade, ...],
    deals: tuple[StrategyDeal, ...] | None = None,
    result_id: str = "strategy-a",
    initial_deposit: float = 100_000.0,
    risk_pct: float | None = 1.0,
) -> StrategySeries:
    resolved_deals = deals if deals is not None else tuple(
        _deal(
            time=trade.time,
            balance_delta=trade.profit,
            equity_before=trade.equity_before,
            result_id=trade.result_id,
            symbol=trade.symbol,
            timeframe=trade.timeframe,
        )
        for trade in trades
    )
    return StrategySeries(
        result_id=result_id,
        symbol="EURUSD",
        timeframe="M15",
        profile="Classic",
        pass_id=1,
        risk_pct=risk_pct,
        initial_deposit=initial_deposit,
        deals=resolved_deals,
        closed_trades=trades,
    )


def test_derive_report_stem_from_identity() -> None:
    assert (
        _derive_report_stem(
            symbol="GBPUSD",
            timeframe="M15",
            profile="Classic",
            pass_id=42,
            report_stem=None,
        )
        == "GBPUSD_M15_Classic_pass42"
    )


def test_derive_report_stem_prefers_explicit_stem() -> None:
    assert (
        _derive_report_stem(
            symbol="GBPUSD",
            timeframe="M15",
            profile="Classic",
            pass_id=42,
            report_stem="custom_stem",
        )
        == "custom_stem"
    )


def test_resolve_strategy_report_path_prefers_favorites_bucket(
    tmp_path: Path,
) -> None:
    favorites_dir = tmp_path / "Favorites"
    best_dir = tmp_path / "Best"
    report_dir = favorites_dir / "reports" / "EURUSD"
    report_dir.mkdir(parents=True)
    report_file = report_dir / "EURUSD_M15_Classic_pass1_realticks.htm"
    report_file.write_text("<html></html>", encoding="utf-8")

    resolved = resolve_strategy_report_path(
        symbol="eurusd",
        timeframe="M15",
        profile="Classic",
        pass_id=1,
        report_stem=None,
        best_dir=best_dir,
        favorites_dir=favorites_dir,
    )

    assert resolved == report_file


def test_trades_from_equity_curve_extracts_profits() -> None:
    trades = trades_from_equity_curve(
        [
            {"time": "2020-01-01T00:00:00", "balance": 100_000},
            {"time": "2020-01-02T00:00:00", "balance": 101_000},
            {"time": "2020-01-03T00:00:00", "balance": 100_500},
        ],
        result_id="row-1",
        symbol="EURUSD",
        timeframe="M15",
    )

    assert len(trades) == 2
    assert trades[0].profit == 1_000
    assert trades[0].equity_before == 100_000
    assert trades[1].profit == -500
    assert trades[1].equity_before == 101_000


def test_trades_from_equity_curve_returns_empty_for_short_curve() -> None:
    assert (
        trades_from_equity_curve(
            [{"time": "2020-01-01T00:00:00", "balance": 100_000}],
            result_id="row-1",
            symbol="EURUSD",
            timeframe="M15",
        )
        == []
    )


def _deal_row(*, time: str, direction: str, balance: str) -> str:
    cells = [
        time,
        "1",
        "EURUSD",
        "buy",
        direction,
        "0.10",
        "1.10000",
        "1",
        "-10.00",
        "0.00",
        "510.00",
        balance,
        "",
    ]
    return "<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"


def _sample_deals_report_html(*rows: str) -> str:
    return f"""
    Initial deposit:</td><td align=right><b>100000</b>
    <b>Deals</b>
    <table>
      {"".join(rows)}
    </table>
    """


def test_resolve_strategy_report_path_falls_back_to_pass_stem(
    tmp_path: Path,
) -> None:
    favorites_dir = tmp_path / "Favorites"
    best_dir = tmp_path / "Best"
    report_dir = best_dir / "reports" / "AUDNZD"
    report_dir.mkdir(parents=True)
    report_file = report_dir / "AUDNZD_M15_Multi_pass1457_realticks.htm"
    report_file.write_text("<html></html>", encoding="utf-8")

    resolved = resolve_strategy_report_path(
        symbol="AUDNZD",
        timeframe="M15",
        profile="Multi",
        pass_id=1457,
        report_stem="124_AUDNZD_M15_Multi_M15_HTFD1",
        best_dir=best_dir,
        favorites_dir=favorites_dir,
    )

    assert resolved == report_file


def test_trades_from_report_counts_only_exit_deals(tmp_path: Path) -> None:
    report_path = tmp_path / "sample.htm"
    report_path.write_text(
        _sample_deals_report_html(
            _deal_row(time="2020.01.01 00:00:00", direction="in", balance="99990"),
            _deal_row(time="2020.01.02 00:00:00", direction="out", balance="100500"),
            _deal_row(time="2020.01.03 00:00:00", direction="in", balance="100490"),
            _deal_row(time="2020.01.04 00:00:00", direction="out", balance="100200"),
            _deal_row(time="2020.01.05 00:00:00", direction="in", balance="100190"),
            _deal_row(
                time="2020.01.06 00:00:00",
                direction="in/out",
                balance="100800",
            ),
        ),
        encoding="utf-8",
    )

    trades = trades_from_report(
        report_path,
        result_id="row-1",
        symbol="EURUSD",
        timeframe="M15",
    )

    assert len(trades) == 3
    assert trades[0].profit == 500
    assert trades[1].profit == -300
    assert trades[2].profit == 600


def test_deals_from_report_includes_entry_and_exit_events(tmp_path: Path) -> None:
    report_path = tmp_path / "sample.htm"
    report_path.write_text(
        _sample_deals_report_html(
            _deal_row(time="2020.01.01 00:00:00", direction="in", balance="99990"),
            _deal_row(time="2020.01.02 00:00:00", direction="out", balance="100500"),
        ),
        encoding="utf-8",
    )

    deals, closed_trades = deals_from_report(
        report_path,
        result_id="row-1",
        symbol="EURUSD",
        timeframe="M15",
        initial_deposit=100_000.0,
    )

    assert len(deals) == 2
    assert deals[0].direction == "in"
    assert deals[0].balance_delta == pytest.approx(-10.0)
    assert deals[1].direction == "out"
    assert deals[1].is_closed_trade is True
    assert len(closed_trades) == 1
    assert closed_trades[0].profit == 500


def test_trades_from_report_parses_exit_deal_rows(tmp_path: Path) -> None:
    report_path = tmp_path / "sample.htm"
    report_path.write_text(
        _sample_deals_report_html(
            _deal_row(time="2020.01.01 00:00:00", direction="in", balance="99990"),
            _deal_row(time="2020.01.02 00:00:00", direction="out", balance="100500"),
        ),
        encoding="utf-8",
    )

    trades = trades_from_report(
        report_path,
        result_id="row-1",
        symbol="EURUSD",
        timeframe="M15",
    )

    assert len(trades) == 1
    assert trades[0].profit == 500
    assert trades[0].equity_before == 100_000


def test_merge_applies_entry_commission_before_overlapping_strategy_trade() -> None:
    entry_time = datetime(2020, 1, 1, 10, 0, 0)
    overlap_time = datetime(2020, 1, 1, 12, 0, 0)
    exit_time = datetime(2020, 1, 1, 15, 0, 0)

    strategy_a = _series(
        result_id="strategy-a",
        trades=(
            _trade(
                time=exit_time,
                profit=500,
                equity_before=100_000,
                result_id="strategy-a",
            ),
        ),
        deals=(
            _deal(
                time=entry_time,
                balance_delta=-10,
                equity_before=100_000,
                direction="in",
                is_closed_trade=False,
                result_id="strategy-a",
            ),
            _deal(
                time=exit_time,
                balance_delta=510,
                equity_before=99_990,
                direction="out",
                is_closed_trade=True,
                result_id="strategy-a",
            ),
        ),
    )
    strategy_a_exit_only = _series(
        result_id="strategy-a",
        trades=(
            _trade(
                time=exit_time,
                profit=500,
                equity_before=100_000,
                result_id="strategy-a",
            ),
        ),
    )
    strategy_b = _series(
        result_id="strategy-b",
        trades=(
            _trade(
                time=overlap_time,
                profit=-500,
                equity_before=100_000,
                result_id="strategy-b",
            ),
        ),
    )

    exit_only = merge_strategy_series(
        [strategy_a_exit_only, strategy_b],
        initial_deposit=100_000,
    )
    all_deals = merge_strategy_series(
        [strategy_a, strategy_b],
        initial_deposit=100_000,
    )

    overlap_points = [
        point for point in all_deals.equity_curve if point["time"] == overlap_time.isoformat()
    ]
    exit_only_points = [
        point for point in exit_only.equity_curve if point["time"] == overlap_time.isoformat()
    ]

    assert overlap_points[-1]["balance"] == pytest.approx(99_490.0)
    assert exit_only_points[-1]["balance"] == pytest.approx(99_500.0)
    assert all_deals.total_trades == 2


def test_load_strategy_series_uses_equity_curve_when_report_missing() -> None:
    series = load_strategy_series(
        {
            "id": "favorite-1",
            "symbol": "EURUSD",
            "timeframe": "M15",
            "profile": "Classic",
            "pass_id": 9,
            "summary": {"deposit": 50_000, "scaled_risk": 1.5},
            "parameters": {"RISK": "1.0"},
            "equity_curve": [
                {"time": "2020-01-01T00:00:00", "balance": 50_000},
                {"time": "2020-01-02T00:00:00", "balance": 51_000},
            ],
        }
    )

    assert series.result_id == "favorite-1"
    assert series.initial_deposit == 50_000
    assert series.risk_pct == 1.5
    assert len(series.trades) == 1
    assert len(series.deals) == 1
    assert series.trades[0].profit == 1_000


def test_load_strategy_series_raises_when_no_trades() -> None:
    with pytest.raises(ValueError, match="No trade series"):
        load_strategy_series(
            {
                "id": "favorite-1",
                "symbol": "EURUSD",
                "timeframe": "M15",
                "profile": "Classic",
                "pass_id": 9,
                "summary": {"deposit": 50_000},
                "equity_curve": [{"time": "2020-01-01T00:00:00", "balance": 50_000}],
            }
        )


def test_merge_single_strategy_preserves_profit_when_deposit_matches() -> None:
    merged = merge_strategy_series(
        [
            _series(
                trades=(
                    _trade(
                        time=datetime(2020, 1, 2),
                        profit=1_000,
                        equity_before=100_000,
                    ),
                ),
            )
        ],
        initial_deposit=100_000,
    )

    assert merged.total_trades == 1
    assert merged.equity_curve[-1]["balance"] == 101_000
    assert merged.report_metrics["metrics"]["Total trades"] == "1"
    assert merged.summary["portfolio_id"] == ALL_FAVORITES_PORTFOLIO_ID


def test_merge_orders_trades_chronologically() -> None:
    merged = merge_strategy_series(
        [
            _series(
                result_id="late",
                trades=(
                    _trade(
                        time=datetime(2020, 1, 3),
                        profit=1_000,
                        equity_before=100_000,
                        result_id="late",
                    ),
                ),
            ),
            _series(
                result_id="early",
                trades=(
                    _trade(
                        time=datetime(2020, 1, 1),
                        profit=500,
                        equity_before=100_000,
                        result_id="early",
                    ),
                ),
            ),
        ],
        initial_deposit=100_000,
    )

    assert merged.strategy_ids == ["late", "early"]
    assert merged.equity_curve[1]["balance"] == 100_500
    assert merged.equity_curve[2]["balance"] == pytest.approx(101_505.0)


def test_merge_scales_later_trade_to_current_portfolio_equity() -> None:
    merged = merge_strategy_series(
        [
            _series(
                result_id="first",
                trades=(
                    _trade(
                        time=datetime(2020, 1, 1),
                        profit=500,
                        equity_before=100_000,
                        result_id="first",
                    ),
                ),
            ),
            _series(
                result_id="second",
                trades=(
                    _trade(
                        time=datetime(2020, 1, 2),
                        profit=1_000,
                        equity_before=100_000,
                        result_id="second",
                    ),
                ),
            ),
        ],
        initial_deposit=100_000,
    )

    assert merged.equity_curve[1]["balance"] == 100_500
    assert merged.equity_curve[2]["balance"] == pytest.approx(101_505.0)


def test_higher_risk_backtest_increases_profit_and_drawdown() -> None:
    """Doubled trade P&L simulates 2x RISK: same timing, larger wins and losses."""
    win_time = datetime(2020, 4, 1)
    loss_time = datetime(2020, 4, 2)
    deposit = 100_000

    low_risk = merge_strategy_series(
        [
            _series(
                trades=(
                    _trade(
                        time=win_time,
                        profit=5_000,
                        equity_before=deposit,
                    ),
                    _trade(
                        time=loss_time,
                        profit=-2_000,
                        equity_before=105_000,
                    ),
                ),
                risk_pct=1.0,
            )
        ],
        initial_deposit=deposit,
    )
    high_risk = merge_strategy_series(
        [
            _series(
                trades=(
                    _trade(
                        time=win_time,
                        profit=10_000,
                        equity_before=deposit,
                    ),
                    _trade(
                        time=loss_time,
                        profit=-4_000,
                        equity_before=110_000,
                    ),
                ),
                risk_pct=2.0,
            )
        ],
        initial_deposit=deposit,
    )

    low_net_profit = low_risk.equity_curve[-1]["balance"] - deposit
    high_net_profit = high_risk.equity_curve[-1]["balance"] - deposit

    assert low_net_profit == pytest.approx(3_000)
    assert high_net_profit == pytest.approx(6_000)
    assert high_net_profit > low_net_profit
    assert high_risk.summary["max_balance_drawdown_relative_pct"] > low_risk.summary[
        "max_balance_drawdown_relative_pct"
    ]


def _max_drawdown_from_merged(merged) -> float:
    balances = [point["balance"] for point in merged.equity_curve]
    return max_drawdown_pct(balances)


def test_merge_correlated_losses_combine_drawdown_to_thirty_percent() -> None:
    crash_time = datetime(2020, 3, 15)
    loss_a = _trade(
        time=crash_time,
        profit=-15_000,
        equity_before=100_000,
        result_id="strategy-a",
    )
    loss_b = _trade(
        time=crash_time,
        profit=-15_000,
        equity_before=100_000,
        result_id="strategy-b",
    )

    solo_a = merge_strategy_series(
        [_series(trades=(loss_a,), result_id="strategy-a")],
        initial_deposit=100_000,
    )
    solo_b = merge_strategy_series(
        [_series(trades=(loss_b,), result_id="strategy-b")],
        initial_deposit=100_000,
    )
    merged = merge_strategy_series(
        [
            _series(trades=(loss_a,), result_id="strategy-a"),
            _series(trades=(loss_b,), result_id="strategy-b"),
        ],
        initial_deposit=100_000,
    )

    solo_a_dd = _max_drawdown_from_merged(solo_a)
    solo_b_dd = _max_drawdown_from_merged(solo_b)
    merged_dd = _max_drawdown_from_merged(merged)

    assert solo_a_dd == pytest.approx(15.0, abs=0.01)
    assert solo_b_dd == pytest.approx(15.0, abs=0.01)
    assert merged_dd == pytest.approx(30.0, abs=0.01)
    assert merged.equity_curve[-1]["balance"] == 70_000
    assert merged.summary["max_equity_drawdown_relative_pct"] is None
    assert merged.summary["max_balance_drawdown_relative_pct"] == pytest.approx(30.0)
    assert merged.report_metrics["metrics"]["Total trades"] == "2"


def test_merge_raises_when_no_strategies() -> None:
    with pytest.raises(ValueError, match="At least one strategy"):
        merge_strategy_series([])


def test_solo_merge_final_balance_matches_report_net_profit(tmp_path: Path) -> None:
    report_path = tmp_path / "sample.htm"
    report_path.write_text(
        _sample_deals_report_html(
            _deal_row(time="2020.01.01 00:00:00", direction="in", balance="99990"),
            _deal_row(time="2020.01.02 00:00:00", direction="out", balance="100500"),
        ),
        encoding="utf-8",
    )

    deals, closed_trades = deals_from_report(
        report_path,
        result_id="favorite-1",
        symbol="EURUSD",
        timeframe="M15",
        initial_deposit=100_000.0,
    )
    series = StrategySeries(
        result_id="favorite-1",
        symbol="EURUSD",
        timeframe="M15",
        profile="Classic",
        pass_id=1,
        risk_pct=None,
        initial_deposit=100_000.0,
        deals=tuple(deals),
        closed_trades=tuple(closed_trades),
    )
    merged = merge_strategy_series([series], initial_deposit=100_000.0)

    assert merged.equity_curve[-1]["balance"] == pytest.approx(100_500.0)
    assert merged.total_trades == 1


def test_build_synthetic_report_metrics_includes_net_profit_and_drawdown() -> None:
    result = build_synthetic_report_metrics(
        initial_deposit=100_000,
        equity_curve=[
            {"time": "2020-01-01T00:00:00", "balance": 100_000, "equity": 100_000},
            {"time": "2020-01-02T00:00:00", "balance": 110_000, "equity": 110_000},
            {"time": "2020-01-03T00:00:00", "balance": 99_000, "equity": 99_000},
        ],
        trade_profits=[10_000, -11_000],
        equity_metrics_available=True,
    )

    assert result.report_metrics["format"] == "html"
    assert result.report_metrics["metrics"]["Total trades"] == "2"
    assert result.report_metrics["metrics"]["Total net profit"].startswith("-1,000.00")
    assert result.report_metrics["metrics"]["Equity Drawdown Relative"].startswith(
        "10.00%"
    )
    assert result.max_drawdown_pct == pytest.approx(10.0)


def test_max_drawdown_pct_tracks_peak_to_trough() -> None:
    assert max_drawdown_pct([100_000, 110_000, 99_000]) == pytest.approx(10.0)


def test_profit_factor_handles_mixed_trades() -> None:
    assert profit_factor([1_000, -400, 200]) == pytest.approx(3.0)


def test_resolve_deal_equity_sidecar_path_strips_realticks_suffix(tmp_path: Path) -> None:
    report_path = tmp_path / "EURUSD_M15_Classic_pass1_realticks.htm"
    report_path.write_text("<html></html>", encoding="utf-8")

    assert resolve_deal_equity_sidecar_path(report_path) == (
        tmp_path / "EURUSD_M15_Classic_pass1_realticks_deals.json"
    )


def test_load_deal_equity_sidecar_parses_mt5_and_iso_times(tmp_path: Path) -> None:
    report_path = tmp_path / "EURUSD_M15_Classic_pass1_realticks.htm"
    report_path.write_text("<html></html>", encoding="utf-8")
    sidecar_path = resolve_deal_equity_sidecar_path(report_path)
    sidecar_path.write_text(
        json.dumps(
            [
                {"time": "2020.01.02 12:00:00", "equity": 101_500.0},
                {"time": "2020-01-03T00:00:00", "equity": 99_000.0},
            ]
        ),
        encoding="utf-8",
    )

    points = load_deal_equity_sidecar(report_path)

    assert len(points) == 2
    assert points[0][1] == pytest.approx(101_500.0)
    assert points[1][1] == pytest.approx(99_000.0)


def test_load_deal_equity_sidecar_returns_empty_for_invalid_json(tmp_path: Path) -> None:
    report_path = tmp_path / "sample_realticks.htm"
    report_path.write_text("<html></html>", encoding="utf-8")
    sidecar_path = resolve_deal_equity_sidecar_path(report_path)
    sidecar_path.write_text("{not json", encoding="utf-8")

    assert load_deal_equity_sidecar(report_path) == []


def test_deals_from_equity_curve_includes_equity_after_when_present() -> None:
    deals, closed_trades = deals_from_equity_curve(
        [
            {"time": "2020-01-01T00:00:00", "balance": 100_000, "equity": 100_000},
            {"time": "2020-01-02T00:00:00", "balance": 99_000, "equity": 98_500},
        ],
        result_id="row-1",
        symbol="EURUSD",
        timeframe="M15",
    )

    assert len(deals) == 1
    assert deals[0].equity_after == pytest.approx(98_500)
    assert len(closed_trades) == 1
    assert closed_trades[0].profit == pytest.approx(-1_000)


def test_extract_initial_deposit_prefers_summary_then_html(tmp_path: Path) -> None:
    report_path = tmp_path / "sample.htm"
    report_path.write_text(
        "Initial deposit:</td><td align=right><b>75,000.00</b>",
        encoding="utf-8",
    )

    assert extract_initial_deposit(report_path, {"deposit": 50_000}) == pytest.approx(50_000)
    assert extract_initial_deposit(report_path, {}) == pytest.approx(75_000)
    assert extract_initial_deposit(None, {}) is None


def test_extract_initial_deposit_uses_equity_curve_when_report_missing() -> None:
    curve = [
        {"time": "2016-12-21T04:00:00", "balance": 99_998.42, "equity": 99_998.42},
        {"time": "2016-12-22T04:00:00", "balance": 100_500.0, "equity": 100_500.0},
    ]

    assert extract_initial_deposit(None, {}, equity_curve=curve) == pytest.approx(99_998.42)


def test_load_strategy_series_uses_equity_curve_deposit_when_report_missing() -> None:
    series = load_strategy_series(
        {
            "id": "favorite-3434",
            "symbol": "AUDUSD",
            "timeframe": "H4",
            "profile": "Multi",
            "pass_id": 3434,
            "summary": {},
            "parameters": {"RISK": "1.0"},
            "equity_curve": [
                {"time": "2016-12-21T04:00:00", "balance": 99_998.42, "equity": 99_998.42},
                {"time": "2016-12-22T04:00:00", "balance": 100_500.0, "equity": 100_500.0},
            ],
        }
    )

    assert series.initial_deposit == pytest.approx(99_998.42)
    assert len(series.trades) == 1


def test_load_strategy_series_uses_report_when_available(tmp_path: Path) -> None:
    report_path = tmp_path / "EURUSD_M15_Classic_pass9_realticks.htm"
    report_path.write_text(
        sample_deals_report_html(
            deal_row(time="2020.01.01 00:00:00", direction="in", balance="99990"),
            deal_row(time="2020.01.02 00:00:00", direction="out", balance="101000"),
        ),
        encoding="utf-8",
    )

    with patch(
        "mt5_portfolio_merge.resolve_strategy_report_path",
        return_value=report_path,
    ):
        loaded = load_strategy_series(
            {
                "id": "favorite-1",
                "symbol": "EURUSD",
                "timeframe": "M15",
                "profile": "Classic",
                "pass_id": 9,
                "summary": {"deposit": 100_000, "realticks_equity_dd_pct": 12.5},
                "parameters": {"RISK": "1.0"},
                "equity_curve": [
                    {"time": "2020-01-01T00:00:00", "balance": 100_000},
                    {"time": "2020-01-02T00:00:00", "balance": 50_000},
                ],
            }
        )

    assert len(loaded.deals) == 2
    assert len(loaded.closed_trades) == 1
    assert loaded.realticks_equity_dd_pct == pytest.approx(12.5)


def test_load_strategy_series_falls_back_when_report_has_no_deals(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "EURUSD_M15_Classic_pass9_realticks.htm"
    report_path.write_text("<html>No deals table</html>", encoding="utf-8")

    with patch(
        "mt5_portfolio_merge.resolve_strategy_report_path",
        return_value=report_path,
    ):
        loaded = load_strategy_series(
            {
                "id": "favorite-1",
                "symbol": "EURUSD",
                "timeframe": "M15",
                "profile": "Classic",
                "pass_id": 9,
                "summary": {"deposit": 50_000},
                "parameters": {},
                "equity_curve": [
                    {"time": "2020-01-01T00:00:00", "balance": 50_000},
                    {"time": "2020-01-02T00:00:00", "balance": 51_000},
                ],
            }
        )

    assert len(loaded.closed_trades) == 1
    assert loaded.closed_trades[0].profit == pytest.approx(1_000)


def test_merge_raises_when_no_deals() -> None:
    empty = series(trades=(), deals=())
    with pytest.raises(ValueError, match="No deals to merge"):
        merge_strategy_series([empty])


def test_merge_with_equity_sidecar_tracks_equity_drawdown_separately() -> None:
    entry_time = datetime(2020, 1, 1)
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
                        time=entry_time,
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
                equity_at_deals=((entry_time, 99_000), (exit_time, 89_000)),
            )
        ],
        initial_deposit=100_000,
    )

    assert merged.summary["max_balance_drawdown_relative_pct"] == pytest.approx(10.0, abs=0.1)
    assert merged.summary["max_equity_drawdown_relative_pct"] > merged.summary[
        "max_balance_drawdown_relative_pct"
    ]
    assert merged.equity_curve[-1]["equity"] < merged.equity_curve[-1]["balance"]


def test_merge_summary_aggregates_strategy_trade_counts_and_equity_dd() -> None:
    merged = merge_strategy_series(
        [
            series(
                result_id="strategy-a",
                trades=(
                    trade(
                        time=datetime(2020, 1, 1),
                        profit=500,
                        equity_before=100_000,
                        result_id="strategy-a",
                    ),
                ),
                realticks_equity_dd_pct=11.0,
            ),
            series(
                result_id="strategy-b",
                trades=(
                    trade(
                        time=datetime(2020, 1, 2),
                        profit=500,
                        equity_before=100_000,
                        result_id="strategy-b",
                    ),
                ),
                realticks_equity_dd_pct=15.5,
            ),
        ],
        initial_deposit=100_000,
    )

    assert merged.total_trades == 2
    assert merged.summary["max_strategy_equity_dd_pct"] == pytest.approx(15.5)
    assert {item["result_id"]: item["trade_count"] for item in merged.summary["strategies"]} == {
        "strategy-a": 1,
        "strategy-b": 1,
    }


def test_deals_from_report_attaches_equity_after_from_sidecar(tmp_path: Path) -> None:
    report_path = tmp_path / "EURUSD_M15_Classic_pass1_realticks.htm"
    report_path.write_text(
        sample_deals_report_html(
            deal_row(time="2020.01.01 00:00:00", direction="in", balance="99990"),
            deal_row(time="2020.01.02 00:00:00", direction="out", balance="100500"),
        ),
        encoding="utf-8",
    )
    sidecar_path = resolve_deal_equity_sidecar_path(report_path)
    sidecar_path.write_text(
        json.dumps([{"time": "2020.01.02 00:00:00", "equity": 100_450.0}]),
        encoding="utf-8",
    )

    deals, _closed = deals_from_report(
        report_path,
        result_id="row-1",
        symbol="EURUSD",
        timeframe="M15",
        initial_deposit=100_000.0,
    )

    exit_deal = next(item for item in deals if item.direction == "out")
    assert exit_deal.equity_after == pytest.approx(100_450.0)


def test_merge_duplicate_exit_deals_same_timestamp_both_trades_counted() -> None:
    same_time = datetime(2020, 4, 1, 12, 0, 0)
    merged = merge_strategy_series(
        [
            _series(
                trades=(
                    _trade(time=same_time, profit=500, equity_before=100_000),
                    _trade(time=same_time, profit=300, equity_before=100_500),
                ),
                deals=(
                    _deal(
                        time=same_time,
                        balance_delta=500,
                        equity_before=100_000,
                        is_closed_trade=True,
                    ),
                    _deal(
                        time=same_time,
                        balance_delta=300,
                        equity_before=100_500,
                        is_closed_trade=True,
                    ),
                ),
            )
        ],
        initial_deposit=100_000,
    )

    assert merged.total_trades == 2
    assert merged.equity_curve[-1]["balance"] == pytest.approx(100_798.507, rel=1e-4)
    assert merged.report_metrics["metrics"]["Total trades"] == "2"


def test_parse_deal_events_counts_exit_rows_only(tmp_path: Path) -> None:
    report_path = tmp_path / "sample.htm"
    report_path.write_text(
        sample_deals_report_html(
            deal_row(time="2020.01.01 00:00:00", direction="in", balance="99990"),
            deal_row(time="2020.01.02 00:00:00", direction="out", balance="100500"),
            deal_row(time="2020.01.03 00:00:00", direction="in/out", balance="100800"),
        ),
        encoding="utf-8",
    )

    events = parse_deal_events(report_path, initial_deposit=100_000.0)

    assert len(events) == 3
    assert sum(1 for event in events if event.is_closed_trade) == 2
