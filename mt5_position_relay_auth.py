"""Verify PositionRelay Ultimate subscription before running optimizer CLIs."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import NoReturn

from mt5_env import load_repo_env

DEFAULT_POSITIONRELAY_API_BASE_URL = "https://positionrelay.com"
_TRUTHY_ENV = {"1", "true", "yes"}


def _fail(message: str, *, code: int = 1) -> NoReturn:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _is_truthy_env(name: str) -> bool:
    return _env(name) in _TRUTHY_ENV


def resolve_position_relay_api_base() -> str:
    base = (
        _env("POSITIONRELAY_API_BASE_URL")
        or _env("TRADEECHO_API_BASE_URL")  # legacy operator .env
        or DEFAULT_POSITIONRELAY_API_BASE_URL
    )
    return base.rstrip("/")


def resolve_position_relay_user_id() -> str:
    user_id = _env("POSITIONRELAY_USER_ID") or _env("TRADEECHO_USER_ID")
    if not user_id:
        _fail(
            "POSITIONRELAY_USER_ID is required. Copy your User ID from the PositionRelay dashboard Setup page."
        )
    return user_id


def assert_optimizer_access(*, skip: bool = False) -> None:
    """Call GET /api/optimizer/access with x-user-id (same pattern as MQ5 EAs)."""
    if (
        skip
        or _is_truthy_env("POSITIONRELAY_SKIP_ACCESS_CHECK")
        or _is_truthy_env("TRADEECHO_SKIP_ACCESS_CHECK")
    ):
        return

    load_repo_env()
    user_id = resolve_position_relay_user_id()
    url = f"{resolve_position_relay_api_base()}/api/optimizer/access"
    request = urllib.request.Request(
        url,
        headers={"x-user-id": user_id, "Accept": "application/json"},
        method="GET",
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status != 200:
                _fail(f"PositionRelay optimizer access denied (HTTP {response.status}).")
            body = response.read().decode("utf-8")
            if body.strip():
                try:
                    json.loads(body)
                except json.JSONDecodeError:
                    _fail("PositionRelay optimizer access check returned invalid JSON.")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace").strip()
        if error.code == 403:
            _fail(
                "Active Ultimate subscription required for MT5 optimizations. "
                "Upgrade at https://positionrelay.com/dashboard/billing"
            )
        _fail(
            f"PositionRelay optimizer access check failed (HTTP {error.code}): {detail or error.reason}"
        )
    except urllib.error.URLError as error:
        _fail(f"Could not reach PositionRelay API ({url}): {error.reason}")
