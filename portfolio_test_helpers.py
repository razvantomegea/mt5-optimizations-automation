"""Shared fixtures for portfolio merge/generation tests."""

from __future__ import annotations

from datetime import datetime

from mt5_portfolio_merge import StrategyDeal, StrategySeries, StrategyTrade


def trade(
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


def deal(
    *,
    time: datetime,
    balance_delta: float,
    equity_before: float,
    direction: str = "out",
    is_closed_trade: bool = True,
    result_id: str = "strategy-a",
    symbol: str = "EURUSD",
    timeframe: str = "M15",
    equity_after: float | None = None,
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
        equity_after=equity_after,
    )


def series(
    *,
    trades: tuple[StrategyTrade, ...],
    deals: tuple[StrategyDeal, ...] | None = None,
    result_id: str = "strategy-a",
    initial_deposit: float = 100_000.0,
    risk_pct: float | None = 1.0,
    realticks_equity_dd_pct: float | None = None,
    equity_at_deals: tuple[tuple[datetime, float], ...] = (),
) -> StrategySeries:
    resolved_deals = deals if deals is not None else tuple(
        deal(
            time=closed.time,
            balance_delta=closed.profit,
            equity_before=closed.equity_before,
            result_id=closed.result_id,
            symbol=closed.symbol,
            timeframe=closed.timeframe,
        )
        for closed in trades
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
        equity_at_deals=equity_at_deals,
        realticks_equity_dd_pct=realticks_equity_dd_pct,
    )


def deal_row(*, time: str, direction: str, balance: str) -> str:
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


def sample_deals_report_html(*rows: str) -> str:
    return f"""
    Initial deposit:</td><td align=right><b>100000</b>
    <b>Deals</b>
    <table>
      {"".join(rows)}
    </table>
    """
