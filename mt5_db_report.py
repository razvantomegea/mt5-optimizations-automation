"""Push MT5 optimization progress and results via TradeEcho API."""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from mt5_equity_metrics import attach_equity_to_deal_events, parse_deal_events
from mt5_opt_report import read_report_text, worksheet_rows
from mt5_portfolio_merge import (
    resolve_deal_equity_series,
    resolve_initial_deposit,
)
from mt5_trade_echo_api import TradeEchoOptimizerApi, resolve_optimization_run_id


def _json_default(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value)!r} is not JSON serializable")


def _stringify_params(params: dict[str, Any] | None) -> dict[str, str] | None:
    if not params:
        return None
    return {str(key): str(val) for key, val in params.items()}


def extract_full_report_metrics(report_path: Path) -> dict[str, Any]:
    """Parse every labelled stat from an MT5 backtest report (HTML or XML)."""
    if report_path.suffix.lower() == ".xml":
        title, headers, records = worksheet_rows(report_path)
        return {
            "format": "xml",
            "title": title,
            "headers": headers,
            "records": records[:1] if records else [],
        }

    text = read_report_text(report_path)
    metrics: dict[str, str] = {}
    for match in re.finditer(
        r">([^<:]+):</td>\s*<td[^>]*>.*?<b>([^<]+)</b>",
        text,
        re.S | re.I,
    ):
        label = re.sub(r"\s+", " ", match.group(1)).strip()
        metrics[label] = match.group(2).replace("\xa0", " ").strip()
    return {"format": "html", "metrics": metrics}


def extract_equity_curve(report_path: Path) -> list[dict[str, Any]]:
    if report_path.suffix.lower() == ".xml":
        return []

    initial_deposit = resolve_initial_deposit(report_path, {}, context=str(report_path))
    events = parse_deal_events(report_path, initial_deposit=initial_deposit)
    if events:
        equity_series = resolve_deal_equity_series(
            report_path,
            initial_deposit=initial_deposit,
        )
        events_with_equity = attach_equity_to_deal_events(events, equity_series)
        return [
            {
                "time": event.time.isoformat(),
                "balance": event.balance_after,
                **({"equity": equity} if equity is not None else {}),
            }
            for event, equity in events_with_equity
        ]

    from mt5_equity_metrics import parse_balance_rows

    times, balances = parse_balance_rows(report_path)
    return [
        {"time": ts.isoformat(), "balance": balance}
        for ts, balance in zip(times, balances, strict=False)
    ]


class NoopReporter:
    """Null object: every reporter call is a silent no-op."""

    def __getattr__(self, _name: str) -> Any:
        return lambda *args, **kwargs: None


class OptimizationApiReporter:
    """Writes run progress and per-candidate results through TradeEcho API."""

    def __init__(self, *, run_id: str, api: TradeEchoOptimizerApi) -> None:
        self.run_id = run_id
        self._api = api
        self._job_index = 0
        self._report_stem = ""

    def _safe(self, event_type: str, payload: dict[str, Any]) -> None:
        try:
            self._api.post_run_event(self.run_id, event_type, payload)
        except SystemExit:
            raise
        except Exception as exc:  # noqa: BLE001 - reporting must not crash run
            print(f"  WARNING: API {event_type} failed: {exc}", file=sys.stderr)

    def close(self) -> None:
        return None

    def run_started(
        self,
        *,
        total_jobs: int,
        from_date: str,
        to_date: str,
        symbols: list[str],
        timeframes: list[str],
        resume: bool,
    ) -> None:
        self._safe(
            "run_started",
            {
                "totalJobs": total_jobs,
                "fromDate": from_date,
                "toDate": to_date,
                "symbols": symbols,
                "timeframes": timeframes,
                "resume": resume,
            },
        )

    def job_started(
        self,
        *,
        job_index: int,
        total_jobs: int,
        symbol: str,
        timeframe: str,
        param_file: str,
        report_stem: str,
        phase: str = "optimizing",
    ) -> None:
        self._job_index = job_index
        self._report_stem = report_stem
        self._safe(
            "job_started",
            {
                "jobIndex": job_index,
                "totalJobs": total_jobs,
                "symbol": symbol,
                "timeframe": timeframe,
                "paramFile": param_file,
                "reportStem": report_stem,
                "phase": phase,
            },
        )

    def job_completed(
        self,
        *,
        job_index: int,
        total_jobs: int,
        symbol: str,
        timeframe: str,
        param_file: str,
        report_stem: str,
        status: str,
        error: str = "",
    ) -> None:
        self._job_index = job_index
        self._report_stem = report_stem
        self._safe(
            "job_completed",
            {
                "jobIndex": job_index,
                "totalJobs": total_jobs,
                "symbol": symbol,
                "timeframe": timeframe,
                "paramFile": param_file,
                "reportStem": report_stem,
                "status": status,
                "error": error,
            },
        )

    def validation_result(
        self,
        *,
        row: dict[str, Any],
        parameters: dict[str, Any] | None,
        real_report_path: Path | None,
    ) -> None:
        passed = bool(row.get("validation_pass"))
        reject_reason = str(row.get("reject_reason", "") or "")
        report_metrics: dict[str, Any] | None = None
        equity_curve: list[dict[str, Any]] | None = None
        should_persist_detail = (
            passed or reject_reason
        ) and real_report_path is not None and real_report_path.is_file()
        if should_persist_detail:
            try:
                report_metrics = extract_full_report_metrics(real_report_path)
                equity_curve = extract_equity_curve(real_report_path)
            except Exception as exc:
                print(f"  WARNING: report parse failed: {exc}", file=sys.stderr)

        self._safe(
            "validation_result",
            {
                "jobIndex": self._job_index,
                "reportStem": self._report_stem,
                "row": row,
                "parameters": _stringify_params(parameters),
                "reportMetrics": report_metrics,
                "equityCurve": equity_curve,
            },
        )

    def run_completed(self, *, status: str = "completed", error: str = "") -> None:
        self._safe(
            "run_completed",
            {"status": status, "error": error},
        )

    def run_failed(self, error: str) -> None:
        self._safe("run_failed", {"error": error})


def create_reporter() -> OptimizationApiReporter | NoopReporter:
    run_id = resolve_optimization_run_id()
    user_id = os.environ.get("TRADEECHO_USER_ID", "").strip()
    if not run_id or not user_id:
        return NoopReporter()

    try:
        api = TradeEchoOptimizerApi.from_env()
        api.mark_worker_running(run_id)
        return OptimizationApiReporter(run_id=run_id, api=api)
    except SystemExit:
        return NoopReporter()
    except Exception as exc:  # noqa: BLE001
        print(f"  WARNING: optimizer API reporter disabled: {exc}", file=sys.stderr)
        return NoopReporter()
