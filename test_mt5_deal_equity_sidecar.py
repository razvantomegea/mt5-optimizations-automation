from pathlib import Path

from mt5_deal_equity_sidecar import (
    copy_deal_equity_sidecar_beside_report,
    is_matching_realticks_artifact,
    is_matching_strategy_artifact,
    load_deal_equity_sidecar,
    resolve_deal_equity_sidecar_path,
)


def test_is_matching_strategy_artifact_accepts_html_and_sidecar() -> None:
    stem = "EURUSD_M15_Classic_pass1"
    assert is_matching_strategy_artifact(f"{stem}_realticks.htm", stem)
    assert is_matching_strategy_artifact(f"{stem}_ohlc.htm", stem)
    assert is_matching_strategy_artifact(f"{stem}_realticks_deals.json", stem)
    assert not is_matching_strategy_artifact(f"{stem}_extra_realticks_deals.json", stem)
    assert not is_matching_strategy_artifact("GBPUSD_M15_Classic_pass1_realticks.htm", stem)
    assert not is_matching_strategy_artifact(f"{stem}2_realticks.htm", stem)


def test_is_matching_realticks_artifact_rejects_ohlc_html() -> None:
    stem = "EURUSD_M15_Classic_pass1"
    assert is_matching_realticks_artifact(f"{stem}_realticks.htm", stem)
    assert is_matching_realticks_artifact(f"{stem}_realticks_deals.json", stem)
    assert not is_matching_realticks_artifact(f"{stem}_extra_realticks_deals.json", stem)
    assert not is_matching_realticks_artifact(f"{stem}_ohlc.htm", stem)
    assert not is_matching_realticks_artifact("notes.txt", stem)
    assert not is_matching_realticks_artifact(f"{stem}2_realticks.htm", stem)


def _realticks_report(tmp_path: Path) -> Path:
    report_path = tmp_path / "EURUSD_M15_Classic_pass1_realticks.htm"
    report_path.write_text("<html></html>", encoding="utf-8")
    return report_path


def test_load_deal_equity_sidecar_skips_non_finite_equity(tmp_path: Path) -> None:
    report_path = _realticks_report(tmp_path)
    sidecar = resolve_deal_equity_sidecar_path(report_path)
    sidecar.write_text(
        '[{"time":"2020.01.01 00:00:00","equity":100.0},'
        '{"time":"2020.01.02 00:00:00","equity":"inf"},'
        '{"time":"2020.01.03 00:00:00","equity":101.0}]',
        encoding="utf-8",
    )

    points = load_deal_equity_sidecar(report_path)

    assert [equity for _time, equity in points] == [100.0, 101.0]


def test_copy_explicit_missing_source_removes_dest_sidecar(tmp_path: Path) -> None:
    report_path = _realticks_report(tmp_path)
    dest = resolve_deal_equity_sidecar_path(report_path)
    dest.write_text('[{"time":"2019.01.01 00:00:00","equity":1.0}]', encoding="utf-8")
    missing = tmp_path / "missing-export.json"

    assert copy_deal_equity_sidecar_beside_report(report_path, source=missing) is None
    assert not dest.is_file()


def test_copy_source_none_keeps_existing_dest_sidecar(tmp_path: Path) -> None:
    report_path = _realticks_report(tmp_path)
    dest = resolve_deal_equity_sidecar_path(report_path)
    dest.write_text('[{"time":"2019.01.01 00:00:00","equity":1.0}]', encoding="utf-8")

    result = copy_deal_equity_sidecar_beside_report(report_path, source=None)

    assert result == dest
    assert dest.is_file()

