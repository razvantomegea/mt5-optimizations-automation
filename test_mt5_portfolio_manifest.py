"""Tests for versioned Favorites portfolio build manifests."""

from __future__ import annotations

from pathlib import Path

import pytest

from mt5_portfolio_manifest import (
    PORTFOLIO_MANIFEST_VERSION,
    PORTFOLIO_TESTER_MODEL,
    PortfolioManifestError,
    build_portfolio_manifest,
)
from mt5_portfolio_merge import build_company_portfolio_id

COMPANY = "Tradeslide Trading Tech Limited"
PORTFOLIO_ID = build_company_portfolio_id(COMPANY)


def _row(
    *,
    result_id: str = "fav-1",
    symbol: str = "EURUSD",
    pass_id: int = 94051,
    deposit: float = 100_000,
    currency: str = "USD",
    leverage: str = "1:33",
    from_date: str = "2014.09.16",
    to_date: str = "2026.09.16",
    company: str = COMPANY,
    set_name: str | None = None,
) -> dict:
    stem = f"{symbol}_M15_Classic_pass{pass_id}"
    return {
        "id": result_id,
        "symbol": symbol,
        "timeframe": "M15",
        "profile": "Classic",
        "pass_id": pass_id,
        "report_stem": stem,
        "param_file": set_name or f"{stem}.set",
        "company": company,
        "from_date": from_date,
        "to_date": to_date,
        "summary": {
            "deposit": deposit,
            "currency": currency,
            "leverage": leverage,
            "set_file": set_name or f"{stem}.set",
        },
        "parameters": {"RISK": "10.6"},
        "report_metrics": {
            "format": "html",
            "metrics": {
                "Company": company,
                "Currency": currency,
                "Initial Deposit": f"{deposit:,.2f}".replace(",", " "),
                "Leverage": leverage,
                "Period": f"M15 ({from_date} - {to_date})",
            },
        },
    }


def test_build_manifest_for_compatible_favorites(tmp_path: Path) -> None:
    favorites = tmp_path / "Favorites"
    best = tmp_path / "Best"
    sets = favorites / "sets"
    reports = favorites / "reports" / "EURUSD"
    sets.mkdir(parents=True)
    reports.mkdir(parents=True)
    set_name = "EURUSD_M15_Classic_pass94051.set"
    (sets / set_name).write_text("RISK=10.6\n", encoding="utf-8")
    (reports / "EURUSD_M15_Classic_pass94051_realticks.htm").write_text(
        "<html></html>",
        encoding="utf-8",
    )

    row = _row(set_name=set_name)
    manifest = build_portfolio_manifest(
        [row],
        company=COMPANY,
        best_dir=best,
        favorites_dir=favorites,
        repo_root=tmp_path,
    )

    assert manifest.version == PORTFOLIO_MANIFEST_VERSION
    assert manifest.portfolio_id == PORTFOLIO_ID
    assert manifest.company == COMPANY
    assert manifest.deposit == 100_000
    assert manifest.currency == "USD"
    assert manifest.leverage == "1:33"
    assert manifest.from_date == "2014.09.16"
    assert manifest.to_date == "2026.09.16"
    assert manifest.tester_model == PORTFOLIO_TESTER_MODEL
    assert len(manifest.strategies) == 1
    assert manifest.strategies[0].set_file == set_name
    assert manifest.strategies[0].magic != 0


def test_manifest_accepts_many_same_symbol_timeframe_configurations(tmp_path: Path) -> None:
    favorites = tmp_path / "Favorites"
    sets = favorites / "sets"
    reports = favorites / "reports" / "EURUSD"
    sets.mkdir(parents=True)
    reports.mkdir(parents=True)
    rows = []
    for index in range(64):
        pass_id = 10_000 + index
        stem = f"EURUSD_M15_Classic_pass{pass_id}"
        (sets / f"{stem}.set").write_text(f"RISK={index + 1}\n", encoding="utf-8")
        (reports / f"{stem}_realticks.htm").write_text("<html></html>", encoding="utf-8")
        rows.append(_row(result_id=f"fav-{index}", pass_id=pass_id))

    manifest = build_portfolio_manifest(
        rows,
        company=COMPANY,
        best_dir=tmp_path / "Best",
        favorites_dir=favorites,
        repo_root=tmp_path,
    )

    assert len(manifest.strategies) == 64
    assert len({strategy.magic for strategy in manifest.strategies}) == 64
    assert {strategy.symbol for strategy in manifest.strategies} == {"EURUSD"}
    assert {strategy.timeframe for strategy in manifest.strategies} == {"M15"}


