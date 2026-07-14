"""Validate merged portfolio metrics against MT5 report ground truth."""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

from mt5_env import load_repo_env
from mt5_opt_report import read_report_text, to_float
from mt5_portfolio_merge import (
    load_strategy_series,
    merge_strategy_series,
    resolve_strategy_report_path,
)
from mt5_trade_echo_api import TradeEchoOptimizerApi
from mt5_trade_echo_auth import assert_optimizer_access


def _metric_value(report_metrics: dict[str, Any] | None, *labels: str) -> float | None:
    if not isinstance(report_metrics, dict):
        return None
    metrics = report_metrics.get("metrics")
    if not isinstance(metrics, dict):
        return None
    for label in labels:
        raw = metrics.get(label)
        if not isinstance(raw, str):
            continue
        match = re.search(r"[-\d.,]+", raw.replace(" ", ""))
        if not match:
            continue
        parsed = to_float(match.group(0).replace(",", ""))
        if parsed is not None:
            return parsed
    return None


def _report_balance_dd(report_path) -> float | None:
    text = read_report_text(report_path)
    match = re.search(
        r"Balance Drawdown Relative:.*?<b>([-\d.\s]+)\s*%",
        text,
        re.S | re.I,
    )
    if not match:
        return None
    return to_float(match.group(1).replace(" ", ""))


def _normalize_favorite_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
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
        normalized.append(data)
    return normalized


def validate_favorites(api: TradeEchoOptimizerApi) -> int:
    issues: list[str] = []
    warnings: list[str] = []

    rows = _normalize_favorite_rows(api.get_favorites())
    strategies = [load_strategy_series(row) for row in rows]
    merged = merge_strategy_series(strategies)

    for row, series in zip(rows, strategies, strict=True):
        label = f"{series.symbol} {series.timeframe} pass {series.pass_id}"
        report_path = resolve_strategy_report_path(
            symbol=series.symbol,
            timeframe=series.timeframe,
            profile=series.profile,
            pass_id=series.pass_id,
            report_stem=str(row.get("report_stem") or "") or None,
        )
        if report_path is None:
            warnings.append(f"{label}: no HTML report (equity-curve fallback)")
            continue

        report_trades = _metric_value(row.get("report_metrics"), "Total trades", "Trades")
        if report_trades is not None and len(series.closed_trades) != int(report_trades):
            issues.append(
                f"{label}: trade_count {len(series.closed_trades)} != report {int(report_trades)}"
            )

        solo = merge_strategy_series([series])
        report_net = _metric_value(
            row.get("report_metrics"),
            "Total net profit",
            "Total Net Profit",
            "Net Profit",
        )
        solo_net = solo.equity_curve[-1]["balance"] - series.initial_deposit
        if report_net is not None and abs(solo_net - report_net) > max(1.0, abs(report_net) * 0.001):
            issues.append(
                f"{label}: solo net {solo_net:.2f} != report net {report_net:.2f}"
            )

        report_dd = _report_balance_dd(report_path)
        solo_dd = solo.summary.get("max_balance_drawdown_relative_pct")
        if (
            report_dd is not None
            and isinstance(solo_dd, (int, float))
            and abs(float(solo_dd) - report_dd) > 0.25
        ):
            issues.append(
                f"{label}: solo balance DD {solo_dd}% != report {report_dd}%"
            )

        entry_times = [deal.time for deal in series.deals if deal.direction == "in"]
        exit_times = [deal.time for deal in series.deals if deal.is_closed_trade]
        if entry_times and exit_times and min(exit_times) < min(entry_times):
            issues.append(f"{label}: first exit occurs before first entry")

    last_point = merged.equity_curve[-1] if merged.equity_curve else None

    print(json.dumps({
        "strategy_count": len(strategies),
        "total_trades": merged.total_trades,
        "final_balance": last_point["balance"] if last_point else None,
        "final_equity": last_point.get("equity") if last_point else None,
        "max_balance_drawdown_relative_pct": merged.summary.get("max_balance_drawdown_relative_pct"),
        "max_equity_drawdown_relative_pct": merged.summary.get("max_equity_drawdown_relative_pct"),
        "max_strategy_equity_dd_pct": merged.summary.get("max_strategy_equity_dd_pct"),
        "warnings": warnings,
        "issues": issues,
    }, indent=2))

    return 1 if issues else 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    return argparse.ArgumentParser(
        description="Validate portfolio metrics against MT5 reports.",
    ).parse_args(argv)


def main(argv: list[str]) -> int:
    load_repo_env()
    assert_optimizer_access()
    parse_args(argv)
    try:
        api = TradeEchoOptimizerApi.from_env()
        return validate_favorites(api)
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
