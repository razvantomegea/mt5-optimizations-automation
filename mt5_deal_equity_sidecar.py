"""Deal-equity sidecar JSON next to MT5 realticks reports."""

from __future__ import annotations

import json
import math
import os
import shutil
from datetime import datetime
from pathlib import Path

from mt5_equity_metrics import _parse_mt5_datetime
from mt5_opt_report import to_float
from mt5_synthetic_report import parse_iso_datetime

DEAL_EQUITY_SIDECAR_SUFFIX = "_realticks_deals.json"
DEAL_EQUITY_EXPORT_BASENAME = "deal_equity_series.json"
REPORT_SUFFIXES = {".htm", ".html", ".xml"}


def resolve_deal_equity_sidecar_path(report_path: Path) -> Path:
    stem = report_path.stem
    if stem.endswith("_realticks"):
        stem = stem[: -len("_realticks")]
    return report_path.parent / f"{stem}{DEAL_EQUITY_SIDECAR_SUFFIX}"


def mt5_common_files_dir() -> Path:
    """MetaQuotes Terminal Common\\Files (FILE_COMMON target)."""
    appdata = Path(os.environ.get("APPDATA", ""))
    return appdata / "MetaQuotes" / "Terminal" / "Common" / "Files"


def resolve_common_deal_equity_export() -> Path:
    return mt5_common_files_dir() / DEAL_EQUITY_EXPORT_BASENAME


def clear_common_deal_equity_export() -> None:
    """Remove a leftover tester export so the next backtest cannot inherit it."""
    path = resolve_common_deal_equity_export()
    if path.is_file():
        path.unlink()


def _name_matches_stem(name: str, stem: str) -> bool:
    return name.startswith(f"{stem}_")


def is_matching_strategy_artifact(name: str, stem: str) -> bool:
    """True for this strategy's HTML/XML reports or deal-equity sidecar."""
    if not _name_matches_stem(name, stem):
        return False
    if Path(name).suffix.lower() in REPORT_SUFFIXES:
        return True
    return name == f"{stem}{DEAL_EQUITY_SIDECAR_SUFFIX}"


def is_matching_realticks_artifact(name: str, stem: str) -> bool:
    """True for this strategy's realticks HTML/XML or deal-equity sidecar."""
    if not _name_matches_stem(name, stem):
        return False
    suffix = Path(name).suffix.lower()
    if suffix in REPORT_SUFFIXES:
        return "_realticks" in name
    return name == f"{stem}{DEAL_EQUITY_SIDECAR_SUFFIX}"


def copy_deal_equity_sidecar_beside_report(
    report_path: Path,
    *,
    source: Path | None = None,
) -> Path | None:
    """Copy a known-fresh deal-equity JSON next to a realticks HTML report.

    Does not fall back to Common Files. Pass source= only when this backtest
    created that export (clear it first, then copy if the file exists).
    """
    dest = resolve_deal_equity_sidecar_path(report_path)
    if source is None:
        return dest if dest.is_file() else None
    if not source.is_file():
        if dest.is_file():
            dest.unlink()
        return None
    if source.resolve() == dest.resolve():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    return dest


def copy_deal_equity_sidecar_between_reports(
    *,
    source_report: Path,
    dest_report: Path,
) -> Path | None:
    """Copy a report-local sidecar from source_report to dest_report."""
    src = resolve_deal_equity_sidecar_path(source_report)
    dest = resolve_deal_equity_sidecar_path(dest_report)
    if not src.is_file():
        return dest if dest.is_file() else None
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return dest


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
        if not isinstance(time_raw, str) or equity is None or not math.isfinite(equity):
            continue
        try:
            points.append((_parse_deal_snapshot_time(time_raw), equity))
        except ValueError:
            continue
    return points
