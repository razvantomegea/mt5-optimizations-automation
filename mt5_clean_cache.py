#!/usr/bin/env python3
"""Clear MT5 tester cache and local batch optimizer artifacts."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from mt5_workspace import PACKAGE_ROOT

DEFAULT_TERMINAL = r"C:\Program Files\MetaTrader 5\terminal64.exe"
ARTIFACT_PATHS = (
    "generated_configs",
    "reports",
    "validate_staging",
    "mt5_batch_runs.csv",
)


def _read_arg(name: str, args: argparse.Namespace) -> str | None:
    value = getattr(args, name.replace("-", "_"), None)
    return str(value) if value else None


def resolve_mt5_data_dir(*, terminal: str, mt5_data: str | None) -> Path:
    explicit = (mt5_data or os.environ.get("MT5_DATA_DIR", "")).strip()
    if explicit:
        return Path(explicit).resolve()

    install_dir = Path(terminal).parent

    appdata = Path(os.environ.get("APPDATA", "")) / "MetaQuotes" / "Terminal"
    if not appdata.is_dir():
        raise RuntimeError(
            "MT5 data directory not found. Pass --mt5-data or set MT5_DATA_DIR."
        )

    install_norm = str(install_dir.resolve()).lower()
    for entry in appdata.iterdir():
        origin = entry / "origin.txt"
        if not origin.is_file():
            continue
        text = origin.read_text(encoding="utf-16").replace("\ufeff", "").replace("\0", "").strip()
        if text.lower() == install_norm:
            return entry.resolve()

    raise RuntimeError(
        "MT5 data directory not found. Pass --mt5-data or set MT5_DATA_DIR."
    )


def assert_work_dir(work_dir: Path) -> Path:
    resolved = work_dir.resolve()
    cwd = Path.cwd().resolve()

    if resolved == Path(resolved.anchor) or re.fullmatch(r"[A-Za-z]:\\?", str(resolved)):
        raise RuntimeError(f"Refusing to clean unsafe work directory: {resolved}")

    home = Path(os.environ.get("USERPROFILE", os.environ.get("HOME", ""))).resolve()
    if home.exists() and resolved == home:
        raise RuntimeError(f"Refusing to clean home directory: {resolved}")

    try:
        resolved.relative_to(cwd)
    except ValueError as error:
        raise RuntimeError(
            f"Work directory must be under cwd ({cwd}): {resolved}"
        ) from error

    return resolved


def assert_cache_dir(cache_dir: Path, data_dir: Path) -> bool:
    expected = (data_dir / "Tester" / "cache").resolve()
    if cache_dir.resolve() != expected:
        raise RuntimeError(f"Refusing to clean non-cache path: {cache_dir}")
    if not cache_dir.is_dir():
        print(f"Cache folder does not exist: {cache_dir}")
        return False
    return True


def dir_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(dir_size(child) for child in path.iterdir())


def format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def stop_mt5() -> None:
    print("Stopping MT5 and batch scripts...")
    subprocess.run(
        [sys.executable, str(PACKAGE_ROOT / "mt5_stop.py")],
        check=False,
    )


def run_cache_cleanup(*, cache_dir: Path, data_dir: Path, dry_run: bool) -> None:
    if not assert_cache_dir(cache_dir, data_dir):
        return

    entries = list(cache_dir.iterdir())
    if not entries:
        print(f"Cache already empty: {cache_dir}")
        return

    total_bytes = sum(dir_size(entry) for entry in entries)
    print(
        f"{'Would remove' if dry_run else 'Removing'} {len(entries)} item(s) from {cache_dir}"
    )
    print(f"Estimated size: {format_bytes(total_bytes)}")

    for entry in entries:
        print(f"  {'[dry-run]' if dry_run else 'delete'} {entry}")
        if not dry_run:
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink(missing_ok=True)

    print("Cache dry run complete." if dry_run else "Cache cleanup complete.")


def run_artifacts_cleanup(work_dir: Path, *, dry_run: bool) -> None:
    resolved_work_dir = assert_work_dir(work_dir)
    print(
        f"{'Would clean' if dry_run else 'Cleaning'} artifacts in {resolved_work_dir}"
    )

    for rel in ARTIFACT_PATHS:
        target = resolved_work_dir / rel
        if not target.exists():
            print(f"  skip (not found) {target}")
            continue
        size = dir_size(target)
        print(
            f"  {'[dry-run]' if dry_run else 'delete'} {target} ({format_bytes(size)})"
        )
        if not dry_run:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink(missing_ok=True)

    print("Artifacts dry run complete." if dry_run else "Artifacts cleanup complete.")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clear MT5 cache and optimizer artifacts")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--cache-only", action="store_true")
    parser.add_argument("--artifacts-only", action="store_true")
    parser.add_argument("--no-stop", action="store_true")
    parser.add_argument("--mt5-data")
    parser.add_argument("--terminal", default=DEFAULT_TERMINAL)
    parser.add_argument("--cache-dir")
    parser.add_argument("--work-dir", default=str(PACKAGE_ROOT))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.cache_only and args.artifacts_only:
        print("Conflicting flags: --cache-only and --artifacts-only cannot be used together.")
        return 1

    should_clean_cache = not args.artifacts_only
    should_clean_artifacts = not args.cache_only

    if not args.dry_run and not args.no_stop:
        stop_mt5()
    elif args.dry_run:
        print("[dry-run] Skipping MT5 stop.")

    if should_clean_cache:
        data_dir = resolve_mt5_data_dir(
            terminal=args.terminal,
            mt5_data=args.mt5_data,
        )
        cache_dir = Path(args.cache_dir or data_dir / "Tester" / "cache")
        run_cache_cleanup(cache_dir=cache_dir, data_dir=data_dir, dry_run=args.dry_run)

    if should_clean_artifacts:
        run_artifacts_cleanup(Path(args.work_dir), dry_run=args.dry_run)

    if args.dry_run:
        print("Dry run complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
