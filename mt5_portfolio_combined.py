"""Compile, run, and verify one shared-account TrendReversal portfolio backtest."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from mt5_db_report import extract_full_report_metrics
from mt5_deal_equity_sidecar import mt5_common_files_dir
from mt5_opt_report import to_float
from mt5_paths import DEFAULT_FAVORITES_DIR, resolve_terminal
from mt5_portfolio_ea_generator import canonical_ea_revision, generate_portfolio_ea
from mt5_portfolio_manifest import PortfolioManifest
from mt5_set_files import resolve_mt5_data_dir
from mt5_workspace import PACKAGE_ROOT


@dataclass(frozen=True)
class CombinedPortfolioResult:
    payload: dict[str, Any]
    summary: dict[str, Any]
    report_path: Path


def _report_number(metrics: dict[str, str], label: str) -> float:
    value = metrics.get(label)
    if value is None:
        raise ValueError(f"Combined tester report missing {label}")
    match = re.match(r"\s*([-+]?\d[\d ,]*(?:\.\d+)?)", value)
    number = to_float(match.group(1).replace(" ", "").replace(",", "")) if match else None
    if number is None or not math.isfinite(number):
        raise ValueError(f"Combined tester report has invalid {label}: {value!r}")
    return number


def parse_combined_portfolio_result(
    *,
    manifest: PortfolioManifest,
    report_path: Path,
    export_path: Path,
) -> CombinedPortfolioResult:
    """Fail closed unless the real-tick report and same-run export agree."""
    report = extract_full_report_metrics(report_path)
    if report.get("format") != "html" or not isinstance(report.get("metrics"), dict):
        raise ValueError("Combined tester did not produce an HTML backtest report")
    metrics: dict[str, str] = report["metrics"]
    initial = _report_number(metrics, "Initial Deposit")
    profit = _report_number(metrics, "Total Net Profit")
    trades = _report_number(metrics, "Total Trades")
    balance_dd = _report_number(metrics, "Balance Drawdown Relative")
    equity_dd = _report_number(metrics, "Equity Drawdown Relative")
    _report_number(metrics, "Profit Factor")
    _report_number(metrics, "Sharpe Ratio")
    if abs(initial - manifest.deposit) > 0.01:
        raise ValueError("Combined tester deposit differs from portfolio manifest")

    export = json.loads(export_path.read_text(encoding="utf-8-sig"))
    if not isinstance(export, dict) or export.get("source") != "mt5_combined_tester":
        raise ValueError("Combined tester equity export has invalid provenance")
    # missed_bars can trip on multi-symbol chart-driven ticks when a non-chart
    # symbol advances more than one bar between chart ticks. Certification relies
    # on HTML report ↔ export agreement instead.
    if export.get("window_close_failed") is not False:
        raise ValueError("Combined tester could not close a strategy at its source-window end")
    for label, actual, tolerance in (
        ("initial_deposit", initial, 0.01),
        ("net_profit", profit, 0.01),
        ("trades", trades, 0.0),
        ("final_balance", initial + profit, 0.01),
        ("balance_dd_pct", balance_dd, 0.011),
        ("equity_dd_pct", equity_dd, 0.011),
    ):
        exported = to_float(export.get(label))
        if exported is None or not math.isfinite(exported) or abs(exported - actual) > tolerance:
            raise ValueError(f"Combined tester {label} export differs from HTML report")
    counts = export.get("strategy_trade_counts")
    if (
        not isinstance(counts, list)
        or len(counts) != len(manifest.strategies)
        or any(not isinstance(value, int) or value < 0 for value in counts)
        or sum(counts) != int(trades)
    ):
        raise ValueError("Combined tester per-strategy trades do not match total trades")
    raw_curve = export.get("equity_curve")
    if not isinstance(raw_curve, list) or len(raw_curve) < 2:
        raise ValueError("Combined tester equity curve is missing")
    curve: list[dict[str, Any]] = []
    previous_time: datetime | None = None
    for point in raw_curve:
        if not isinstance(point, dict):
            raise ValueError("Invalid combined tester equity curve point")
        try:
            timestamp = datetime.strptime(point["time"], "%Y.%m.%d %H:%M:%S")
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Invalid combined tester equity curve timestamp") from exc
        balance = to_float(point.get("balance"))
        equity = to_float(point.get("equity"))
        if (
            (previous_time is not None and timestamp < previous_time)
            or balance is None or equity is None
            or not math.isfinite(balance) or not math.isfinite(equity)
        ):
            raise ValueError("Combined tester equity curve is incomplete or out of order")
        previous_time = timestamp
        curve.append({"time": timestamp.isoformat(), "balance": balance, "equity": equity})
    if abs(curve[-1]["balance"] - (initial + profit)) > 0.01:
        raise ValueError("Combined tester curve does not end at report final balance")
    final_equity = to_float(export.get("final_equity"))
    if final_equity is None or not math.isfinite(final_equity):
        raise ValueError("Combined tester final equity is invalid")
    if abs(curve[-1]["equity"] - final_equity) > 0.01:
        raise ValueError("Combined tester curve does not end at final equity")

    strategy_summaries = [
        {
            "result_id": strategy.result_id,
            "symbol": strategy.symbol,
            "timeframe": strategy.timeframe,
            "profile": strategy.profile,
            "pass_id": strategy.pass_id,
            "risk_pct": strategy.risk_pct,
            "trade_count": counts[index],
        }
        for index, strategy in enumerate(manifest.strategies)
    ]
    summary = {
        "portfolio_id": manifest.portfolio_id,
        "company": manifest.company,
        "deposit": initial,
        "strategy_count": len(manifest.strategies),
        "total_trades": int(trades),
        "final_balance": initial + profit,
        "final_equity": final_equity,
        "max_balance_drawdown_relative_pct": to_float(export["balance_dd_pct"]),
        "max_equity_drawdown_relative_pct": to_float(export["equity_dd_pct"]),
        "strategies": strategy_summaries,
        "source": "mt5_combined_tester",
        "validation_state": "verified",
        "canonical_ea_revision": canonical_ea_revision(),
        "manifest_version": manifest.version,
        "tester_model": manifest.tester_model,
        "manifest": manifest.to_dict(),
        "missed_bars": export.get("missed_bars") is True,
        "window_close_failed": export.get("window_close_failed") is True,
    }
    payload = {
        "strategyIds": [strategy.result_id for strategy in manifest.strategies],
        "strategyCount": len(manifest.strategies),
        "manifest": manifest.to_dict(),
        "summary": summary,
        "reportMetrics": report,
        "equityCurve": curve,
    }
    return CombinedPortfolioResult(
        payload=payload,
        summary={
            "portfolio_id": manifest.portfolio_id,
            "company": manifest.company,
            "strategy_count": len(manifest.strategies),
            "total_trades": int(trades),
            "final_balance": initial + profit,
            "final_equity": summary["final_equity"],
            "max_balance_drawdown_relative_pct": summary["max_balance_drawdown_relative_pct"],
            "max_equity_drawdown_relative_pct": summary["max_equity_drawdown_relative_pct"],
        },
        report_path=report_path,
    )


def run_combined_portfolio(manifest: PortfolioManifest) -> CombinedPortfolioResult:
    """Run all manifest strategies on one MT5 tester account, then verify output."""
    # Import lazily: the batch module reads the optimization environment at import.
    from mt5_batch_optimize import run_single_backtest

    terminal = resolve_terminal()
    assert terminal is not None
    portable = os.environ.get("MT5_PORTABLE", "").strip().lower() in {"1", "true", "yes"}
    data_dir = resolve_mt5_data_dir(
        terminal=terminal,
        portable=portable,
        mt5_data=os.environ.get("MT5_DATA") or None,
    )
    manifest_bytes = json.dumps(manifest.to_dict(), sort_keys=True).encode("utf-8")
    revision = canonical_ea_revision().encode("utf-8")
    fingerprint = hashlib.sha256(manifest_bytes + b"\0" + revision).hexdigest()[:16]
    build_dir = PACKAGE_ROOT / "reports" / "PortfolioBuilds" / fingerprint
    build_dir.mkdir(parents=True, exist_ok=True)
    name = f"PositionRelayPortfolio_{fingerprint}"
    export_file = f"{name}_equity.json"
    source_path = build_dir / f"{name}.mq5"
    source_path.write_text(
        generate_portfolio_ea(
            manifest,
            sets_dir=DEFAULT_FAVORITES_DIR / "sets",
            export_file=export_file,
        ),
        encoding="utf-8",
    )
    compiled_path = source_path.with_suffix(".ex5")
    log_path = build_dir / "compile.log"
    compiled_path.unlink(missing_ok=True)
    log_path.unlink(missing_ok=True)
    editor = terminal.with_name("MetaEditor64.exe")
    if not editor.is_file():
        raise FileNotFoundError(f"MetaEditor not found beside terminal: {editor}")
    started_at = time.time()
    subprocess.run(
        [str(editor), f"/compile:{source_path}", f"/log:{log_path}"],
        cwd=str(terminal.parent),
        timeout=180,
        check=False,
    )
    if not log_path.is_file():
        raise RuntimeError("MetaEditor did not produce a combined portfolio compile log")
    log_bytes = log_path.read_bytes()
    log = log_bytes.decode(
        "utf-16" if log_bytes.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig",
        errors="replace",
    )
    if (
        "Result: 0 errors" not in log
        or not compiled_path.is_file()
        or compiled_path.stat().st_mtime + 2.0 < started_at
    ):
        raise RuntimeError(f"Combined portfolio EA compilation failed: {log_path}")
    expert_name = f"{name}.ex5"
    shutil.copy2(compiled_path, data_dir / "MQL5" / "Experts" / expert_name)
    tester_set = data_dir / "MQL5" / "Profiles" / "Tester" / f"{name}.set"
    tester_set.write_text("\n", encoding="utf-16")
    export_path = mt5_common_files_dir() / export_file
    export_path.unlink(missing_ok=True)
    # The chart symbol drives OnTick for every instance. Start with the
    # earliest active strategy so the first source window receives ticks.
    chart_strategy = min(
        manifest.strategies,
        key=lambda strategy: strategy.source_from_date or manifest.from_date,
    )
    report_path = run_single_backtest(
        terminal=terminal,
        install_dir=terminal.parent,
        data_dir=data_dir,
        work_dir=build_dir,
        expert=expert_name,
        set_file_name=tester_set.name,
        symbol=chart_strategy.symbol,
        timeframe=chart_strategy.timeframe,
        from_date=manifest.from_date,
        to_date=manifest.to_date,
        model=4,
        execution_mode=0,
        deposit=str(manifest.deposit),
        currency=manifest.currency,
        leverage=manifest.leverage,
        portable=portable,
        timeout_seconds=60 * 60 * 12,
        max_tester_memory_mb=int(os.environ.get("MT5_PORTFOLIO_MEMORY_LIMIT_MB", "32768")),
    )
    if not export_path.is_file() or export_path.stat().st_mtime + 2.0 < started_at:
        raise ValueError("Combined tester did not write a fresh equity export")
    shutil.copy2(export_path, build_dir / export_file)
    return parse_combined_portfolio_result(
        manifest=manifest,
        report_path=report_path,
        export_path=export_path,
    )
