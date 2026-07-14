"""Resolve MT5 .set file directories and repo-relative artifact paths."""

from __future__ import annotations

import os
from pathlib import Path

from mt5_workspace import PACKAGE_ROOT

DEFAULT_BEST_DIR = PACKAGE_ROOT / "reports" / "Best"
DEFAULT_FAVORITES_DIR = PACKAGE_ROOT / "reports" / "Favorites"
DEFAULT_SET_FILES_DIR = PACKAGE_ROOT / "SetFiles"


def resolve_set_dir(*, required: bool = False) -> Path | None:
    """Return the MT5 optimization .set directory.

    Priority: ``MT5_SET_DIR`` env → ``SetFiles/`` next to these scripts (gitignored).
    Pass ``--validate-set-dir`` when using a custom path.
    """
    env = os.environ.get("MT5_SET_DIR", "").strip()
    if env:
        path = Path(env).expanduser().resolve()
        if required and not path.is_dir():
            raise FileNotFoundError(f"MT5 set directory not found: {path}")
        return path

    if DEFAULT_SET_FILES_DIR.is_dir():
        return DEFAULT_SET_FILES_DIR

    if required:
        raise FileNotFoundError(
            "MT5 set directory is required. Pass --validate-set-dir or set MT5_SET_DIR."
        )
    return None
