"""Tests for the Favorites portfolio MT5 feasibility gate helpers."""

from __future__ import annotations

from pathlib import Path

from mt5_portfolio_feasibility import (
    compare_solo_report_parity,
    evaluate_probe_export,
)


def test_evaluate_probe_export_requires_trades_and_floating_drawdown() -> None:
    assert evaluate_probe_export(
        {
            "bar_count_a": 10,
            "bar_count_b": 12,
            "trade_count_a": 1,
            "trade_count_b": 1,
            "close_count_a": 1,
            "close_count_b": 1,
            "samples_truncated": False,
            "equity_samples": [
                {"balance": 100_000, "equity": 100_000},
                {"balance": 100_000, "equity": 99_500},
            ],
        }
    ) == []
    no_trade_issues = evaluate_probe_export(
        {"bar_count_a": 10, "bar_count_b": 12, "equity_samples": [{}, {}]}
    )
    assert "no executed trades" in " ".join(no_trade_issues)
    assert "no closed trades" in " ".join(no_trade_issues)
    assert "floating equity drawdown" in " ".join(no_trade_issues)
    assert "symbol B" in evaluate_probe_export(
        {"bar_count_a": 10, "bar_count_b": 0, "equity_samples": [{}, {}]}
    )[0]
    assert "equity samples" in evaluate_probe_export(
        {"bar_count_a": 1, "bar_count_b": 1, "equity_samples": []}
    )[0]


def test_compare_solo_report_parity_reads_checked_in_eurusd() -> None:
    report = (
        Path(__file__).resolve().parents[2]
        / "EAs"
        / "Favorites"
        / "reports"
        / "EURUSD"
        / "EURUSD_M15_Classic_pass94051_realticks.htm"
    )
    if not report.is_file():
        report = (
            Path(__file__).resolve().parent
            / "reports"
            / "Favorites"
            / "reports"
            / "EURUSD"
            / "EURUSD_M15_Classic_pass94051_realticks.htm"
        )
    assert report.is_file(), f"missing golden EURUSD report at {report}"
    issues = compare_solo_report_parity(report_path=report)
    assert issues == [], issues
