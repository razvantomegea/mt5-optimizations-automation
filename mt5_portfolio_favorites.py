"""Build and persist per-company favorites portfolio snapshots via PositionRelay API."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from mt5_env import load_repo_env
from mt5_portfolio_combined import run_combined_portfolio
from mt5_portfolio_manifest import PortfolioManifestError, build_portfolio_manifest
from mt5_portfolio_merge import (
    build_company_portfolio_id,
    load_strategy_series,
    merge_strategy_series,
    normalize_favorite_export_rows,
    resolve_favorite_company_for_portfolio,
)
from mt5_position_relay_api import PositionRelayOptimizerApi
from mt5_position_relay_auth import assert_optimizer_access


@dataclass(frozen=True)
class FavoriteCompanyGroup:
    portfolio_id: str
    company: str
    rows: list[dict[str, Any]]


@dataclass(frozen=True)
class PreparedCompanyPortfolio:
    portfolio_id: str
    company: str
    payload: dict[str, Any]
    summary: dict[str, Any]


def _result_summary(merged: Any) -> dict[str, Any]:
    last_point = merged.equity_curve[-1] if merged.equity_curve else None
    final_balance = merged.summary.get("final_balance")
    if final_balance is None and last_point is not None:
        final_balance = last_point.get("balance")
    final_equity = merged.summary.get("final_equity")
    if final_equity is None and last_point is not None:
        final_equity = last_point.get("equity")

    return {
        "portfolio_id": merged.summary["portfolio_id"],
        "company": merged.summary.get("company"),
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


def group_favorites_by_company(
    rows: list[dict[str, Any]],
) -> list[FavoriteCompanyGroup]:
    """Group favorites by company portfolio id, not display-string equality."""
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    display: dict[str, str] = {}
    for row in rows:
        company = resolve_favorite_company_for_portfolio(row.get("company"))
        portfolio_id = build_company_portfolio_id(company)
        buckets[portfolio_id].append(row)
        if portfolio_id not in display:
            display[portfolio_id] = company
    return [
        FavoriteCompanyGroup(
            portfolio_id=portfolio_id,
            company=display[portfolio_id],
            rows=buckets[portfolio_id],
        )
        for portfolio_id in sorted(
            buckets.keys(),
            key=lambda pid: display[pid].casefold(),
        )
    ]


def prepare_company_favorites_portfolio(
    *,
    company: str,
    rows: list[dict[str, Any]],
) -> PreparedCompanyPortfolio:
    """Load + merge into an upsert payload without writing.

    Builds a versioned combined-tester manifest first and rejects incompatible
    or missing inputs. The Python deal merge remains a transitional diagnostic
    payload until the MT5 combined run cutover.
    """
    if not rows:
        raise ValueError(f"No favorite strategies found for company {company}")

    # Fail closed: missing/incompatible inputs never produce a plausible merge.
    try:
        manifest = build_portfolio_manifest(rows, company=company)
    except PortfolioManifestError as exc:
        raise ValueError(str(exc)) from exc

    portfolio_id = manifest.portfolio_id
    strategies = [load_strategy_series(row) for row in rows]
    merged = merge_strategy_series(
        strategies,
        portfolio_id=portfolio_id,
        company=manifest.company,
        initial_deposit=manifest.deposit,
    )
    payload = {
        "strategyIds": merged.strategy_ids,
        "strategyCount": len(merged.strategy_ids),
        "manifest": manifest.to_dict(),
        "summary": {
            **merged.summary,
            "manifest_version": manifest.version,
            "tester_model": manifest.tester_model,
            "source": "python_deal_merge_diagnostic",
            "validation_state": "unverified",
        },
        "reportMetrics": merged.report_metrics,
        "equityCurve": merged.equity_curve,
    }
    return PreparedCompanyPortfolio(
        portfolio_id=portfolio_id,
        company=manifest.company,
        payload=payload,
        summary=_result_summary(merged),
    )


def prepare_company_unavailable_portfolio(
    *,
    company: str,
    rows: list[dict[str, Any]],
    reason: str,
) -> PreparedCompanyPortfolio:
    """Persist a non-displayable snapshot so stale verified metrics cannot linger."""
    resolved = resolve_favorite_company_for_portfolio(company)
    portfolio_id = build_company_portfolio_id(resolved)
    strategy_ids = [str(row["id"]) for row in rows if isinstance(row.get("id"), str)]
    strategies = [
        {
            "result_id": str(row.get("id") or ""),
            "symbol": str(row.get("symbol") or ""),
            "timeframe": str(row.get("timeframe") or ""),
            "profile": row.get("profile") if isinstance(row.get("profile"), str) else None,
            "pass_id": row.get("pass_id") if isinstance(row.get("pass_id"), int) else None,
            "risk_pct": None,
            "trade_count": 0,
        }
        for row in rows
    ]
    summary = {
        "portfolio_id": portfolio_id,
        "company": resolved,
        "deposit": 0.0,
        "strategy_count": len(rows),
        "total_trades": 0,
        "final_balance": None,
        "final_equity": None,
        "max_equity_drawdown_relative_pct": 0.0,
        "strategies": strategies,
        "source": "mt5_combined_tester",
        "validation_state": "unavailable",
        "validation_failure_reason": reason,
    }
    return PreparedCompanyPortfolio(
        portfolio_id=portfolio_id,
        company=resolved,
        payload={
            "strategyIds": strategy_ids,
            "strategyCount": len(strategy_ids),
            "summary": summary,
            "reportMetrics": {},
            "equityCurve": [],
        },
        summary={
            "portfolio_id": portfolio_id,
            "company": resolved,
            "strategy_count": len(rows),
            "total_trades": 0,
            "final_balance": None,
            "final_equity": None,
            "max_equity_drawdown_relative_pct": 0.0,
            "validation_state": "unavailable",
            "validation_failure_reason": reason,
        },
    )


def prepare_company_verified_portfolio(
    *,
    company: str,
    rows: list[dict[str, Any]],
) -> PreparedCompanyPortfolio:
    """Build one shared-account real-tick tester snapshot for a company.

    Missing or incompatible inputs raise ``ValueError`` (caller may mark
    unavailable). MT5 runtime failures raise ``RuntimeError``.
    """
    if not rows:
        raise ValueError(f"No favorite strategies found for company {company}")
    try:
        manifest = build_portfolio_manifest(rows, company=company)
    except PortfolioManifestError as exc:
        raise ValueError(str(exc)) from exc
    combined = run_combined_portfolio(manifest)
    return PreparedCompanyPortfolio(
        portfolio_id=manifest.portfolio_id,
        company=manifest.company,
        payload=combined.payload,
        summary=combined.summary,
    )


def _prepare_company_or_unavailable(
    *,
    company: str,
    rows: list[dict[str, Any]],
) -> PreparedCompanyPortfolio:
    try:
        return prepare_company_verified_portfolio(company=company, rows=rows)
    except (ValueError, RuntimeError) as exc:
        return prepare_company_unavailable_portfolio(
            company=company,
            rows=rows,
            reason=str(exc),
        )


def refresh_has_unavailable(results: list[dict[str, Any]]) -> bool:
    return any(result.get("validation_state") == "unavailable" for result in results)


def unavailable_refresh_error(results: list[dict[str, Any]]) -> str:
    reasons = [
        str(result.get("validation_failure_reason") or result.get("company") or "unknown")
        for result in results
        if result.get("validation_state") == "unavailable"
    ]
    if not reasons:
        return "Portfolio refresh left one or more companies unavailable"
    return "Portfolio unavailable: " + "; ".join(reasons)


def build_company_favorites_portfolio(
    api: PositionRelayOptimizerApi,
    *,
    company: str,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    prepared = prepare_company_favorites_portfolio(company=company, rows=rows)
    api.upsert_portfolio(prepared.payload)
    return prepared.summary


def build_company_verified_portfolio(
    api: PositionRelayOptimizerApi,
    *,
    company: str,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    prepared = prepare_company_verified_portfolio(company=company, rows=rows)
    api.upsert_portfolio(prepared.payload)
    return prepared.summary


def refresh_company_favorites_portfolios(
    api: PositionRelayOptimizerApi,
    *,
    company: str | None = None,
) -> list[dict[str, Any]]:
    """Rebuild company portfolio snapshots.

    When ``company`` is set, only that company's portfolio is upserted or cleared.
    When omitted, prepares every company payload first (so MT5 runtime failures leave
    prior snapshots untouched), then upserts and reconciles orphans + legacy.

    Missing reports / incompatible inputs produce an ``unavailable`` snapshot for
    that company so other companies can still publish verified metrics.
    """
    rows = normalize_favorite_export_rows(api.get_favorites())
    grouped = group_favorites_by_company(rows)

    if company is not None:
        resolved = resolve_favorite_company_for_portfolio(company)
        portfolio_id = build_company_portfolio_id(resolved)
        match = next((g for g in grouped if g.portfolio_id == portfolio_id), None)
        if match is None:
            api.clear_portfolio(portfolio_id=portfolio_id)
            return []
        prepared = _prepare_company_or_unavailable(
            company=match.company,
            rows=match.rows,
        )
        api.upsert_portfolio(prepared.payload)
        return [prepared.summary]

    if not rows:
        api.clear_portfolio(all_portfolios=True)
        return []

    # Build every replacement payload before any write. Input errors and MT5
    # runtime failures become unavailable snapshots so stale verified metrics
    # cannot linger; callers may still treat unavailable as a sync failure.
    prepared = [
        _prepare_company_or_unavailable(company=group.company, rows=group.rows)
        for group in grouped
    ]

    results: list[dict[str, Any]] = []
    keep_ids: list[str] = []
    for item in prepared:
        api.upsert_portfolio(item.payload)
        results.append(item.summary)
        keep_ids.append(item.portfolio_id)

    api.reconcile_portfolios(keep_portfolio_ids=keep_ids)
    return results


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run combined real-tick MT5 Favorites portfolios and save verified results via PositionRelay API."
        ),
    )
    parser.add_argument(
        "--company",
        default=None,
        help="Rebuild only this company (default: all companies with favorites).",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    load_repo_env()
    assert_optimizer_access()
    args = parse_args(argv)

    try:
        api = PositionRelayOptimizerApi.from_env()
        results = refresh_company_favorites_portfolios(api, company=args.company)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - CLI should report failure
        print(f"Portfolio build failed: {exc}", file=sys.stderr)
        return 1

    if not results:
        print("No favorites remain; portfolio snapshot(s) cleared")
        return 0

    print(json.dumps(results if len(results) > 1 else results[0], indent=2))
    if refresh_has_unavailable(results):
        print(unavailable_refresh_error(results), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
