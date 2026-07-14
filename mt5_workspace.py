"""Resolve TradeEcho app root vs standalone open-source package root."""

from __future__ import annotations

import os
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent


def resolve_app_root() -> Path:
    """Monorepo: ea-sync root (reports/, .env). Standalone: this package directory."""
    override = os.environ.get("TRADEECHO_APP_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()

    monorepo_root = PACKAGE_ROOT.parent.parent
    if (monorepo_root / "package.json").is_file() and (monorepo_root / "app").is_dir():
        return monorepo_root

    return PACKAGE_ROOT
