"""Move a passed strategy's report and .set file from Best/ to Favorites/."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from mt5_paths import DEFAULT_BEST_DIR, DEFAULT_FAVORITES_DIR

REPORT_SUFFIXES = {".htm", ".html", ".xml"}


def transfer_strategy(
    *,
    set_file: Path,
    symbol: str,
    from_dir: Path,
    to_dir: Path,
) -> list[Path]:
    if not set_file.is_file():
        raise FileNotFoundError(f"Set file not found: {set_file}")

    stem = set_file.stem
    moved: list[Path] = []

    dest_set_dir = to_dir / "sets"
    dest_set_dir.mkdir(parents=True, exist_ok=True)
    dest_set = dest_set_dir / set_file.name
    shutil.move(str(set_file), dest_set)
    moved.append(dest_set)

    src_report_dir = from_dir / "reports" / symbol
    if not src_report_dir.is_dir():
        return moved

    dest_report_dir = to_dir / "reports" / symbol
    dest_report_dir.mkdir(parents=True, exist_ok=True)
    for report in sorted(src_report_dir.iterdir()):
        if not report.is_file():
            continue
        if report.suffix.lower() not in REPORT_SUFFIXES:
            continue
        if stem not in report.name:
            continue
        dest_report = dest_report_dir / report.name
        shutil.move(str(report), dest_report)
        moved.append(dest_report)

    return moved


def move_strategy_to_favorites(
    *,
    set_file: Path,
    symbol: str,
    best_dir: Path,
    favorites_dir: Path,
) -> list[Path]:
    return transfer_strategy(
        set_file=set_file,
        symbol=symbol,
        from_dir=best_dir,
        to_dir=favorites_dir,
    )


def move_strategy_from_favorites(
    *,
    set_file: Path,
    symbol: str,
    best_dir: Path,
    favorites_dir: Path,
) -> list[Path]:
    return transfer_strategy(
        set_file=set_file,
        symbol=symbol,
        from_dir=favorites_dir,
        to_dir=best_dir,
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Move a strategy .set and report files from reports/Best to reports/Favorites.",
    )
    parser.add_argument("--set-file", required=True, help="Path to the .set file under Best/sets/")
    parser.add_argument("--symbol", required=True, help="Symbol folder under reports/*/reports/")
    parser.add_argument(
        "--best-dir",
        default=str(DEFAULT_BEST_DIR),
        help=f"Source Best directory (default: {DEFAULT_BEST_DIR})",
    )
    parser.add_argument(
        "--favorites-dir",
        default=str(DEFAULT_FAVORITES_DIR),
        help=f"Destination Favorites directory (default: {DEFAULT_FAVORITES_DIR})",
    )
    parser.add_argument(
        "--unfavorite",
        action="store_true",
        help="Move files from Favorites/ back to Best/ instead of into Favorites/",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    set_file = Path(args.set_file).expanduser().resolve()
    symbol = args.symbol.strip().upper()
    best_dir = Path(args.best_dir).expanduser().resolve()
    favorites_dir = Path(args.favorites_dir).expanduser().resolve()
    if args.unfavorite:
        moved = move_strategy_from_favorites(
            set_file=set_file,
            symbol=symbol,
            best_dir=best_dir,
            favorites_dir=favorites_dir,
        )
    else:
        moved = move_strategy_to_favorites(
            set_file=set_file,
            symbol=symbol,
            best_dir=best_dir,
            favorites_dir=favorites_dir,
        )
    for path in moved:
        print(f"Moved to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
