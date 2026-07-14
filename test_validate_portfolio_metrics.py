"""Tests for portfolio validation against MT5 report ground truth."""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from portfolio_test_helpers import deal, sample_deals_report_html, series, trade
from validate_portfolio_metrics import _metric_value, _report_balance_dd, main, validate_favorites


def test_metric_value_parses_total_trades_and_net_profit() -> None:
    report_metrics = {
        "metrics": {
            "Total trades": "861",
            "Total net profit": "211 660.05 (211.66%)",
        }
    }

    assert _metric_value(report_metrics, "Total trades", "Trades") == 861
    assert _metric_value(report_metrics, "Total net profit", "Net Profit") == pytest.approx(
        211_660.05
    )


def test_metric_value_returns_none_for_missing_metrics() -> None:
    assert _metric_value(None, "Total trades") is None
    assert _metric_value({"metrics": {}}, "Total trades") is None


def test_report_balance_dd_parses_html(tmp_path) -> None:
    report_path = tmp_path / "sample.htm"
    report_path.write_text(
        "Balance Drawdown Relative:</td><td align=right><b>2.74%</b>",
        encoding="utf-8",
    )

    assert _report_balance_dd(report_path) == pytest.approx(2.74)


def _favorite_row(
    *,
    row_id: str = "strategy-a",
    report_metrics: dict | None = None,
    report_stem: str | None = None,
) -> dict:
    return {
        "id": row_id,
        "symbol": "EURUSD",
        "timeframe": "M15",
        "profile": "Classic",
        "pass_id": 1,
        "report_stem": report_stem,
        "summary": {"deposit": 100_000, "realticks_equity_dd_pct": 11.5},
        "parameters": {},
        "equity_curve": [],
        "report_metrics": report_metrics
        or {"metrics": {"Total trades": "1", "Total net profit": "1000.00"}},
    }


def _favorite_db_row(**kwargs) -> tuple:
    row = _favorite_row(**kwargs)
    return (
        row["id"],
        row["symbol"],
        row["timeframe"],
        row["profile"],
        row["pass_id"],
        row["report_stem"],
        json.dumps(row["summary"]),
        json.dumps(row["parameters"]),
        json.dumps(row["equity_curve"]),
        json.dumps(row["report_metrics"]),
    )


