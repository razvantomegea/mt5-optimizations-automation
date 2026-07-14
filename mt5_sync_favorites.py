#!/usr/bin/env python3
"""Copy dashboard favorites from reports/Best to reports/Favorites."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from mt5_optimization_set_paths import (
    describe_favorite_identity,
    resolve_favorite_source_set_file,
)
from mt5_paths import DEFAULT_BEST_DIR, DEFAULT_FAVORITES_DIR
from mt5_workspace import PACKAGE_ROOT
from mt5_trade_echo_api import TradeEchoOptimizerApi

REPORT_SUFFIXES = {".htm", ".html", ".xml"}


def _is_matching_report_file(report_name: str, stem: str) -> bool:
    suffix = Path(report_name).suffix.lower()
    return suffix in REPORT_SUFFIXES and stem in report_name and "_realticks" in report_name


def _copy_set_file(set_file: Path, favorites_dir: Path) -> Path:
    dest_set_dir = favorites_dir / "sets"
    dest_set_dir.mkdir(parents=True, exist_ok=True)
    dest_set = dest_set_dir / set_file.name
    shutil.copy2(set_file, dest_set)
    return dest_set


def _copy_matching_reports(
    *,
    best_dir: Path,
    favorites_dir: Path,
    symbol: str,
    stem: str,
) -> list[Path]:
    src_report_dir = best_dir / "reports" / symbol
    if not src_report_dir.is_dir():
        return []

    dest_report_dir = favorites_dir / "reports" / symbol
    dest_report_dir.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []

    for report in sorted(src_report_dir.iterdir()):
        if not report.is_file():
            continue
        if not _is_matching_report_file(report.name, stem):
            continue
        dest_report = dest_report_dir / report.name
        shutil.copy2(report, dest_report)
        copied.append(dest_report)

    return copied


def copy_strategy_to_favorites(
    *,
    set_file: Path,
    symbol: str,
    best_dir: Path,
    favorites_dir: Path,
) -> list[Path]:
    if not set_file.is_file():
        raise FileNotFoundError(f"Set file not found: {set_file}")

    stem = set_file.stem
    copied = [_copy_set_file(set_file, favorites_dir)]
    copied.extend(
        _copy_matching_reports(
            best_dir=best_dir,
            favorites_dir=favorites_dir,
            symbol=symbol,
            stem=stem,
        )
    )
    return copied


def _build_favorite_identity(favorite: dict) -> dict:
    return {
        "symbol": str(favorite.get("symbol", "")).strip().upper(),
        "timeframe": str(favorite.get("timeframe", "")),
        "profile": favorite.get("profile"),
        "passId": favorite.get("passId", favorite.get("pass_id")),
    }


def sync_favorite(
    *,
    favorite: dict,
    best_dir: Path,
    favorites_dir: Path,
    repo_root: Path,
) -> tuple[list[Path] | None, str | None]:
    identity = _build_favorite_identity(favorite)
    set_file = resolve_favorite_source_set_file(
        best_dir=best_dir,
        favorites_dir=favorites_dir,
        repo_root=repo_root,
        param_file=favorite.get("paramFile", favorite.get("param_file")),
        summary=favorite.get("summary") if isinstance(favorite.get("summary"), dict) else None,
        identity=identity,
    )
    if not set_file:
        return None, describe_favorite_identity(identity)

    copied = copy_strategy_to_favorites(
        set_file=set_file,
        symbol=identity["symbol"],
        best_dir=best_dir,
        favorites_dir=favorites_dir,
    )
    return copied, None


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync dashboard favorites to Favorites/")
    parser.add_argument("--best-dir", default=str(DEFAULT_BEST_DIR))
    parser.add_argument("--favorites-dir", default=str(DEFAULT_FAVORITES_DIR))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    best_dir = Path(args.best_dir).resolve()
    favorites_dir = Path(args.favorites_dir).resolve()

    if not best_dir.is_dir():
        print(f"Best directory not found: {best_dir}", file=sys.stderr)
        return 1

    api = TradeEchoOptimizerApi.from_env()
    favorites = api.get_favorites()
    if not favorites:
        print("No favorites found")
        return 0

    copied_count = 0
    skipped: list[str] = []
    for favorite in favorites:
        copied, skip_label = sync_favorite(
            favorite=favorite,
            best_dir=best_dir,
            favorites_dir=favorites_dir,
            repo_root=PACKAGE_ROOT,
        )
        if skip_label:
            skipped.append(skip_label)
            continue
        copied_count += 1
        for file_path in copied or []:
            print(f"Copied to {file_path}")

    if skipped:
        print(
            f"Skipped {len(skipped)} favorite(s) with no .set file in "
            f"{best_dir} or {favorites_dir}:"
        )
        for label in skipped:
            print(f"  - {label}")

    print(f"Synced {copied_count}/{len(favorites)} favorite(s) to {favorites_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
