"""Move a passed strategy's report and .set file from Best/ to Favorites/."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from mt5_paths import DEFAULT_BEST_DIR, DEFAULT_FAVORITES_DIR

REPORT_SUFFIXES = {".htm", ".html", ".xml"}


def _rollback_moves(moves: list[tuple[Path, Path]]) -> None:
    for src, dest in reversed(moves):
        if dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dest), src)


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
    dest_set_dir = to_dir / "sets"
    dest_set = dest_set_dir / set_file.name

    src_report_dir = from_dir / "reports" / symbol
    report_moves: list[tuple[Path, Path]] = []
    if src_report_dir.is_dir():
        dest_report_dir = to_dir / "reports" / symbol
        for report in sorted(src_report_dir.iterdir()):
            if not report.is_file():
                continue
            if report.suffix.lower() not in REPORT_SUFFIXES:
                continue
            if stem not in report.name:
                continue
            report_moves.append((report, dest_report_dir / report.name))

    moved: list[Path] = []
    completed_moves: list[tuple[Path, Path]] = []

    try:
        dest_set_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(set_file), dest_set)
        completed_moves.append((set_file, dest_set))
        moved.append(dest_set)

        if report_moves:
            dest_report_dir = to_dir / "reports" / symbol
            dest_report_dir.mkdir(parents=True, exist_ok=True)
            for src_report, dest_report in report_moves:
                shutil.move(str(src_report), dest_report)
                completed_moves.append((src_report, dest_report))
                moved.append(dest_report)
    except Exception:
        _rollback_moves(completed_moves)
        raise

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
