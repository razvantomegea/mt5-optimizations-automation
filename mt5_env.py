"""Load env files for MT5 CLI scripts."""

from __future__ import annotations

import os
from pathlib import Path

from mt5_workspace import PACKAGE_ROOT


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")


def load_repo_env() -> None:
    _load_env_file(PACKAGE_ROOT / ".env.local")
    _load_env_file(PACKAGE_ROOT / ".env")