def test_manifest_reads_missing_window_from_required_realticks_report(tmp_path: Path) -> None:
    favorites = tmp_path / "Favorites"
    sets = favorites / "sets"
    reports = favorites / "reports" / "AUDUSD"
    sets.mkdir(parents=True)
    reports.mkdir(parents=True)
    (sets / "AUDUSD_M15_Classic_pass56629.set").write_text("RISK=10.6\n")
    (reports / "AUDUSD_M15_Classic_pass56629_realticks.htm").write_text(
        "<html><tr><td>Period:</td><td><b>"
        "M15 (2014.09.30 - 2026.09.30)</b></td></tr></html>",
        encoding="utf-8",
    )
    row = _row(symbol="AUDUSD", pass_id=56629)
    row.pop("from_date")
    row.pop("to_date")
    row["report_metrics"]["metrics"].pop("Period", None)

    manifest = build_portfolio_manifest(
        [row], company=COMPANY, best_dir=tmp_path / "Best",
        favorites_dir=favorites, repo_root=tmp_path,
    )

    assert (manifest.from_date, manifest.to_date) == ("2014.09.30", "2026.09.30")


def test_manifest_reads_missing_leverage_from_required_realticks_report(tmp_path: Path) -> None:
    favorites = tmp_path / "Favorites"
    sets = favorites / "sets"
    reports = favorites / "reports" / "EURUSD"
    sets.mkdir(parents=True)
    reports.mkdir(parents=True)
    (sets / "EURUSD_M15_Classic_pass94051.set").write_text("RISK=10.6\n")
    (reports / "EURUSD_M15_Classic_pass94051_realticks.htm").write_text(
        "<html><tr><td>Leverage:</td><td><b>1:33</b></td></tr></html>",
        encoding="utf-8",
    )
    row = _row()
    row["summary"].pop("leverage")
    row["report_metrics"]["metrics"].pop("Leverage")

    manifest = build_portfolio_manifest(
        [row], company=COMPANY, best_dir=tmp_path / "Best",
        favorites_dir=favorites, repo_root=tmp_path,
    )
    assert manifest.leverage == "1:33"


def test_reject_mismatched_deposits(tmp_path: Path) -> None:
    favorites = tmp_path / "Favorites"
    best = tmp_path / "Best"
    (favorites / "sets").mkdir(parents=True)
    for symbol, pass_id, deposit in (
        ("EURUSD", 1, 100_000),
        ("GBPUSD", 2, 50_000),
    ):
        stem = f"{symbol}_M15_Classic_pass{pass_id}"
        (favorites / "sets" / f"{stem}.set").write_text("RISK=1\n", encoding="utf-8")
        report_dir = favorites / "reports" / symbol
        report_dir.mkdir(parents=True)
        (report_dir / f"{stem}_realticks.htm").write_text("<html></html>", encoding="utf-8")

    rows = [
        _row(result_id="a", symbol="EURUSD", pass_id=1, deposit=100_000),
        _row(result_id="b", symbol="GBPUSD", pass_id=2, deposit=50_000),
    ]
    with pytest.raises(PortfolioManifestError, match="Incompatible deposits"):
        build_portfolio_manifest(
            rows,
            company=COMPANY,
            best_dir=best,
            favorites_dir=favorites,
            repo_root=tmp_path,
        )


