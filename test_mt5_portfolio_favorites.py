"""Tests for per-company portfolio generation CLI and API persistence."""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from mt5_portfolio_favorites import (
    build_company_favorites_portfolio,
    group_favorites_by_company,
    main,
    refresh_company_favorites_portfolios,
)
from mt5_portfolio_merge import (
    MIGRATION_DEFAULT_COMPANY,
    build_company_portfolio_id,
    MergedPortfolio,
)
from portfolio_test_helpers import series, trade

TRADESLIDE = MIGRATION_DEFAULT_COMPANY
TRADESLIDE_PORTFOLIO_ID = build_company_portfolio_id(TRADESLIDE)
FTMO_PORTFOLIO_ID = build_company_portfolio_id("FTMO")


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


@patch("mt5_portfolio_favorites.merge_strategy_series")
@patch("mt5_portfolio_favorites.load_strategy_series")
def test_build_company_favorites_portfolio_persists_snapshot(
    load_mock: MagicMock,
    merge_mock: MagicMock,
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
    merge_mock.assert_called_once()
    assert merge_mock.call_args.kwargs["portfolio_id"] == TRADESLIDE_PORTFOLIO_ID
    assert merge_mock.call_args.kwargs["company"] == TRADESLIDE


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


@patch("mt5_portfolio_favorites.merge_strategy_series")
@patch("mt5_portfolio_favorites.load_strategy_series")
def test_refresh_prepares_all_before_upsert(
    load_mock: MagicMock,
    merge_mock: MagicMock,
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
    load_mock.side_effect = [
        series(
            trades=(
                trade(
                    time=datetime(2020, 1, 2),
                    profit=1_000,
                    equity_before=100_000,
                ),
            ),
            result_id="strategy-a",
        ),
        series(
            trades=(
                trade(
                    time=datetime(2020, 1, 2),
                    profit=500,
                    equity_before=100_000,
                ),
            ),
            result_id="strategy-b",
        ),
    ]
    merge_mock.side_effect = [
        _merged_portfolio(company="FTMO"),
        _merged_portfolio(company=TRADESLIDE),
    ]

    results = refresh_company_favorites_portfolios(api)

    assert len(results) == 2
    assert load_mock.call_count == 2
    assert merge_mock.call_count == 2
    assert api.upsert_portfolio.call_count == 2
    api.clear_portfolio.assert_not_called()
    api.reconcile_portfolios.assert_called_once_with(
        keep_portfolio_ids=[FTMO_PORTFOLIO_ID, TRADESLIDE_PORTFOLIO_ID],
    )
    # All loads/merges complete before first upsert (prepare-then-write).
    assert load_mock.call_count == 2
    first_upsert_order = next(
        i for i, c in enumerate(api.mock_calls) if c[0] == "upsert_portfolio"
    )
    assert first_upsert_order >= 0


@patch("mt5_portfolio_favorites.merge_strategy_series")
@patch("mt5_portfolio_favorites.load_strategy_series")
def test_refresh_aborts_before_upsert_when_prepare_fails(
    load_mock: MagicMock,
    merge_mock: MagicMock,
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
    load_mock.side_effect = [
        series(
            trades=(
                trade(
                    time=datetime(2020, 1, 2),
                    profit=1_000,
                    equity_before=100_000,
                ),
            ),
            result_id="strategy-a",
        ),
        RuntimeError("report missing"),
    ]
    merge_mock.return_value = _merged_portfolio(company="FTMO")

    with pytest.raises(RuntimeError, match="report missing"):
        refresh_company_favorites_portfolios(api)

    api.upsert_portfolio.assert_not_called()
    api.reconcile_portfolios.assert_not_called()
    api.clear_portfolio.assert_not_called()


@patch("mt5_portfolio_favorites.TradeEchoOptimizerApi.from_env")
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


@patch("mt5_portfolio_favorites.TradeEchoOptimizerApi.from_env")
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


@patch("mt5_portfolio_favorites.TradeEchoOptimizerApi.from_env")
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


@patch("mt5_portfolio_favorites.TradeEchoOptimizerApi.from_env")
@patch("mt5_portfolio_favorites.assert_optimizer_access")
@patch("mt5_portfolio_favorites.load_repo_env")
def test_main_returns_error_when_api_fails(
    _load_env: MagicMock,
    _access: MagicMock,
    from_env: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from_env.side_effect = RuntimeError("TradeEcho API unavailable")

    exit_code = main([])

    assert exit_code == 1
    assert "TradeEcho API unavailable" in capsys.readouterr().err
