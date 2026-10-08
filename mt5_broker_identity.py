"""Terminal broker identity: Server= from common.ini plus learned server→company cache."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mt5_portfolio_merge import resolve_favorite_company_for_portfolio
from mt5_workspace import PACKAGE_ROOT

BROKER_SERVERS_PATH = PACKAGE_ROOT / "reports" / "PortfolioBuilds" / "broker_servers.json"


class BrokerMismatchError(Exception):
    """Combined tester ran on a different broker than the portfolio company.

    Intentionally not a subclass of ValueError/RuntimeError so refresh callers
    can skip without marking the company unavailable.
    """

    def __init__(
        self,
        *,
        expected_company: str,
        terminal_company: str,
        message: str | None = None,
    ) -> None:
        self.expected_company = expected_company
        self.terminal_company = terminal_company
        super().__init__(
            message
            or (
                f"Terminal broker {terminal_company!r} does not match "
                f"portfolio company {expected_company!r}"
            )
        )


def read_terminal_server(data_dir: Path) -> str | None:
    """Return ``Server=`` under ``[Common]`` in ``config/common.ini``, or None.

    Only the Server key is read; Login and other credentials are never touched.
    """
    path = Path(data_dir) / "config" / "common.ini"
    if not path.is_file():
        return None
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = raw.decode("utf-16", errors="replace")
    else:
        text = raw.decode("utf-8-sig", errors="replace")
    section: str | None = None
    for line in text.splitlines():
        stripped = line.strip().strip("\x00")
        if not stripped or stripped.startswith(";") or stripped.startswith("#"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1].strip()
            continue
        if section != "Common":
            continue
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        if key.strip().casefold() == "server":
            server = value.strip()
            return server or None
    return None


def load_server_companies(
    *,
    path: Path | None = None,
) -> dict[str, str]:
    """Load the learned server→company map (empty when missing/invalid)."""
    target = path or BROKER_SERVERS_PATH
    if not target.is_file():
        return {}
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, str) and key.strip() and value.strip():
            out[key.strip()] = resolve_favorite_company_for_portfolio(value)
    return out


def record_server_company(
    server: str,
    company: str,
    *,
    path: Path | None = None,
) -> None:
    """Persist ``server → company`` after a successful combined-tester report."""
    resolved_server = server.strip()
    if not resolved_server:
        return
    resolved_company = resolve_favorite_company_for_portfolio(company)
    target = path or BROKER_SERVERS_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    mapping = load_server_companies(path=target)
    mapping[resolved_server] = resolved_company
    payload: dict[str, Any] = dict(sorted(mapping.items()))
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def resolve_terminal_company(
    data_dir: Path,
    *,
    cache_path: Path | None = None,
) -> tuple[str | None, str | None]:
    """Return ``(server, company)`` for the terminal data dir.

    Company is None when the server is unknown or not yet learned.
    """
    server = read_terminal_server(data_dir)
    if server is None:
        return None, None
    company = load_server_companies(path=cache_path).get(server)
    return server, company
