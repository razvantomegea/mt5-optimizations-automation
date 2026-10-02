from pathlib import Path
from dataclasses import replace

import pytest

from mt5_portfolio_ea_generator import canonical_ea_revision, generate_portfolio_ea
from mt5_portfolio_manifest import ManifestStrategy, PortfolioManifest


def _manifest(count: int) -> PortfolioManifest:
    strategies = tuple(
        ManifestStrategy(
            result_id=f"result-{index}",
            symbol="EURUSD",
            timeframe="M15",
            profile="Classic",
            pass_id=94051,
            set_file="EURUSD_M15_Classic_pass94051.set",
            report_stem="EURUSD_M15_Classic_pass94051",
            magic=4_991_701_300 + index,
            risk_pct=10.6,
            parameters={},
        )
        for index in range(count)
    )
    return PortfolioManifest(
        version=1,
        portfolio_id="favorites:ftmo",
        company="FTMO",
        server="FTMO-Server",
        from_date="2026.09.15",
        to_date="2026.09.16",
        deposit=100000,
        currency="USD",
        leverage="1:33",
        tester_model=4,
        strategies=strategies,
    )


def _sets_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "EAs" / "Favorites" / "sets"


def test_generated_ea_keeps_same_symbol_instances_separate() -> None:
    source = generate_portfolio_ea(_manifest(64), sets_dir=_sets_dir())
    assert "const int PORTFOLIO_STRATEGY_COUNT = 64;" in source
    assert "case 63:" in source
    assert "EXPERT_MAGIC = 4991701363;" in source
    assert (
        "ArrayResize(gPortfolioContexts[index].longGroups, ArraySize(longGroups));"
        in source
    )
    assert (
        "if(ArraySize(longGroups) > 0) "
        "ArrayCopy(gPortfolioContexts[index].longGroups, longGroups);"
        in source
    )
    assert (
        "ArrayResize(longGroups, ArraySize(gPortfolioContexts[index].longGroups));"
        in source
    )
    assert (
        "if(ArraySize(gPortfolioContexts[index].longGroups) > 0) "
        "ArrayCopy(longGroups, gPortfolioContexts[index].longGroups);"
        in source
    )
    assert "void OnTick() { RunPortfolioStrategies(); }" in source
    assert "gPortfolioMissedBars = true;" in source
    assert "EventSetTimer" not in source


def test_canonical_ea_revision_matches_checked_in_identity() -> None:
    import json

    identity = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "lib"
            / "optimizations"
            / "canonical-ea-revision.json"
        ).read_text(encoding="utf-8")
    )
    assert canonical_ea_revision() == identity["revision"]
    assert len(identity["revision"]) == 64


def test_generated_ea_rejects_unrecognised_set_parameter(tmp_path: Path) -> None:
    (tmp_path / "EURUSD_M15_Classic_pass94051.set").write_text("FAKE_INPUT=1\n")
    with pytest.raises(ValueError, match="unknown inputs"):
        generate_portfolio_ea(_manifest(1), sets_dir=tmp_path)


def test_generated_ea_gates_disjoint_source_windows() -> None:
    base = _manifest(2)
    manifest = replace(
        base,
        from_date="2026.09.14",
        to_date="2026.09.18",
        strategies=(
            replace(base.strategies[0], source_from_date="2026.09.14", source_to_date="2026.09.16"),
            replace(base.strategies[1], source_from_date="2026.09.16", source_to_date="2026.09.18"),
        ),
    )
    source = generate_portfolio_ea(manifest, sets_dir=_sets_dir())
    assert 'gPortfolioStartTimes[index] = StringToTime("2026.09.14 00:00:00");' in source
    assert 'gPortfolioEndTimes[index] = StringToTime("2026.09.18 00:00:00");' in source
    assert "if(TimeCurrent() < gPortfolioStartTimes[i] || gPortfolioFinished[i]) continue;" in source
    assert "if(TimeCurrent() >= gPortfolioEndTimes[i])" in source
    assert "if(bar == 0 || bar == gPortfolioContexts[i].lastBarTime) continue;" in source
    assert "CloseAllEATrades();" in source
