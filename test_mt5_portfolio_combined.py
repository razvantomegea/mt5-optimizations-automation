"""Contract tests for certifying MT5 combined report and equity export."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mt5_broker_identity import BrokerMismatchError
from mt5_portfolio_combined import parse_combined_portfolio_result
from mt5_portfolio_manifest import ManifestStrategy, PortfolioManifest


def _manifest(*, company: str = "FTMO") -> PortfolioManifest:
    return PortfolioManifest(
        version=1, portfolio_id="favorites:ftmo", company=company, server="FTMO",
        from_date="2026.09.14", to_date="2026.09.16", deposit=100000,
        currency="USD", leverage="1:33", tester_model=4,
        strategies=tuple(
            ManifestStrategy(
                result_id=f"strategy-{index}", symbol=symbol, timeframe="M15",
                profile="Classic", pass_id=index, set_file=f"{symbol}.set",
                report_stem=symbol, magic=4991701300 + index, risk_pct=1,
                parameters={},
            )
            for index, symbol in enumerate(("EURUSD", "GBPUSD"))
        ),
    )


def _write_artifacts(
    tmp_path: Path,
    *,
    company: str = "FTMO",
    **export_overrides: object,
) -> tuple[Path, Path]:
    metrics = {
        "Company": company,
        "Initial Deposit": "100 000.00",
        "Total Net Profit": "143.92",
        "Total Trades": "2",
        "Balance Drawdown Relative": "0.01% (6.64)",
        "Equity Drawdown Relative": "0.12% (122.64)",
        "Profit Factor": "23.47",
        "Sharpe Ratio": "0.84",
    }
    report = tmp_path / "combined.htm"
    report.write_text(
        "<html>" + "".join(
            f"<tr><td>{label}:</td><td><b>{value}</b></td></tr>"
            for label, value in metrics.items()
        ) + "</html>", encoding="utf-8",
    )
    export = {
        "source": "mt5_combined_tester",
        "missed_bars": False,
        "window_close_failed": False,
        "initial_deposit": 100000,
        "net_profit": 143.92,
        "trades": 2,
        "final_balance": 100143.92,
        "final_equity": 100143.92,
        "balance_dd_pct": 0.00664,
        "equity_dd_pct": 0.12255735,
        "strategy_trade_counts": [1, 1],
        "equity_curve": [
            {"time": "2026.09.14 00:00:00", "balance": 100000, "equity": 100000},
            {"time": "2026.09.15 23:59:01", "balance": 100143.92, "equity": 100143.92},
        ],
    }
    export.update(export_overrides)
    sidecar = tmp_path / "combined.json"
    sidecar.write_text(json.dumps(export), encoding="utf-8")
    return report, sidecar


def test_verified_payload_uses_combined_tester_metrics(tmp_path: Path) -> None:
    report, sidecar = _write_artifacts(tmp_path)
    result = parse_combined_portfolio_result(
        manifest=_manifest(), report_path=report, export_path=sidecar,
    )
    assert result.payload["summary"]["validation_state"] == "verified"
    assert result.payload["summary"]["source"] == "mt5_combined_tester"
    assert len(result.payload["summary"]["canonical_ea_revision"]) == 64
    assert result.payload["summary"]["max_equity_drawdown_relative_pct"] == 0.12255735
    assert result.payload["reportMetrics"]["metrics"]["Profit Factor"] == "23.47"
    assert [row["trade_count"] for row in result.payload["summary"]["strategies"]] == [1, 1]
    assert result.payload["summary"]["missed_bars"] is False


def test_accepts_missed_bars_when_report_and_export_agree(tmp_path: Path) -> None:
    """Multi-symbol chart driving can set missed_bars without invalidating metrics."""
    report, sidecar = _write_artifacts(tmp_path, missed_bars=True)
    result = parse_combined_portfolio_result(
        manifest=_manifest(), report_path=report, export_path=sidecar,
    )
    assert result.payload["summary"]["validation_state"] == "verified"
    assert result.payload["summary"]["missed_bars"] is True
    assert result.payload["summary"]["total_trades"] == 2


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"window_close_failed": True}, "source-window end"),
        ({"strategy_trade_counts": [2, 1]}, "per-strategy"),
        ({"equity_dd_pct": 5.0}, "equity_dd_pct"),
    ],
)
def test_rejects_unverified_or_inconsistent_tester_export(
    tmp_path: Path, override: dict[str, object], message: str,
) -> None:
    report, sidecar = _write_artifacts(tmp_path, **override)
    with pytest.raises(ValueError, match=message):
        parse_combined_portfolio_result(
            manifest=_manifest(), report_path=report, export_path=sidecar,
        )


def test_rejects_report_company_mismatch(tmp_path: Path) -> None:
    report, sidecar = _write_artifacts(
        tmp_path, company="Pepperstone EU Limited",
    )
    with pytest.raises(BrokerMismatchError) as exc_info:
        parse_combined_portfolio_result(
            manifest=_manifest(company="Tradeslide Trading Tech Limited"),
            report_path=report,
            export_path=sidecar,
        )
    assert exc_info.value.expected_company == "Tradeslide Trading Tech Limited"
    assert exc_info.value.terminal_company == "Pepperstone EU Limited"


def test_accepts_matching_report_company_case_insensitive(tmp_path: Path) -> None:
    report, sidecar = _write_artifacts(tmp_path, company="ftmo")
    result = parse_combined_portfolio_result(
        manifest=_manifest(company="FTMO"),
        report_path=report,
        export_path=sidecar,
    )
    assert result.payload["summary"]["validation_state"] == "verified"
    assert result.payload["summary"]["company"] == "FTMO"
