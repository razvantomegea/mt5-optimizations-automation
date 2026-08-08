"""Resolve MT5 .set file directories, terminal path, and repo-relative artifact paths."""

from __future__ import annotations

import os
from pathlib import Path

from mt5_workspace import PACKAGE_ROOT

DEFAULT_BEST_DIR = PACKAGE_ROOT / "reports" / "Best"
DEFAULT_FAVORITES_DIR = PACKAGE_ROOT / "reports" / "Favorites"
DEFAULT_SET_FILES_DIR = PACKAGE_ROOT / "SetFiles"
# packages/mt5-optimizations-automation → ../../EAs/SetFiles
EAS_SET_FILES_DIR = PACKAGE_ROOT.parent.parent / "EAs" / "SetFiles"

# Prefer env / first existing install; FTMO listed first for TradeEcho operators.
_TERMINAL_CANDIDATES = (
    Path(r"C:\Program Files\MetaTrader FTMO\terminal64.exe"),
    Path(r"C:\Program Files\MetaTrader 5\terminal64.exe"),
)


def _dir_has_set_files(path: Path) -> bool:
    if not path.is_dir():
        return False
    return next(path.rglob("*.set"), None) is not None


def resolve_set_dir(*, required: bool = False) -> Path | None:
    """Return the MT5 optimization .set directory.

    Priority: ``MT5_SET_DIR`` env → package ``SetFiles/`` (if it has ``.set`` files)
    → ``../../EAs/SetFiles``. Pass ``--validate-set-dir`` when using a custom path.
    """
    env = os.environ.get("MT5_SET_DIR", "").strip()
    if env:
        path = Path(env).expanduser().resolve()
        if required and not path.is_dir():
            raise FileNotFoundError(f"MT5 set directory not found: {path}")
        return path

    if _dir_has_set_files(DEFAULT_SET_FILES_DIR):
        return DEFAULT_SET_FILES_DIR

    if _dir_has_set_files(EAS_SET_FILES_DIR):
        return EAS_SET_FILES_DIR.resolve()

    if required:
        raise FileNotFoundError(
            "MT5 set directory is required. Pass --validate-set-dir or set MT5_SET_DIR "
            "(or add .set grids under ./SetFiles or ../../EAs/SetFiles)."
        )
    return None


def resolve_terminal(
    *,
    explicit: str | Path | None = None,
    required: bool = True,
) -> Path | None:
    """Resolve ``terminal64.exe``.

    Priority: ``explicit`` → ``MT5_TERMINAL`` env → first existing well-known path.
    """
    candidates: list[Path] = []
    if explicit is not None and str(explicit).strip():
        candidates.append(Path(str(explicit).strip()).expanduser())
    env = os.environ.get("MT5_TERMINAL", "").strip()
    if env:
        candidates.append(Path(env).expanduser())
    candidates.extend(_TERMINAL_CANDIDATES)

    seen: set[Path] = set()
    for raw in candidates:
        path = raw.resolve()
        if path in seen:
            continue
        seen.add(path)
        if path.is_file():
            return path

    if required:
        hint = env or str(_TERMINAL_CANDIDATES[0])
        raise FileNotFoundError(
            f"MT5 terminal not found (tried env/explicit/defaults). "
            f"Set MT5_TERMINAL or pass --terminal. Last hint: {hint}"
        )
    return None


def default_terminal_arg() -> str:
    """Argparse default string: env, existing install, or FTMO path hint."""
    found = resolve_terminal(required=False)
    if found is not None:
        return str(found)
    env = os.environ.get("MT5_TERMINAL", "").strip()
    if env:
        return env
    return str(_TERMINAL_CANDIDATES[0])
