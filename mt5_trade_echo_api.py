"""TradeEcho optimizer HTTP API client (x-user-id auth, no direct Postgres)."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

from mt5_env import load_repo_env
from mt5_trade_echo_auth import (
    resolve_trade_echo_api_base,
    resolve_trade_echo_user_id,
)


class TradeEchoOptimizerApi:
    def __init__(self, *, user_id: str, base_url: str) -> None:
        self._user_id = user_id
        self._base_url = base_url.rstrip("/")

    @classmethod
    def from_env(cls) -> "TradeEchoOptimizerApi":
        load_repo_env()
        return cls(
            user_id=resolve_trade_echo_user_id(),
            base_url=resolve_trade_echo_api_base(),
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        data = None
        headers = {
            "x-user-id": self._user_id,
            "Accept": "application/json",
        }
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read().decode("utf-8")
                if not raw.strip():
                    return {}
                return json.loads(raw)
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace").strip()
            try:
                parsed = json.loads(detail)
                if isinstance(parsed, dict) and parsed.get("error"):
                    detail = str(parsed["error"])
            except json.JSONDecodeError:
                pass
            raise RuntimeError(
                f"TradeEcho API {method} {path} failed (HTTP {error.code}): "
                f"{detail or error.reason}"
            ) from error
        except urllib.error.URLError as error:
            raise RuntimeError(
                f"Could not reach TradeEcho API ({url}): {error.reason}"
            ) from error

    def get_favorites(self) -> list[dict[str, Any]]:
        payload = self._request("GET", "/api/optimizer/favorites")
        favorites = payload.get("favorites")
        if not isinstance(favorites, list):
            return []
        return favorites

    def upsert_portfolio(self, portfolio: dict[str, Any]) -> None:
        self._request("PUT", "/api/optimizer/portfolio", body=portfolio)

    def clear_portfolio(self) -> None:
        self._request("DELETE", "/api/optimizer/portfolio")

    def post_run_event(self, run_id: str, event_type: str, payload: dict[str, Any]) -> None:
        self._request(
            "POST",
            f"/api/optimizer/runs/{run_id}/events",
            body={"type": event_type, "payload": payload},
        )

    def mark_worker_running(self, run_id: str) -> None:
        self._request(
            "POST",
            "/api/optimizer/worker",
            body={"op": "markRunning", "runId": run_id},
        )

    def touch_heartbeat(self, *, busy: bool = False) -> None:
        self._request(
            "POST",
            "/api/optimizer/worker",
            body={"op": "heartbeat", "busy": busy},
        )

    def mark_command_done(
        self,
        *,
        command_id: str,
        status: str,
        error: str | None = None,
    ) -> None:
        body: dict[str, Any] = {
            "op": "markCommandDone",
            "commandId": command_id,
            "status": status,
        }
        if error is not None:
            body["error"] = error
        self._request("POST", "/api/optimizer/worker", body=body)

    def start_run(
        self,
        *,
        command_id: str,
        from_date: str,
        to_date: str,
        symbols: list[str],
        timeframes: list[str],
        resume: bool,
        run_id: str,
    ) -> str:
        payload = self._request(
            "POST",
            "/api/optimizer/worker",
            body={
                "op": "startRun",
                "commandId": command_id,
                "fromDate": from_date,
                "toDate": to_date,
                "symbols": symbols,
                "timeframes": timeframes,
                "resume": resume,
                "runId": run_id,
            },
        )
        return str(payload.get("runId") or run_id)

    def set_worker_idle(self) -> None:
        self._request("POST", "/api/optimizer/worker", body={"op": "setIdle"})

    def mark_running_runs_stopped(self) -> None:
        self._request("POST", "/api/optimizer/worker", body={"op": "stopRuns"})

    def fail_running_runs(self, *, error: str) -> None:
        self._request(
            "POST",
            "/api/optimizer/worker",
            body={"op": "failRuns", "error": error},
        )

    def clear_optimization_data(self) -> None:
        self._request("POST", "/api/optimizer/worker", body={"op": "clearData"})

    def claim_pending_command(self, *, interruptible_only: bool = False) -> dict[str, Any] | None:
        payload = self._request(
            "POST",
            "/api/optimizer/worker",
            body={"op": "poll", "interruptibleOnly": interruptible_only},
        )
        command = payload.get("command")
        return command if isinstance(command, dict) else None


def resolve_optimization_run_id() -> str:
    run_id = os.environ.get("OPTIMIZATION_RUN_ID", "").strip()
    if not run_id:
        return ""
    return run_id
