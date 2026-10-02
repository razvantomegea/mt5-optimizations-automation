"""Tests for per-company portfolio generation CLI and API persistence."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mt5_portfolio_favorites import (
    build_company_favorites_portfolio,
    group_favorites_by_company,
    main,
    refresh_company_favorites_portfolios,
)
from mt5_portfolio_manifest import PortfolioManifest, ManifestStrategy
from mt5_portfolio_combined import CombinedPortfolioResult
from mt5_portfolio_merge import (
    MIGRATION_DEFAULT_COMPANY,
    build_company_portfolio_id,
    MergedPortfolio,
)
from portfolio_test_helpers import series, trade

TRADESLIDE = MIGRATION_DEFAULT_COMPANY
TRADESLIDE_PORTFOLIO_ID = build_company_portfolio_id(TRADESLIDE)
FTMO_PORTFOLIO_ID = build_company_portfolio_id("FTMO")


def _manifest(*, company: str = TRADESLIDE, deposit: float = 100_000) -> PortfolioManifest:
    return PortfolioManifest(
        version=1,
        portfolio_id=build_company_portfolio_id(company),
        company=company,
        server=None,
        from_date="2014.09.16",
        to_date="2026.09.16",
        deposit=deposit,
        currency="USD",
        leverage="1:33",
        tester_model=4,
        strategies=(
            ManifestStrategy(
                result_id="strategy-a",
                symbol="EURUSD",
                timeframe="M15",
                profile="Classic",
                pass_id=1,
                set_file="EURUSD_M15_Classic_pass1.set",
                report_stem="EURUSD_M15_Classic_pass1",
                magic=4_991_701_301,
                risk_pct=1.0,
                parameters={},
            ),
        ),
    )


def _merged_portfolio(*, company: str = TRADESLIDE) -> MergedPortfolio:
    portfolio_id = build_company_portfolio_id(company)
    return MergedPortfolio(
        strategy_ids=["strategy-a"],
        equity_curve=[
            {"time": "2020-01-01T00:00:00", "balance": 100_000, "equity": 100_000},
            {"time": "2020-01-02T00:00:00", "balance": 101_000, "equity": 101_000},
        ],
        total_trades=1,
        report_metrics={"format": "html", "metrics": {"Total trades": "1"}},
        summary={
            "portfolio_id": portfolio_id,
            "company": company,
            "deposit": 100_000,
            "strategy_count": 1,
            "total_trades": 1,
            "max_equity_drawdown_relative_pct": 0.0,
            "max_balance_drawdown_relative_pct": 0.0,
            "max_strategy_equity_dd_pct": 11.5,
            "strategies": [
                {
                    "result_id": "strategy-a",
                    "symbol": "EURUSD",
                    "timeframe": "M15",
                    "profile": "Classic",
                    "pass_id": 1,
                    "risk_pct": 1.0,
                    "trade_count": 1,
                }
            ],
        },
    )


def _verified_result(*, company: str) -> CombinedPortfolioResult:
    portfolio_id = build_company_portfolio_id(company)
    summary = {
        "portfolio_id": portfolio_id,
        "company": company,
        "strategy_count": 1,
        "total_trades": 1,
        "final_balance": 101000,
        "max_equity_drawdown_relative_pct": 1.0,
    }
    return CombinedPortfolioResult(
        payload={
            "strategyIds": ["strategy-a"],
            "strategyCount": 1,
            "summary": {**summary, "source": "mt5_combined_tester", "validation_state": "verified"},
            "reportMetrics": {"format": "html", "metrics": {"Total Trades": "1"}},
            "equityCurve": [],
        },
        summary=summary,
        report_path=Path("combined.htm"),
    )


def test_group_favorites_by_company_uses_migration_default() -> None:
    grouped = group_favorites_by_company(
        [
            {"id": "a", "company": "FTMO"},
            {"id": "b", "company": None},
            {"id": "c", "company": "  FTMO  "},
        ]
    )
    by_id = {group.portfolio_id: group for group in grouped}
    assert set(by_id) == {FTMO_PORTFOLIO_ID, TRADESLIDE_PORTFOLIO_ID}
    assert [row["id"] for row in by_id[FTMO_PORTFOLIO_ID].rows] == ["a", "c"]
    assert [row["id"] for row in by_id[TRADESLIDE_PORTFOLIO_ID].rows] == ["b"]
    assert by_id[FTMO_PORTFOLIO_ID].company == "FTMO"
    assert by_id[TRADESLIDE_PORTFOLIO_ID].company == TRADESLIDE


def test_group_favorites_by_company_merges_case_variants_not_punct() -> None:
    grouped = group_favorites_by_company(
        [
            {"id": "a", "company": "FTMO"},
            {"id": "b", "company": "ftmo"},
            {"id": "c", "company": "Foo Bar"},
            {"id": "d", "company": "Foo-Bar"},
        ]
    )
    by_id = {group.portfolio_id: group for group in grouped}
    assert set(by_id) == {
        FTMO_PORTFOLIO_ID,
        build_company_portfolio_id("Foo Bar"),
        build_company_portfolio_id("Foo-Bar"),
    }
    assert [row["id"] for row in by_id[FTMO_PORTFOLIO_ID].rows] == ["a", "b"]
    assert [row["id"] for row in by_id[build_company_portfolio_id("Foo Bar")].rows] == [
        "c",
    ]
    assert [row["id"] for row in by_id[build_company_portfolio_id("Foo-Bar")].rows] == [
        "d",
    ]


@patch("mt5_portfolio_favorites.build_portfolio_manifest")
@patch("mt5_portfolio_favorites.merge_strategy_series")
@patch("mt5_portfolio_favorites.load_strategy_series")
def test_build_company_favorites_portfolio_persists_snapshot(
    load_mock: MagicMock,
    merge_mock: MagicMock,
    manifest_mock: MagicMock,
) -> None:
    favorite_row = {
        "id": "strategy-a",
        "symbol": "EURUSD",
        "timeframe": "M15",
        "profile": "Classic",
        "pass_id": 1,
        "report_stem": None,
        "company": TRADESLIDE,
        "summary": {"deposit": 100_000},
        "parameters": {},
        "equity_curve": [],
    }
    api = MagicMock()
    manifest_mock.return_value = _manifest()

    load_mock.return_value = series(
        trades=(
            trade(
                time=datetime(2020, 1, 2),
                profit=1_000,
                equity_before=100_000,
            ),
        ),
        result_id="strategy-a",
    )
    merge_mock.return_value = _merged_portfolio()

    result = build_company_favorites_portfolio(
        api,
        company=TRADESLIDE,
        rows=[favorite_row],
    )

    assert result["portfolio_id"] == TRADESLIDE_PORTFOLIO_ID
    assert result["company"] == TRADESLIDE
    assert result["strategy_count"] == 1
    assert result["total_trades"] == 1
    assert result["final_balance"] == 101_000
    assert result["max_strategy_equity_dd_pct"] == 11.5
    api.upsert_portfolio.assert_called_once()
    payload = api.upsert_portfolio.call_args.args[0]
    assert payload["strategyIds"] == ["strategy-a"]
    assert payload["summary"]["portfolio_id"] == TRADESLIDE_PORTFOLIO_ID
    assert payload["summary"]["validation_state"] == "unverified"
    assert payload["summary"]["source"] == "python_deal_merge_diagnostic"
    assert payload["manifest"]["strategies"][0]["result_id"] == "strategy-a"
    merge_mock.assert_called_once()
    assert merge_mock.call_args.kwargs["portfolio_id"] == TRADESLIDE_PORTFOLIO_ID
    assert merge_mock.call_args.kwargs["company"] == TRADESLIDE
    assert merge_mock.call_args.kwargs["initial_deposit"] == 100_000
    manifest_mock.assert_called_once()


def test_refresh_clears_all_when_no_favorites() -> None:
    api = MagicMock()
    api.get_favorites.return_value = []

    result = refresh_company_favorites_portfolios(api)

    assert result == []
    api.clear_portfolio.assert_called_once_with(all_portfolios=True)
    api.upsert_portfolio.assert_not_called()
    api.reconcile_portfolios.assert_not_called()


def test_refresh_company_clears_only_that_portfolio_when_empty() -> None:
    api = MagicMock()
    api.get_favorites.return_value = [
        {"id": "other", "company": "FTMO", "symbol": "EURUSD", "timeframe": "M15"},
    ]

    result = refresh_company_favorites_portfolios(api, company=TRADESLIDE)

    assert result == []
    api.clear_portfolio.assert_called_once_with(
        portfolio_id=TRADESLIDE_PORTFOLIO_ID,
    )
    api.upsert_portfolio.assert_not_called()
    api.reconcile_portfolios.assert_not_called()


@patch("mt5_portfolio_favorites.build_portfolio_manifest")
@patch("mt5_portfolio_favorites.run_combined_portfolio")
def test_refresh_prepares_all_before_upsert(
    combined_mock: MagicMock,
    manifest_mock: MagicMock,
) -> None:
    api = MagicMock()
    api.get_favorites.return_value = [
        {
            "id": "strategy-a",
            "symbol": "EURUSD",
            "timeframe": "M15",
            "company": TRADESLIDE,
            "summary": {"deposit": 100_000},
            "parameters": {},
            "equity_curve": [],
        },
        {
            "id": "strategy-b",
            "symbol": "GBPUSD",
            "timeframe": "M15",
            "company": "FTMO",
            "summary": {"deposit": 100_000},
            "parameters": {},
            "equity_curve": [],
        },
    ]
    manifest_mock.side_effect = [
        _manifest(company="FTMO"),
        _manifest(company=TRADESLIDE),
    ]
    combined_mock.side_effect = [
        _verified_result(company="FTMO"),
        _verified_result(company=TRADESLIDE),
    ]

    results = refresh_company_favorites_portfolios(api)

    assert len(results) == 2
    assert combined_mock.call_count == 2
    assert manifest_mock.call_count == 2
    assert api.upsert_portfolio.call_count == 2
    api.clear_portfolio.assert_not_called()
    api.reconcile_portfolios.assert_called_once_with(
        keep_portfolio_ids=[FTMO_PORTFOLIO_ID, TRADESLIDE_PORTFOLIO_ID],
    )
    # Both tester runs complete before the first upsert.
    assert combined_mock.call_count == 2
    first_upsert_order = next(
        i for i, c in enumerate(api.mock_calls) if c[0] == "upsert_portfolio"
    )
    assert first_upsert_order >= 0


@patch("mt5_portfolio_favorites.build_portfolio_manifest")
@patch("mt5_portfolio_favorites.run_combined_portfolio")
def test_refresh_marks_runtime_failures_unavailable_without_aborting_others(
    combined_mock: MagicMock,
    manifest_mock: MagicMock,
) -> None:
    api = MagicMock()
    api.get_favorites.return_value = [
        {
            "id": "strategy-a",
            "symbol": "EURUSD",
            "timeframe": "M15",
            "company": TRADESLIDE,
            "summary": {"deposit": 100_000},
            "parameters": {},
            "equity_curve": [],
        },
        {
            "id": "strategy-b",
            "symbol": "GBPUSD",
            "timeframe": "M15",
            "company": "FTMO",
            "summary": {"deposit": 100_000},
            "parameters": {},
            "equity_curve": [],
        },
    ]
    manifest_mock.side_effect = [
        _manifest(company="FTMO"),
        _manifest(company=TRADESLIDE),
    ]
    combined_mock.side_effect = [
        _verified_result(company="FTMO"),
        RuntimeError("MT5 portfolio tester exceeded 8192 MB memory budget"),
    ]

    results = refresh_company_favorites_portfolios(api)

    assert len(results) == 2
    unavailable = next(r for r in results if r["company"] == TRADESLIDE)
    verified = next(r for r in results if r["company"] == "FTMO")
    assert unavailable["validation_state"] == "unavailable"
    assert "8192 MB" in unavailable["validation_failure_reason"]
    assert verified.get("validation_state") is None
    assert api.upsert_portfolio.call_count == 2
    api.reconcile_portfolios.assert_called_once()


@patch("mt5_portfolio_favorites.build_portfolio_manifest")
@patch("mt5_portfolio_favorites.run_combined_portfolio")
def test_refresh_marks_input_failures_unavailable_without_blocking_others(
    combined_mock: MagicMock,
    manifest_mock: MagicMock,
) -> None:
    api = MagicMock()
    api.get_favorites.return_value = [
        {
            "id": "strategy-a",
            "symbol": "EURUSD",
            "timeframe": "M15",
            "company": TRADESLIDE,
            "summary": {"deposit": 100_000},
            "parameters": {},
            "equity_curve": [],
        },
        {
            "id": "strategy-b",
            "symbol": "GBPUSD",
            "timeframe": "M15",
            "company": "FTMO",
            "summary": {"deposit": 100_000},
            "parameters": {},
            "equity_curve": [],
        },
    ]
    manifest_mock.side_effect = [
        ValueError("missing realticks report"),
        _manifest(company=TRADESLIDE),
    ]
    combined_mock.return_value = _verified_result(company=TRADESLIDE)

    results = refresh_company_favorites_portfolios(api)

    assert len(results) == 2
    unavailable = next(r for r in results if r["company"] == "FTMO")
    verified = next(r for r in results if r["company"] == TRADESLIDE)
    assert unavailable["validation_state"] == "unavailable"
    assert "missing realticks report" in unavailable["validation_failure_reason"]
    assert verified.get("validation_state") is None  # summary from combined result
    assert api.upsert_portfolio.call_count == 2
    unavailable_payload = next(
        call.args[0]
        for call in api.upsert_portfolio.call_args_list
        if call.args[0]["summary"]["company"] == "FTMO"
    )
    assert unavailable_payload["summary"]["validation_state"] == "unavailable"
    assert combined_mock.call_count == 1
    api.reconcile_portfolios.assert_called_once()


@patch("mt5_portfolio_favorites.build_portfolio_manifest")
def test_refresh_company_upserts_unavailable_for_input_errors(
    manifest_mock: MagicMock,
) -> None:
    api = MagicMock()
    api.get_favorites.return_value = [
        {
            "id": "strategy-b",
            "symbol": "EURUSD",
            "timeframe": "M15",
            "company": "FTMO",
            "summary": {"deposit": 100_000},
            "parameters": {},
            "equity_curve": [],
        },
    ]
    manifest_mock.side_effect = ValueError("missing realticks report")

    results = refresh_company_favorites_portfolios(api, company="FTMO")

    assert len(results) == 1
    assert results[0]["validation_state"] == "unavailable"
    api.upsert_portfolio.assert_called_once()
    payload = api.upsert_portfolio.call_args.args[0]
    assert payload["summary"]["validation_state"] == "unavailable"
    assert payload["strategyIds"] == ["strategy-b"]


@patch("mt5_portfolio_favorites.PositionRelayOptimizerApi.from_env")
@patch("mt5_portfolio_favorites.assert_optimizer_access")
@patch("mt5_portfolio_favorites.refresh_company_favorites_portfolios")
@patch("mt5_portfolio_favorites.load_repo_env")
def test_main_success(
    _load_env: MagicMock,
    refresh_mock: MagicMock,
    _access: MagicMock,
    from_env: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from_env.return_value = MagicMock()
    refresh_mock.return_value = [
        {
            "portfolio_id": TRADESLIDE_PORTFOLIO_ID,
            "company": TRADESLIDE,
            "strategy_count": 1,
            "total_trades": 1,
            "final_balance": 101_000,
            "max_equity_drawdown_relative_pct": 0.0,
            "max_balance_drawdown_relative_pct": 0.0,
            "max_strategy_equity_dd_pct": 11.5,
        }
    ]

    exit_code = main([])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["portfolio_id"] == TRADESLIDE_PORTFOLIO_ID


@patch("mt5_portfolio_favorites.PositionRelayOptimizerApi.from_env")
@patch("mt5_portfolio_favorites.assert_optimizer_access")
@patch("mt5_portfolio_favorites.refresh_company_favorites_portfolios")
@patch("mt5_portfolio_favorites.load_repo_env")
def test_main_clears_portfolio_when_no_favorites(
    _load_env: MagicMock,
    refresh_mock: MagicMock,
    _access: MagicMock,
    from_env: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from_env.return_value = MagicMock()
    refresh_mock.return_value = []

    exit_code = main([])

    assert exit_code == 0
    assert "portfolio snapshot" in capsys.readouterr().out.lower()


@patch("mt5_portfolio_favorites.PositionRelayOptimizerApi.from_env")
@patch("mt5_portfolio_favorites.assert_optimizer_access")
@patch("mt5_portfolio_favorites.refresh_company_favorites_portfolios")
@patch("mt5_portfolio_favorites.load_repo_env")
def test_main_returns_error_when_refresh_raises(
    _load_env: MagicMock,
    refresh_mock: MagicMock,
    _access: MagicMock,
    from_env: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from_env.return_value = MagicMock()
    refresh_mock.side_effect = ValueError("Could not resolve initial deposit")

    exit_code = main([])

    assert exit_code == 1
    assert "Could not resolve initial deposit" in capsys.readouterr().err


@patch("mt5_portfolio_favorites.PositionRelayOptimizerApi.from_env")
@patch("mt5_portfolio_favorites.assert_optimizer_access")
@patch("mt5_portfolio_favorites.refresh_company_favorites_portfolios")
@patch("mt5_portfolio_favorites.load_repo_env")
def test_main_returns_error_when_results_unavailable(
    _load_env: MagicMock,
    refresh_mock: MagicMock,
    _access: MagicMock,
    from_env: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from_env.return_value = MagicMock()
    refresh_mock.return_value = [
        {
            "portfolio_id": TRADESLIDE_PORTFOLIO_ID,
            "company": TRADESLIDE,
            "strategy_count": 1,
            "total_trades": 0,
            "validation_state": "unavailable",
            "validation_failure_reason": "missing realticks report",
        }
    ]

    exit_code = main([])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "missing realticks report" in captured.err
    assert json.loads(captured.out)["validation_state"] == "unavailable"

@patch("mt5_portfolio_favorites.PositionRelayOptimizerApi.from_env")
@patch("mt5_portfolio_favorites.assert_optimizer_access")
@patch("mt5_portfolio_favorites.load_repo_env")
def test_main_returns_error_when_api_fails(
    _load_env: MagicMock,
    _access: MagicMock,
    from_env: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from_env.side_effect = RuntimeError("PositionRelay API unavailable")

    exit_code = main([])

    assert exit_code == 1
    assert "PositionRelay API unavailable" in capsys.readouterr().err
