"""Build and persist the all-favorites portfolio snapshot via TradeEcho API."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from mt5_env import load_repo_env
from mt5_portfolio_merge import (
    ALL_FAVORITES_PORTFOLIO_ID,
    load_strategy_series,
    merge_strategy_series,
    normalize_favorite_export_rows,
)
from mt5_trade_echo_api import TradeEchoOptimizerApi
from mt5_trade_echo_auth import assert_optimizer_access


def build_all_favorites_portfolio(api: TradeEchoOptimizerApi) -> dict[str, Any]:
    rows = normalize_favorite_export_rows(api.get_favorites())
    if not rows:
        raise ValueError("No favorite strategies found")

    strategies = [load_strategy_series(row) for row in rows]
    merged = merge_strategy_series(strategies)

    api.upsert_portfolio(
        {
            "strategyIds": merged.strategy_ids,
            "strategyCount": len(merged.strategy_ids),
            "summary": merged.summary,
            "reportMetrics": merged.report_metrics,
            "equityCurve": merged.equity_curve,
        }
    )

    last_point = merged.equity_curve[-1] if merged.equity_curve else None
    final_balance = merged.summary.get("final_balance")
    if final_balance is None and last_point is not None:
        final_balance = last_point.get("balance")
    final_equity = merged.summary.get("final_equity")
    if final_equity is None and last_point is not None:
        final_equity = last_point.get("equity")

    return {
        "portfolio_id": ALL_FAVORITES_PORTFOLIO_ID,
        "strategy_count": len(merged.strategy_ids),
        "total_trades": merged.total_trades,
        "final_balance": final_balance,
        "final_equity": final_equity,
        "max_equity_drawdown_relative_pct": merged.summary[
            "max_equity_drawdown_relative_pct"
        ],
        "max_balance_drawdown_relative_pct": merged.summary.get(
            "max_balance_drawdown_relative_pct"
        ),
        "max_strategy_equity_dd_pct": merged.summary.get("max_strategy_equity_dd_pct"),
    }


def refresh_all_favorites_portfolio(api: TradeEchoOptimizerApi) -> dict[str, Any] | None:
    """Rebuild the all-favorites portfolio snapshot, or clear it when none remain."""
    rows = normalize_favorite_export_rows(api.get_favorites())
    if not rows:
        api.clear_portfolio()
        return None
    return build_all_favorites_portfolio(api)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Merge all favorite strategies into one portfolio and save via TradeEcho API.",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    load_repo_env()
    assert_optimizer_access()
    parse_args(argv)

    try:
        api = TradeEchoOptimizerApi.from_env()
        result = refresh_all_favorites_portfolio(api)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - CLI should report failure
        print(f"Portfolio build failed: {exc}", file=sys.stderr)
        return 1

    if result is None:
        print("No favorites remain; portfolio snapshot cleared")
        return 0

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
