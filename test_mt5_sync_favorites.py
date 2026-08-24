from pathlib import Path

from mt5_sync_favorites import _has_realticks_report


def test_has_realticks_report_ignores_deal_equity_sidecar(tmp_path: Path) -> None:
    stem = "EURUSD_M15_Classic_pass1"
    sidecar = tmp_path / f"{stem}_realticks_deals.json"
    sidecar.write_text("[]", encoding="utf-8")

    assert not _has_realticks_report(
        favorites_dir=tmp_path,
        symbol="EURUSD",
        stem=stem,
        copied=[sidecar],
    )


def test_has_realticks_report_accepts_copied_html(tmp_path: Path) -> None:
    stem = "EURUSD_M15_Classic_pass1"
    report = tmp_path / f"{stem}_realticks.htm"
    report.write_text("<html></html>", encoding="utf-8")

    assert _has_realticks_report(
        favorites_dir=tmp_path,
        symbol="EURUSD",
        stem=stem,
        copied=[report],
    )
