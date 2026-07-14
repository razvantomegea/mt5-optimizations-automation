"""Synthetic MT5-style report metrics from balance/trade series."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from mt5_equity_metrics import compute_equity_quality_from_series
from mt5_opt_report import to_float

SharpeSource = Literal["equity", "balance"]


@dataclass(frozen=True)
class SyntheticReportResult:
    report_metrics: dict[str, Any]
    max_drawdown_pct: float
    metric_flags: dict[str, bool] = field(default_factory=dict)


def parse_iso_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def max_drawdown_pct(balances: list[float]) -> float:
    if not balances:
        return 0.0
    peak = balances[0]
    max_dd = 0.0
    for balance in balances:
        if balance > peak:
            peak = balance
        if peak <= 0:
            continue
        max_dd = max(max_dd, (peak - balance) / peak * 100.0)
    return max_dd


def profit_factor(trade_profits: list[float]) -> float:
    gross_profit = sum(profit for profit in trade_profits if profit > 0)
    gross_loss = abs(sum(profit for profit in trade_profits if profit < 0))
    if gross_loss <= 0:
        return gross_profit if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def sharpe_from_series(values: list[float]) -> float | None:
    """Sample Sharpe (mean / stdev) on simple returns; None if insufficient data."""
    if len(values) < 3:
        return None
    returns = [
        (values[index] / values[index - 1]) - 1.0
        for index in range(1, len(values))
        if values[index - 1] > 0
    ]
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
    if variance <= 0:
        return None
    return mean / math.sqrt(variance)


def _sharpe_from_balances(balances: list[float]) -> float:
    return sharpe_from_series(balances) or 0.0


def build_synthetic_report_metrics(
    *,
    initial_deposit: float,
    equity_curve: list[dict[str, Any]],
    trade_profits: list[float],
    drawdown_pct: float | None = None,
    drawdown_label: str = "Equity Drawdown Relative",
    equity_drawdown_pct: float | None = None,
    equity_metrics_available: bool = True,
    sharpe_source: SharpeSource = "equity",
) -> SyntheticReportResult:
    balances: list[float] = []
    equities: list[float] = []
    for point in equity_curve:
        balance = to_float(point.get("balance"))
        equity = to_float(point.get("equity"))
        if balance is None:
            raise ValueError("Portfolio curve point missing balance")
        balances.append(balance)
        if equity is not None:
            equities.append(equity)

    final_balance = balances[-1] if balances else initial_deposit
    net_profit = final_balance - initial_deposit
    net_profit_pct = (
        (net_profit / initial_deposit * 100.0) if initial_deposit > 0 else 0.0
    )
    max_dd_pct = drawdown_pct if drawdown_pct is not None else max_drawdown_pct(balances)

    equity_dd_available = equity_metrics_available and len(equities) >= 2
    if equity_drawdown_pct is not None:
        resolved_equity_dd_pct: float | None = equity_drawdown_pct
    elif equity_dd_available:
        resolved_equity_dd_pct = max_drawdown_pct(equities)
    else:
        resolved_equity_dd_pct = None

    total_trades = len(trade_profits)
    wins = sum(1 for profit in trade_profits if profit > 0)
    win_rate = (wins / total_trades * 100.0) if total_trades else 0.0

    sharpe_series: list[float] | None = None
    if sharpe_source == "equity" and equity_dd_available:
        sharpe_series = equities
    elif sharpe_source == "balance" or not equity_dd_available:
        sharpe_series = balances
    sharpe_value = sharpe_from_series(sharpe_series) if sharpe_series else None

    times = [
        parse_iso_datetime(str(point["time"]))
        for point in equity_curve
        if point.get("time")
    ]
    equity_quality = None
    if len(times) == len(balances) and len(balances) >= 2:
        dd_for_quality = (
            resolved_equity_dd_pct
            if resolved_equity_dd_pct is not None
            else max_dd_pct
        )
        equity_quality = compute_equity_quality_from_series(
            times,
            balances,
            equity_dd_pct=max(dd_for_quality, 0.01),
        )

    dd_value = f"{max_dd_pct:.2f}% ({initial_deposit * max_dd_pct / 100.0:,.2f})"
    equity_dd_value = (
        f"{resolved_equity_dd_pct:.2f}% ({initial_deposit * resolved_equity_dd_pct / 100.0:,.2f})"
        if resolved_equity_dd_pct is not None
        else "N/A"
    )
    primary_dd_value = (
        equity_dd_value
        if drawdown_label == "Equity Drawdown Relative"
        else dd_value
    )
    metrics: dict[str, str] = {
        "Initial deposit": f"{initial_deposit:,.2f}",
        "Total net profit": f"{net_profit:,.2f} ({net_profit_pct:.2f}%)",
        "Gross profit": f"{sum(profit for profit in trade_profits if profit > 0):,.2f}",
        "Gross loss": f"{sum(profit for profit in trade_profits if profit < 0):,.2f}",
        "Profit factor": f"{profit_factor(trade_profits):.2f}",
        "Expected payoff": (
            f"{(net_profit / total_trades):,.2f}" if total_trades else "0.00"
        ),
        "Recovery factor": (
            f"{(net_profit / (initial_deposit * max_dd_pct / 100.0)):.2f}"
            if max_dd_pct > 0
            else "0.00"
        ),
        "Sharpe Ratio": f"{sharpe_value:.4f}" if sharpe_value is not None else "N/A",
        drawdown_label: primary_dd_value,
        "Total trades": str(total_trades),
        "Profit trades (% of total)": f"{wins} ({win_rate:.2f}%)",
        "Loss trades (% of total)": f"{total_trades - wins} ({100.0 - win_rate:.2f}%)",
    }
    if drawdown_label == "Balance Drawdown Relative":
        metrics["Equity Drawdown Relative"] = equity_dd_value
    elif drawdown_label != "Equity Drawdown Relative":
        metrics["Equity Drawdown Relative"] = equity_dd_value

    if equity_quality is not None:
        metrics["LR Correlation"] = f"{equity_quality.lr_correlation:.6f}"
        metrics["LR Standard Error"] = f"{equity_quality.lr_std_error:.6f}"

    metric_flags = {
        "equity_drawdown_available": resolved_equity_dd_pct is not None,
        "equity_sharpe_available": (
            sharpe_value is not None
            and sharpe_series is equities
            and equity_dd_available
        ),
        "balance_sharpe_available": sharpe_from_series(balances) is not None,
    }

    return SyntheticReportResult(
        report_metrics={"format": "html", "metrics": metrics},
        max_drawdown_pct=max_dd_pct,
        metric_flags=metric_flags,
    )