def test_uses_union_for_mismatched_test_windows(tmp_path: Path) -> None:
    favorites = tmp_path / "Favorites"
    best = tmp_path / "Best"
    (favorites / "sets").mkdir(parents=True)
    for symbol, pass_id in (("EURUSD", 1), ("GBPUSD", 2)):
        stem = f"{symbol}_M15_Classic_pass{pass_id}"
        (favorites / "sets" / f"{stem}.set").write_text("RISK=1\n", encoding="utf-8")
        report_dir = favorites / "reports" / symbol
        report_dir.mkdir(parents=True)
        (report_dir / f"{stem}_realticks.htm").write_text("<html></html>", encoding="utf-8")

    rows = [
        _row(
            result_id="a",
            symbol="EURUSD",
            pass_id=1,
            from_date="2014.09.16",
            to_date="2026.09.16",
        ),
        _row(
            result_id="b",
            symbol="GBPUSD",
            pass_id=2,
            from_date="2015.01.01",
            to_date="2026.09.16",
        ),
    ]
    manifest = build_portfolio_manifest(
        rows, company=COMPANY, best_dir=best,
        favorites_dir=favorites, repo_root=tmp_path,
    )
    assert (manifest.from_date, manifest.to_date) == ("2014.09.16", "2026.09.16")
    assert manifest.strategies[0].source_from_date == "2014.09.16"
    assert manifest.strategies[1].source_from_date == "2015.01.01"


def test_accepts_favorites_without_an_overlapping_test_window(tmp_path: Path) -> None:
    favorites = tmp_path / "Favorites"
    sets = favorites / "sets"
    sets.mkdir(parents=True)
    for symbol, pass_id in (("EURUSD", 1), ("GBPUSD", 2)):
        stem = f"{symbol}_M15_Classic_pass{pass_id}"
        (sets / f"{stem}.set").write_text("RISK=1\n")
        reports = favorites / "reports" / symbol
        reports.mkdir(parents=True)
        (reports / f"{stem}_realticks.htm").write_text("<html></html>")
    rows = [
        _row(result_id="a", symbol="EURUSD", pass_id=1,
             from_date="2014.01.01", to_date="2015.01.01"),
        _row(result_id="b", symbol="GBPUSD", pass_id=2,
             from_date="2016.01.01", to_date="2017.01.01"),
    ]
    manifest = build_portfolio_manifest(
        rows, company=COMPANY, best_dir=tmp_path / "Best",
        favorites_dir=favorites, repo_root=tmp_path,
    )
    assert (manifest.from_date, manifest.to_date) == ("2014.01.01", "2017.01.01")
    assert [(s.source_from_date, s.source_to_date) for s in manifest.strategies] == [
        ("2014.01.01", "2015.01.01"),
        ("2016.01.01", "2017.01.01"),
    ]


def test_reject_missing_set_file(tmp_path: Path) -> None:
    favorites = tmp_path / "Favorites"
    best = tmp_path / "Best"
    report_dir = favorites / "reports" / "EURUSD"
    report_dir.mkdir(parents=True)
    (favorites / "sets").mkdir(parents=True)
    (report_dir / "EURUSD_M15_Classic_pass94051_realticks.htm").write_text(
        "<html></html>",
        encoding="utf-8",
    )

    with pytest.raises(PortfolioManifestError, match="missing set file"):
        build_portfolio_manifest(
            [_row()],
            company=COMPANY,
            best_dir=best,
            favorites_dir=favorites,
            repo_root=tmp_path,
        )


def test_reject_missing_currency(tmp_path: Path) -> None:
    favorites = tmp_path / "Favorites"
    best = tmp_path / "Best"
    (favorites / "sets").mkdir(parents=True)
    stem = "EURUSD_M15_Classic_pass94051"
    (favorites / "sets" / f"{stem}.set").write_text("RISK=1\n", encoding="utf-8")
    report_dir = favorites / "reports" / "EURUSD"
    report_dir.mkdir(parents=True)
    (report_dir / f"{stem}_realticks.htm").write_text("<html></html>", encoding="utf-8")

    row = _row()
    row["summary"].pop("currency")
    row["report_metrics"]["metrics"].pop("Currency")

    with pytest.raises(PortfolioManifestError, match="Missing currency"):
        build_portfolio_manifest(
            [row],
            company=COMPANY,
            best_dir=best,
            favorites_dir=favorites,
            repo_root=tmp_path,
        )