@patch("validate_portfolio_metrics.load_strategy_series")
def test_validate_favorites_reports_no_issues_for_matching_strategy(
    load_mock: MagicMock,
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report_path = tmp_path / "EURUSD_M15_Classic_pass1_realticks.htm"
    report_path.write_text(
        sample_deals_report_html(
            '<tr><td>2020.01.01 00:00:00</td>' + "<td></td>" * 11 + "<td>100500</td></tr>"
        )
        + "\nBalance Drawdown Relative:</td><td align=right><b>0.00%</b>",
        encoding="utf-8",
    )

    api = MagicMock()
    api.get_favorites.return_value = [_favorite_row()]

    strategy = series(
        trades=(
            trade(
                time=datetime(2020, 1, 2),
                profit=1_000,
                equity_before=100_000,
            ),
        ),
        result_id="strategy-a",
        realticks_equity_dd_pct=11.5,
    )
    load_mock.return_value = strategy

    with patch(
        "validate_portfolio_metrics.resolve_strategy_report_path",
        return_value=report_path,
    ):
        exit_code = validate_favorites(api)

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["issues"] == []
    assert payload["total_trades"] == 1


@patch("validate_portfolio_metrics.load_strategy_series")
def test_validate_favorites_flags_trade_count_mismatch(
    load_mock: MagicMock,
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report_path = tmp_path / "report.htm"
    report_path.write_text("Balance Drawdown Relative:</td><td><b>2.00%</b>", encoding="utf-8")

    api = MagicMock()
    api.get_favorites.return_value = [
        _favorite_row(report_metrics={"metrics": {"Total trades": "2"}})
    ]

    load_mock.return_value = series(
        trades=(
            trade(
                time=datetime(2020, 1, 2),
                profit=1_000,
                equity_before=100_000,
            ),
        ),
    )

    with patch(
        "validate_portfolio_metrics.resolve_strategy_report_path",
        return_value=report_path,
    ):
        exit_code = validate_favorites(api)

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert any("trade_count" in issue for issue in payload["issues"])


@patch("validate_portfolio_metrics.load_strategy_series")
def test_validate_favorites_warns_when_report_missing(
    load_mock: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    api = MagicMock()
    api.get_favorites.return_value = [_favorite_row()]

    load_mock.return_value = series(
        trades=(
            trade(
                time=datetime(2020, 1, 2),
                profit=1_000,
                equity_before=100_000,
            ),
        ),
    )

    with patch("validate_portfolio_metrics.resolve_strategy_report_path", return_value=None):
        exit_code = validate_favorites(api)

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["warnings"] == ["EURUSD M15 pass 1: no HTML report (equity-curve fallback)"]


@patch("validate_portfolio_metrics.load_strategy_series")
def test_validate_favorites_flags_exit_before_entry(
    load_mock: MagicMock,
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report_path = tmp_path / "report.htm"
    report_path.write_text("Balance Drawdown Relative:</td><td><b>2.00%</b>", encoding="utf-8")

    api = MagicMock()
    api.get_favorites.return_value = [_favorite_row()]

    entry_time = datetime(2020, 1, 2)
    exit_time = datetime(2020, 1, 1)
    load_mock.return_value = series(
        trades=(
            trade(
                time=exit_time,
                profit=500,
                equity_before=100_000,
            ),
        ),
        deals=(
            deal(
                time=exit_time,
                balance_delta=500,
                equity_before=100_000,
                direction="out",
                is_closed_trade=True,
            ),
            deal(
                time=entry_time,
                balance_delta=-10,
                equity_before=100_000,
                direction="in",
                is_closed_trade=False,
            ),
        ),
    )

    with patch(
        "validate_portfolio_metrics.resolve_strategy_report_path",
        return_value=report_path,
    ):
        exit_code = validate_favorites(api)

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert any("first exit occurs before first entry" in issue for issue in payload["issues"])


@patch("validate_portfolio_metrics.load_strategy_series")
def test_validate_favorites_flags_solo_net_profit_mismatch(
    load_mock: MagicMock,
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report_path = tmp_path / "report.htm"
    report_path.write_text("Balance Drawdown Relative:</td><td><b>0.00%</b>", encoding="utf-8")

    api = MagicMock()
    api.get_favorites.return_value = [
        _favorite_row(
            report_metrics={"metrics": {"Total trades": "1", "Total net profit": "5000.00"}},
        )
    ]

    load_mock.return_value = series(
        trades=(
            trade(
                time=datetime(2020, 1, 2),
                profit=1_000,
                equity_before=100_000,
            ),
        ),
    )

    with patch(
        "validate_portfolio_metrics.resolve_strategy_report_path",
        return_value=report_path,
    ):
        exit_code = validate_favorites(api)

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert any("solo net" in issue for issue in payload["issues"])


@patch("validate_portfolio_metrics.load_strategy_series")
def test_validate_favorites_flags_balance_dd_mismatch(
    load_mock: MagicMock,
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report_path = tmp_path / "report.htm"
    report_path.write_text(
        "Balance Drawdown Relative:</td><td align=right><b>12.00%</b>",
        encoding="utf-8",
    )

    api = MagicMock()
    api.get_favorites.return_value = [_favorite_row()]

    win_time = datetime(2020, 4, 1)
    loss_time = datetime(2020, 4, 2)
    load_mock.return_value = series(
        trades=(
            trade(time=win_time, profit=5_000, equity_before=100_000),
            trade(time=loss_time, profit=-2_000, equity_before=105_000),
        ),
    )

    with patch(
        "validate_portfolio_metrics.resolve_strategy_report_path",
        return_value=report_path,
    ):
        exit_code = validate_favorites(api)

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert any("solo balance DD" in issue for issue in payload["issues"])


@patch("validate_portfolio_metrics.TradeEchoOptimizerApi.from_env")
@patch("validate_portfolio_metrics.assert_optimizer_access")
@patch("validate_portfolio_metrics.load_repo_env")
def test_main_returns_error_when_api_unavailable(
    _load_env: MagicMock,
    _access: MagicMock,
    from_env: MagicMock,
) -> None:
    from_env.side_effect = RuntimeError("TradeEcho API unavailable")
    assert main([]) == 1
