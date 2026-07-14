"""Shared Postgres connection helpers for MT5 Python scripts."""

from __future__ import annotations

try:
    import psycopg
except ImportError:  # pragma: no cover - optional dependency
    psycopg = None  # type: ignore[assignment]


def connect(database_url: str) -> "psycopg.Connection":
    if psycopg is None:
        raise RuntimeError("psycopg is required; install EAs/requirements.txt")

    conninfo = database_url
    if "sslmode=" not in conninfo:
        conninfo += ("&" if "?" in conninfo else "?") + "sslmode=require"
    return psycopg.connect(conninfo, autocommit=True, connect_timeout=10)
