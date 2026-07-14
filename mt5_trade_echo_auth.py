"""Verify TradeEcho Ultimate subscription before running optimizer CLIs."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import NoReturn

from mt5_env import load_repo_env


def _fail(message: str, *, code: int = 1) -> NoReturn:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def resolve_trade_echo_api_base() -> str:
    base = (
        os.environ.get("TRADEECHO_API_BASE_URL", "").strip()
        or os.environ.get("NEXT_PUBLIC_EA_API_BASE_URL", "").strip()
        or "https://ea-sync-production.up.railway.app"
    )
    return base.rstrip("/")


def resolve_trade_echo_user_id() -> str:
    user_id = os.environ.get("TRADEECHO_USER_ID", "").strip()
    if not user_id:
        _fail(
            "TRADEECHO_USER_ID is required. Copy your User ID from the TradeEcho dashboard Setup page."
        )
    return user_id


def assert_optimizer_access(*, skip: bool = False) -> None:
    """Call GET /api/optimizer/access with x-user-id (same pattern as MQ5 EAs)."""
    if skip or os.environ.get("TRADEECHO_SKIP_ACCESS_CHECK", "").strip() in {
        "1",
        "true",
        "yes",
    }:
        return

    load_repo_env()
    user_id = resolve_trade_echo_user_id()
    url = f"{resolve_trade_echo_api_base()}/api/optimizer/access"
    request = urllib.request.Request(
        url,
        headers={"x-user-id": user_id, "Accept": "application/json"},
        method="GET",
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status != 200:
                _fail(f"TradeEcho optimizer access denied (HTTP {response.status}).")
            body = response.read().decode("utf-8")
            if body.strip():
                json.loads(body)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace").strip()
        if error.code == 403:
            _fail(
                "Active Ultimate subscription required for MT5 optimizations. "
                "Upgrade at https://trade-echo.com/dashboard/billing"
            )
        _fail(
            f"TradeEcho optimizer access check failed (HTTP {error.code}): {detail or error.reason}"
        )
    except urllib.error.URLError as error:
        _fail(f"Could not reach TradeEcho API ({url}): {error.reason}")
