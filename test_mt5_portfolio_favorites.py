"""Tests for portfolio generation CLI and API persistence."""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from mt5_portfolio_favorites import (
    build_all_favorites_portfolio,
    main,
    refresh_all_favorites_portfolio,
)
from mt5_portfolio_merge import ALL_FAVORITES_PORTFOLIO_ID, MergedPortfolio
from portfolio_test_helpers import series, trade

TEST_USER_ID = "00000000-0000-4000-8000-000000000099"


def _merged_portfolio() -> MergedPortfolio:
    return MergedPortfolio(
        strategy_ids=["strategy-a"],
        equity_curve=[
            {"time": "2020-01-01T00:00:00", "balance": 100_000, "equity": 100_000},
            {"time": "2020-01-02T00:00:00", "balance": 101_000, "equity": 101_000},
        ],
        total_trades=1,
        report_metrics={"format": "html", "metrics": {"Total trades": "1"}},
        summary={
            "portfolio_id": ALL_FAVORITES_PORTFOLIO_ID,
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


@patch("mt5_portfolio_favorites.merge_strategy_series")
@patch("mt5_portfolio_favorites.load_strategy_series")
def test_build_all_favorites_portfolio_persists_snapshot(
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
        "summary": {"deposit": 100_000},
        "parameters": {},
        "equity_curve": [],
    }
    api = MagicMock()
    api.get_favorites.return_value = [favorite_row]

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

    result = build_all_favorites_portfolio(api)

    assert result["portfolio_id"] == ALL_FAVORITES_PORTFOLIO_ID
    assert result["strategy_count"] == 1
    assert result["total_trades"] == 1
    assert result["final_balance"] == 101_000
    assert result["max_strategy_equity_dd_pct"] == 11.5
    api.upsert_portfolio.assert_called_once()
    payload = api.upsert_portfolio.call_args.args[0]
    assert payload["strategyIds"] == ["strategy-a"]
    assert payload["summary"]["total_trades"] == 1


def test_build_all_favorites_portfolio_raises_when_no_favorites() -> None:
    api = MagicMock()
    api.get_favorites.return_value = []

    with pytest.raises(ValueError, match="No favorite strategies found"):
        build_all_favorites_portfolio(api)


def test_refresh_all_favorites_portfolio_clears_when_no_favorites() -> None:
    api = MagicMock()
    api.get_favorites.return_value = []

    result = refresh_all_favorites_portfolio(api)

    assert result is None
    api.clear_portfolio.assert_called_once()
    api.upsert_portfolio.assert_not_called()


@patch("mt5_portfolio_favorites.TradeEchoOptimizerApi.from_env")
@patch("mt5_portfolio_favorites.assert_optimizer_access")
@patch("mt5_portfolio_favorites.refresh_all_favorites_portfolio")
@patch("mt5_portfolio_favorites.load_repo_env")
def test_main_success(
    _load_env: MagicMock,
    refresh_mock: MagicMock,
    _access: MagicMock,
    from_env: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from_env.return_value = MagicMock()
    refresh_mock.return_value = {
        "portfolio_id": ALL_FAVORITES_PORTFOLIO_ID,
        "strategy_count": 1,
        "total_trades": 1,
        "final_balance": 101_000,
        "max_equity_drawdown_relative_pct": 0.0,
        "max_balance_drawdown_relative_pct": 0.0,
        "max_strategy_equity_dd_pct": 11.5,
    }

    exit_code = main([])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["portfolio_id"] == ALL_FAVORITES_PORTFOLIO_ID


@patch("mt5_portfolio_favorites.TradeEchoOptimizerApi.from_env")
@patch("mt5_portfolio_favorites.assert_optimizer_access")
@patch("mt5_portfolio_favorites.refresh_all_favorites_portfolio")
@patch("mt5_portfolio_favorites.load_repo_env")
def test_main_clears_portfolio_when_no_favorites(
    _load_env: MagicMock,
    refresh_mock: MagicMock,
    _access: MagicMock,
    from_env: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from_env.return_value = MagicMock()
    refresh_mock.return_value = None

    exit_code = main([])

    assert exit_code == 0
    assert "portfolio snapshot cleared" in capsys.readouterr().out.lower()


@patch("mt5_portfolio_favorites.TradeEchoOptimizerApi.from_env")
@patch("mt5_portfolio_favorites.assert_optimizer_access")
@patch("mt5_portfolio_favorites.refresh_all_favorites_portfolio")
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
