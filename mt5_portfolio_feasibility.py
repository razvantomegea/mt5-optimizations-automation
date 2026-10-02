"""Feasibility gate checks for multi-symbol trades and floating equity evidence.

This checks the MT5 execution path, not parity of the production portfolio EA.
No verified portfolio snapshots are written here.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from mt5_env import load_repo_env
from mt5_opt_report import read_report_text, to_float
from mt5_paths import DEFAULT_FAVORITES_DIR
from mt5_portfolio_merge import resolve_strategy_report_path
from mt5_workspace import PACKAGE_ROOT

BALANCE_TOLERANCE = 1.0  # absolute currency units for solo parity


def _metric_value(text: str, *labels: str) -> float | None:
    for label in labels:
        match = re.search(
            re.escape(label) + r":.*?<b>([-\d.\s,]+)",
            text,
            re.S | re.I,
        )
        if not match:
            continue
        parsed = to_float(match.group(1).replace(" ", "").replace(",", ""))
        if parsed is not None:
            return parsed
    return None


def compare_solo_report_parity(
    *,
    report_path: Path,
    expected_final_balance: float | None = None,
) -> list[str]:
    """Return issue strings when a solo report cannot be used as a parity baseline."""
    issues: list[str] = []
    text = read_report_text(report_path)
    initial_deposit = _metric_value(text, "Initial Deposit", "Initial deposit", "Deposit")
    net_profit = _metric_value(text, "Total Net Profit", "Total Net profit", "Net Profit")
    final_balance: float | None = None
    if initial_deposit is not None and net_profit is not None:
        final_balance = initial_deposit + net_profit
    total_trades = _metric_value(text, "Total Trades", "Total trades", "Trades")
    profit_factor = _metric_value(text, "Profit Factor", "Profit factor")
    equity_dd = _metric_value(text, "Equity Drawdown Relative")
    balance_dd = _metric_value(text, "Balance Drawdown Relative")

    if final_balance is None:
        issues.append("missing Final Balance (Initial Deposit + Total Net Profit)")
    if total_trades is None:
        issues.append("missing Total trades")
    if profit_factor is None:
        issues.append("missing Profit Factor")
    if equity_dd is None:
        issues.append("missing Equity Drawdown Relative")
    if balance_dd is None:
        issues.append("missing Balance Drawdown Relative")

    if (
        expected_final_balance is not None
        and final_balance is not None
        and abs(final_balance - expected_final_balance) > BALANCE_TOLERANCE
    ):
        issues.append(
            f"final balance {final_balance:.2f} != expected {expected_final_balance:.2f}"
        )
    return issues


def load_probe_export(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Probe export is not an object: {path}")
    return payload


def evaluate_probe_export(payload: dict[str, Any]) -> list[str]:
    def has_floating_drawdown(sample: Any) -> bool:
        if not isinstance(sample, dict):
            return False
        equity = to_float(sample.get("equity"))
        balance = to_float(sample.get("balance"))
        return equity is not None and balance is not None and equity < balance

    issues: list[str] = []
    if int(payload.get("bar_count_a") or 0) <= 0:
        issues.append("symbol A produced no bars")
    if int(payload.get("bar_count_b") or 0) <= 0:
        issues.append("symbol B produced no bars")
    samples = payload.get("equity_samples")
    if not isinstance(samples, list) or len(samples) < 2:
        issues.append("insufficient equity samples across ticks")
    if int(payload.get("trade_count_a") or 0) <= 0:
        issues.append("symbol A produced no executed trades")
    if int(payload.get("trade_count_b") or 0) <= 0:
        issues.append("symbol B produced no executed trades")
    if int(payload.get("close_count_a") or 0) <= 0:
        issues.append("symbol A produced no closed trades")
    if int(payload.get("close_count_b") or 0) <= 0:
        issues.append("symbol B produced no closed trades")
    if not isinstance(samples, list) or not any(map(has_floating_drawdown, samples)):
        issues.append("no floating equity drawdown was observed")
    if payload.get("samples_truncated") is not False:
        issues.append("equity sample coverage is unverified or truncated")
    return issues


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Favorites portfolio MT5 feasibility gate (multi-symbol probe).",
    )
    parser.add_argument(
        "--solo-report",
        type=Path,
        default=None,
        help="Existing realticks HTML report for solo metric completeness check",
    )
    parser.add_argument(
        "--probe-export",
        type=Path,
        default=None,
        help="JSON written by PortfolioFeasibilityProbe.mq5 (skip live MT5 run)",
    )
    parser.add_argument(
        "--symbol-a",
        default="EURUSD",
    )
    parser.add_argument(
        "--symbol-b",
        default="GBPUSD",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    load_repo_env()
    args = parse_args(argv)
    issues: list[str] = []

    solo_report = args.solo_report
    if solo_report is None:
        solo_report = resolve_strategy_report_path(
            symbol="EURUSD",
            timeframe="M15",
            profile="Classic",
            pass_id=94051,
            report_stem="EURUSD_M15_Classic_pass94051",
            favorites_dir=DEFAULT_FAVORITES_DIR,
        )
        # Fall back to committed EAs/Favorites copy.
        if solo_report is None:
            candidate = (
                PACKAGE_ROOT.parent.parent
                / "EAs"
                / "Favorites"
                / "reports"
                / "EURUSD"
                / "EURUSD_M15_Classic_pass94051_realticks.htm"
            )
            if candidate.is_file():
                solo_report = candidate

    if solo_report is None or not Path(solo_report).is_file():
        issues.append("solo realticks report not found for EURUSD pass94051")
    else:
        issues.extend(compare_solo_report_parity(report_path=Path(solo_report)))

    if args.probe_export is not None:
        if not args.probe_export.is_file():
            issues.append(f"probe export missing: {args.probe_export}")
        else:
            issues.extend(evaluate_probe_export(load_probe_export(args.probe_export)))
    else:
        issues.append(
            "probe export required: compile PortfolioFeasibilityProbe.mq5, "
            "run Model=4 on a hedging account, then pass --probe-export <path>"
        )

    if issues:
        print("FEASIBILITY GATE FAILED:", file=sys.stderr)
        for issue in issues:
            print(f"  - {issue}", file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "ok": True,
                "solo_report": str(solo_report),
                "probe_export": str(args.probe_export),
                "symbol_a": args.symbol_a,
                "symbol_b": args.symbol_b,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
