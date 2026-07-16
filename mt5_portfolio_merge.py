"""Trade-by-trade portfolio merge for favorite optimization strategies."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from itertools import groupby
from pathlib import Path
from typing import Any

from mt5_equity_metrics import (
    attach_equity_to_deal_events,
    parse_deal_events,
    _parse_mt5_datetime,
    reconstruct_deal_equity_series,
)
from mt5_opt_report import read_report_text, to_float
from mt5_paths import DEFAULT_BEST_DIR, DEFAULT_FAVORITES_DIR
from mt5_synthetic_report import build_synthetic_report_metrics, max_drawdown_pct, parse_iso_datetime

REPORT_SUFFIXES = (".htm", ".html")
ALL_FAVORITES_PORTFOLIO_ID = "all-favorites"
DEAL_EQUITY_SIDECAR_SUFFIX = "_realticks_deals.json"


@dataclass(frozen=True)
class StrategyDeal:
    time: datetime
    balance_delta: float
    equity_before: float
    direction: str
    is_closed_trade: bool
    result_id: str
    symbol: str
    timeframe: str
    equity_after: float | None = None


@dataclass(frozen=True)
class StrategyTrade:
    time: datetime
    profit: float
    equity_before: float
    result_id: str
    symbol: str
    timeframe: str


@dataclass(frozen=True)
class StrategySeries:
    result_id: str
    symbol: str
    timeframe: str
    profile: str | None
    pass_id: int | None
    risk_pct: float | None
    initial_deposit: float
    deals: tuple[StrategyDeal, ...]
    closed_trades: tuple[StrategyTrade, ...]
    equity_at_deals: tuple[tuple[datetime, float], ...] = ()
    realticks_equity_dd_pct: float | None = None

    @property
    def trades(self) -> tuple[StrategyTrade, ...]:
        return self.closed_trades


@dataclass(frozen=True)
class IndexedStrategyDeal:
    """Strategy deal with stable ordering key for same-timestamp merge."""

    deal: StrategyDeal
    deal_index: int


@dataclass(frozen=True)
class MergedPortfolioPoint:
    time: datetime
    balance: float
    equity: float | None


@dataclass(frozen=True)
class MergedPortfolio:
    strategy_ids: list[str]
    equity_curve: list[dict[str, Any]]
    total_trades: int
    report_metrics: dict[str, Any]
    summary: dict[str, Any]


def sort_indexed_deals(strategies: list[StrategySeries]) -> list[IndexedStrategyDeal]:
    """Sort all deals by (time, result_id, deal_index) for deterministic merge."""
    indexed = [
        IndexedStrategyDeal(deal=deal, deal_index=deal_index)
        for strategy in strategies
        for deal_index, deal in enumerate(strategy.deals)
    ]
    return sorted(
        indexed,
        key=lambda item: (item.deal.time, item.deal.result_id, item.deal_index),
    )


def group_indexed_deals_by_time(
    indexed_deals: list[IndexedStrategyDeal],
) -> list[tuple[datetime, list[IndexedStrategyDeal]]]:
    """Group sorted deals by timestamp; order within each group is already deterministic."""
    return [
        (time, list(batch))
        for time, batch in groupby(indexed_deals, key=lambda item: item.deal.time)
    ]


def _report_stem_candidates(
    *,
    symbol: str,
    timeframe: str,
    profile: str | None,
    pass_id: int | None,
    report_stem: str | None,
) -> list[str]:
    candidates: list[str] = []
    if report_stem:
        candidates.append(report_stem)
    if profile and pass_id is not None:
        pass_stem = f"{symbol.strip().upper()}_{timeframe}_{profile}_pass{pass_id}"
        if pass_stem not in candidates:
            candidates.append(pass_stem)
    return candidates


def _derive_report_stem(
    *,
    symbol: str,
    timeframe: str,
    profile: str | None,
    pass_id: int | None,
    report_stem: str | None,
) -> str | None:
    candidates = _report_stem_candidates(
        symbol=symbol,
        timeframe=timeframe,
        profile=profile,
        pass_id=pass_id,
        report_stem=report_stem,
    )
    return candidates[0] if candidates else None


def resolve_strategy_report_path(
    *,
    symbol: str,
    timeframe: str,
    profile: str | None,
    pass_id: int | None,
    report_stem: str | None,
    best_dir: Path = DEFAULT_BEST_DIR,
    favorites_dir: Path = DEFAULT_FAVORITES_DIR,
) -> Path | None:
    symbol_dir = symbol.strip().upper()
    for stem in _report_stem_candidates(
        symbol=symbol,
        timeframe=timeframe,
        profile=profile,
        pass_id=pass_id,
        report_stem=report_stem,
    ):
        for bucket in (favorites_dir, best_dir):
            report_root = bucket / "reports" / symbol_dir
            if not report_root.is_dir():
                continue
            for suffix in REPORT_SUFFIXES:
                candidate = report_root / f"{stem}_realticks{suffix}"
                if candidate.is_file():
                    return candidate
    return None


def resolve_deal_equity_sidecar_path(report_path: Path) -> Path:
    stem = report_path.stem
    if stem.endswith("_realticks"):
        stem = stem[: -len("_realticks")]
    return report_path.parent / f"{stem}{DEAL_EQUITY_SIDECAR_SUFFIX}"


def extract_initial_deposit(
    report_path: Path | None,
    summary: dict[str, Any],
    *,
    equity_curve: list[dict[str, Any]] | None = None,
) -> float | None:
    """Parse initial deposit from summary, report HTML, or equity curve; None if not found."""
    deposit = to_float(summary.get("deposit"))
    if deposit is not None and deposit > 0:
        return deposit

    if report_path is not None and report_path.is_file():
        text = read_report_text(report_path)
        for label in ("Initial deposit", "Initial Deposit", "Deposit"):
            pattern = label + r":.*?<b>([-\d.\s,]+)"
            match = re.search(pattern, text, re.S | re.I)
            if match:
                raw = match.group(1).replace(" ", "").replace(",", "")
                parsed = to_float(raw)
                if parsed is not None and parsed > 0:
                    return parsed

    if equity_curve:
        for point in equity_curve:
            balance = to_float(point.get("balance"))
            if balance is not None and balance > 0:
                return balance

    return None


def resolve_initial_deposit(
    report_path: Path | None,
    summary: dict[str, Any],
    *,
    context: str = "",
    equity_curve: list[dict[str, Any]] | None = None,
) -> float:
    """Return initial deposit or raise if it cannot be resolved."""
    deposit = extract_initial_deposit(
        report_path,
        summary,
        equity_curve=equity_curve,
    )
    if deposit is not None and deposit > 0:
        return deposit
    suffix = f" ({context})" if context else ""
    raise ValueError(f"Could not resolve initial deposit{suffix}")


def _risk_pct_from_summary(summary: dict[str, Any], parameters: dict[str, str]) -> float | None:
    for key in ("scaled_risk", "baseline_risk"):
        parsed = to_float(summary.get(key))
        if parsed is not None and parsed > 0:
            return parsed
    parsed = to_float(parameters.get("RISK"))
    if parsed is not None and parsed > 0:
        return parsed
    return None


def _max_strategy_equity_dd_pct(summary: dict[str, Any]) -> float | None:
    parsed = to_float(summary.get("realticks_equity_dd_pct"))
    if parsed is not None and parsed >= 0:
        return parsed
    return None


def _parse_deal_snapshot_time(value: str) -> datetime:
    mt5_time = _parse_mt5_datetime(value.strip())
    if mt5_time is not None:
        return mt5_time
    return parse_iso_datetime(value)


def load_deal_equity_sidecar(report_path: Path) -> list[tuple[datetime, float]]:
    """Load ordered equity snapshots from sidecar JSON (duplicates preserved)."""
    sidecar = resolve_deal_equity_sidecar_path(report_path)
    if not sidecar.is_file():
        return []
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    points: list[tuple[datetime, float]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        time_raw = item.get("time")
        equity = to_float(item.get("equity"))
        if not isinstance(time_raw, str) or equity is None:
            continue
        try:
            points.append((_parse_deal_snapshot_time(time_raw), equity))
        except ValueError:
            continue
    return points


def resolve_deal_equity_series(
    report_path: Path,
    *,
    initial_deposit: float,
) -> list[tuple[datetime, float]]:
    """Ordered equity-after-deal series: sidecar first, else HTML reconstruction."""
    sidecar_series = load_deal_equity_sidecar(report_path)
    if sidecar_series:
        return sidecar_series
    if not report_path.is_file():
        return []
    return reconstruct_deal_equity_series(
        read_report_text(report_path),
        initial_deposit=initial_deposit,
    )


def _closed_trades_from_deal_events(
    events: list[Any],
    *,
    result_id: str,
    symbol: str,
    timeframe: str,
    initial_deposit: float,
) -> list[StrategyTrade]:
    """Reconstruct closed trades from exit deal events in report order."""
    trades: list[StrategyTrade] = []
    last_exit_balance = initial_deposit
    for event in events:
        if not event.is_closed_trade:
            continue
        equity_before = last_exit_balance
        profit = event.balance_after - equity_before
        if equity_before <= 0:
            last_exit_balance = event.balance_after
            continue
        trades.append(
            StrategyTrade(
                time=event.time,
                profit=profit,
                equity_before=equity_before,
                result_id=result_id,
                symbol=symbol,
                timeframe=timeframe,
            )
        )
        last_exit_balance = event.balance_after
    return trades


def deals_from_report(
    report_path: Path,
    *,
    result_id: str,
    symbol: str,
    timeframe: str,
    initial_deposit: float,
) -> tuple[list[StrategyDeal], list[StrategyTrade]]:
    events = parse_deal_events(report_path, initial_deposit=initial_deposit)
    if not events:
        return [], []

    equity_series = resolve_deal_equity_series(
        report_path,
        initial_deposit=initial_deposit,
    )
    events_with_equity = attach_equity_to_deal_events(events, equity_series)
    deals: list[StrategyDeal] = []
    for event, equity_after in events_with_equity:
        deals.append(
            StrategyDeal(
                time=event.time,
                balance_delta=event.balance_delta,
                equity_before=event.equity_before,
                direction=event.direction,
                is_closed_trade=event.is_closed_trade,
                result_id=result_id,
                symbol=symbol,
                timeframe=timeframe,
                equity_after=equity_after,
            )
        )
    closed_trades = _closed_trades_from_deal_events(
        events,
        result_id=result_id,
        symbol=symbol,
        timeframe=timeframe,
        initial_deposit=initial_deposit,
    )
    return deals, closed_trades


def trades_from_report(
    report_path: Path,
    *,
    result_id: str,
    symbol: str,
    timeframe: str,
) -> list[StrategyTrade]:
    initial_deposit = resolve_initial_deposit(
        report_path,
        {},
        context=str(report_path),
    )
    _deals, closed_trades = deals_from_report(
        report_path,
        result_id=result_id,
        symbol=symbol,
        timeframe=timeframe,
        initial_deposit=initial_deposit,
    )
    return closed_trades


def deals_from_equity_curve(
    curve: list[dict[str, Any]],
    *,
    result_id: str,
    symbol: str,
    timeframe: str,
) -> tuple[list[StrategyDeal], list[StrategyTrade]]:
    if len(curve) < 2:
        return [], []

    deals: list[StrategyDeal] = []
    closed_trades: list[StrategyTrade] = []
    for index in range(1, len(curve)):
        previous = curve[index - 1]
        current = curve[index]
        equity_before = to_float(previous.get("balance"))
        balance = to_float(current.get("balance"))
        time_raw = current.get("time")
        equity_after_raw = to_float(current.get("equity"))
        if equity_before is None or balance is None or not isinstance(time_raw, str):
            continue
        if equity_before <= 0:
            continue
        balance_delta = balance - equity_before
        if balance_delta == 0:
            continue
        deal_time = parse_iso_datetime(time_raw)
        deals.append(
            StrategyDeal(
                time=deal_time,
                balance_delta=balance_delta,
                equity_before=equity_before,
                direction="unknown",
                is_closed_trade=True,
                result_id=result_id,
                symbol=symbol,
                timeframe=timeframe,
                equity_after=equity_after_raw,
            )
        )
        closed_trades.append(
            StrategyTrade(
                time=deal_time,
                profit=balance_delta,
                equity_before=equity_before,
                result_id=result_id,
                symbol=symbol,
                timeframe=timeframe,
            )
        )

    return deals, closed_trades


def trades_from_equity_curve(
    curve: list[dict[str, Any]],
    *,
    result_id: str,
    symbol: str,
    timeframe: str,
) -> list[StrategyTrade]:
    _deals, closed_trades = deals_from_equity_curve(
        curve,
        result_id=result_id,
        symbol=symbol,
        timeframe=timeframe,
    )
    return closed_trades


def normalize_favorite_export_row(row: dict[str, Any]) -> dict[str, Any]:
    """Map TradeEcho API camelCase favorite rows to snake_case for merge helpers."""
    data = dict(row)
    aliases = {
        "equityCurve": "equity_curve",
        "reportMetrics": "report_metrics",
        "passId": "pass_id",
        "reportStem": "report_stem",
        "paramFile": "param_file",
    }
    for camel, snake in aliases.items():
        if camel in data and snake not in data:
            data[snake] = data[camel]
    for key in ("summary", "parameters", "equity_curve", "report_metrics"):
        raw = data.get(key)
        if isinstance(raw, str):
            data[key] = json.loads(raw or ("[]" if key == "equity_curve" else "{}"))
    return data


def normalize_favorite_export_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [normalize_favorite_export_row(row) for row in rows]


def load_strategy_series(row: dict[str, Any]) -> StrategySeries:
    result_id = str(row["id"])
    symbol = str(row.get("symbol", ""))
    timeframe = str(row.get("timeframe", ""))
    profile = str(row.get("profile", "") or "") or None
    pass_id_raw = row.get("pass_id")
    pass_id = int(pass_id_raw) if pass_id_raw not in (None, "") else None
    summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
    parameters = row.get("parameters") if isinstance(row.get("parameters"), dict) else {}
    equity_curve = row.get("equity_curve") if isinstance(row.get("equity_curve"), list) else []

    report_path = resolve_strategy_report_path(
        symbol=symbol,
        timeframe=timeframe,
        profile=profile,
        pass_id=pass_id,
        report_stem=str(row.get("report_stem") or "") or None,
    )

    initial_deposit = resolve_initial_deposit(
        report_path,
        summary,
        context=f"{symbol} {timeframe} pass {pass_id} ({result_id})",
        equity_curve=equity_curve,
    )
    deals: list[StrategyDeal] = []
    closed_trades: list[StrategyTrade] = []
    equity_at_deals: list[tuple[datetime, float]] = []

    if report_path is not None:
        deals, closed_trades = deals_from_report(
            report_path,
            result_id=result_id,
            symbol=symbol,
            timeframe=timeframe,
            initial_deposit=initial_deposit,
        )
        if not deals:
            deals, closed_trades = deals_from_equity_curve(
                equity_curve,
                result_id=result_id,
                symbol=symbol,
                timeframe=timeframe,
            )
        else:
            equity_at_deals = resolve_deal_equity_series(
                report_path,
                initial_deposit=initial_deposit,
            )
    else:
        deals, closed_trades = deals_from_equity_curve(
            equity_curve,
            result_id=result_id,
            symbol=symbol,
            timeframe=timeframe,
        )
        for point in equity_curve:
            time_raw = point.get("time")
            equity = to_float(point.get("equity"))
            if isinstance(time_raw, str) and equity is not None:
                try:
                    equity_at_deals.append((parse_iso_datetime(time_raw), equity))
                except ValueError:
                    continue

    if not deals:
        raise ValueError(
            f"No trade series for {symbol} {timeframe} pass {pass_id} ({result_id})"
        )

    return StrategySeries(
        result_id=result_id,
        symbol=symbol,
        timeframe=timeframe,
        profile=profile,
        pass_id=pass_id,
        risk_pct=_risk_pct_from_summary(summary, parameters),
        initial_deposit=initial_deposit,
        deals=tuple(deals),
        closed_trades=tuple(closed_trades),
        equity_at_deals=tuple(equity_at_deals),
        realticks_equity_dd_pct=_max_strategy_equity_dd_pct(summary),
    )


def _scale_cashflow(
    *,
    portfolio_balance: float,
    balance_delta: float,
    strategy_equity_before: float,
) -> float:
    """Scale strategy cashflow to portfolio pool size before the event."""
    if strategy_equity_before <= 0:
        return 0.0
    return balance_delta * (portfolio_balance / strategy_equity_before)


def _scale_equity_point(
    *,
    portfolio_balance: float,
    strategy_equity: float,
    strategy_equity_before: float,
) -> float:
    if strategy_equity_before <= 0:
        return portfolio_balance
    return strategy_equity * (portfolio_balance / strategy_equity_before)


def _strategy_has_equity_data(strategy: StrategySeries) -> bool:
    return bool(strategy.equity_at_deals) or any(
        deal.equity_after is not None for deal in strategy.deals
    )


def _portfolio_has_equity_data(strategies: list[StrategySeries]) -> bool:
    return any(_strategy_has_equity_data(strategy) for strategy in strategies)


def _compute_portfolio_equity_after_batch(
    *,
    portfolio_balance_before: float,
    portfolio_balance_after: float,
    batch: list[IndexedStrategyDeal],
) -> float | None:
    """Mark-to-market portfolio equity after all deal events at one timestamp."""
    deals_with_equity = [
        item
        for item in batch
        if item.deal.equity_after is not None and item.deal.equity_before > 0
    ]
    if not deals_with_equity:
        return portfolio_balance_after

    if len(deals_with_equity) == 1:
        deal = deals_with_equity[0].deal
        return _scale_equity_point(
            portfolio_balance=portfolio_balance_before,
            strategy_equity=deal.equity_after,
            strategy_equity_before=deal.equity_before,
        )

    portfolio_floating = 0.0
    for item in batch:
        deal = item.deal
        if deal.equity_after is None or deal.equity_before <= 0:
            continue
        strategy_balance_after = deal.equity_before + deal.balance_delta
        floating = deal.equity_after - strategy_balance_after
        portfolio_floating += floating * (
            portfolio_balance_before / deal.equity_before
        )
    return portfolio_balance_after + portfolio_floating


def _merged_point_to_dict(point: MergedPortfolioPoint) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "time": point.time.isoformat(),
        "balance": point.balance,
    }
    if point.equity is not None:
        payload["equity"] = point.equity
    return payload


def _scale_closed_trade_profits(
    strategies: list[StrategySeries],
    balance_before_by_time: dict[datetime, float],
) -> list[float]:
    """Scale each closed trade independently using portfolio balance before its timestamp."""
    scaled: list[float] = []
    for strategy in strategies:
        for trade in strategy.closed_trades:
            if trade.equity_before <= 0:
                continue
            portfolio_balance = balance_before_by_time.get(trade.time)
            if portfolio_balance is None:
                continue
            scaled.append(
                trade.profit * (portfolio_balance / trade.equity_before)
            )
    return scaled


def _portfolio_max_equity_drawdown_pct(
    *,
    strategies: list[StrategySeries],
    equity_curve_for_dd: list[float],
) -> float | None:
    """Prefer MT5 report equity DD for a solo portfolio; else use merged equity curve."""
    if len(strategies) == 1:
        solo_dd = strategies[0].realticks_equity_dd_pct
        if solo_dd is not None:
            return solo_dd

    if len(equity_curve_for_dd) < 2:
        return None
    return max_drawdown_pct(equity_curve_for_dd)


def merge_strategy_series(
    strategies: list[StrategySeries],
    *,
    initial_deposit: float | None = None,
) -> MergedPortfolio:
    if not strategies:
        raise ValueError("At least one strategy is required")

    if initial_deposit is not None:
        deposit = initial_deposit
    else:
        deposit = strategies[0].initial_deposit
        if deposit <= 0:
            raise ValueError("Could not resolve initial deposit for portfolio merge")

    indexed_deals = sort_indexed_deals(strategies)
    if not indexed_deals:
        raise ValueError("No deals to merge across strategies")

    equity_metrics_available = _portfolio_has_equity_data(strategies)
    first_time = indexed_deals[0].deal.time
    initial_equity = deposit if equity_metrics_available else None
    points: list[MergedPortfolioPoint] = [
        MergedPortfolioPoint(time=first_time, balance=deposit, equity=initial_equity)
    ]
    balance_curve_for_dd: list[float] = [deposit]
    equity_curve_for_dd: list[float] = [deposit] if equity_metrics_available else []
    balance_before_by_time: dict[datetime, float] = {first_time: deposit}

    for batch_time, batch in group_indexed_deals_by_time(indexed_deals):
        active_batch = [item for item in batch if item.deal.equity_before > 0]
        if not active_batch:
            continue

        balance_before = points[-1].balance
        balance_before_by_time[batch_time] = balance_before
        batch_balance_delta = sum(
            _scale_cashflow(
                portfolio_balance=balance_before,
                balance_delta=item.deal.balance_delta,
                strategy_equity_before=item.deal.equity_before,
            )
            for item in active_batch
        )
        balance_after = balance_before + batch_balance_delta

        equity_after: float | None = None
        if equity_metrics_available:
            equity_after = _compute_portfolio_equity_after_batch(
                portfolio_balance_before=balance_before,
                portfolio_balance_after=balance_after,
                batch=active_batch,
            )

        points.append(
            MergedPortfolioPoint(
                time=batch_time,
                balance=balance_after,
                equity=equity_after,
            )
        )
        balance_curve_for_dd.append(balance_after)
        if equity_after is not None:
            equity_curve_for_dd.append(equity_after)

    equity_curve = [_merged_point_to_dict(point) for point in points]
    max_balance_dd_pct = max_drawdown_pct(balance_curve_for_dd)
    max_equity_dd_pct = _portfolio_max_equity_drawdown_pct(
        strategies=strategies,
        equity_curve_for_dd=equity_curve_for_dd,
    )

    strategy_equity_dds = [
        dd
        for strategy in strategies
        if (dd := strategy.realticks_equity_dd_pct) is not None
    ]
    max_strategy_equity_dd = max(strategy_equity_dds) if strategy_equity_dds else None

    strategy_ids = [strategy.result_id for strategy in strategies]
    total_closed_trades = sum(len(strategy.closed_trades) for strategy in strategies)
    scaled_closed_profits = _scale_closed_trade_profits(strategies, balance_before_by_time)

    synthetic = build_synthetic_report_metrics(
        initial_deposit=deposit,
        equity_curve=equity_curve,
        trade_profits=scaled_closed_profits,
        drawdown_pct=max_balance_dd_pct,
        drawdown_label="Balance Drawdown Relative",
        equity_drawdown_pct=max_equity_dd_pct,
        equity_metrics_available=equity_metrics_available,
        sharpe_source="equity" if equity_metrics_available else "balance",
    )

    last_point = points[-1] if points else None
    summary = {
        "portfolio_id": ALL_FAVORITES_PORTFOLIO_ID,
        "deposit": deposit,
        "strategy_count": len(strategies),
        "total_trades": total_closed_trades,
        "equity_metrics_available": equity_metrics_available,
        "final_balance": last_point.balance if last_point else None,
        "final_equity": last_point.equity if last_point else None,
        "max_equity_drawdown_relative_pct": (
            round(max_equity_dd_pct, 4) if max_equity_dd_pct is not None else None
        ),
        "max_balance_drawdown_relative_pct": round(max_balance_dd_pct, 4),
        "max_strategy_equity_dd_pct": (
            round(max_strategy_equity_dd, 4) if max_strategy_equity_dd is not None else None
        ),
        "strategies": [
            {
                "result_id": strategy.result_id,
                "symbol": strategy.symbol,
                "timeframe": strategy.timeframe,
                "profile": strategy.profile,
                "pass_id": strategy.pass_id,
                "risk_pct": strategy.risk_pct,
                "trade_count": len(strategy.closed_trades),
            }
            for strategy in strategies
        ],
    }

    return MergedPortfolio(
        strategy_ids=strategy_ids,
        equity_curve=equity_curve,
        total_trades=total_closed_trades,
        report_metrics=synthetic.report_metrics,
        summary=summary,
    )
