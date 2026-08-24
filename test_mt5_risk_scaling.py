"""Unit tests for linear RISK scaling (no MT5 required)."""

from __future__ import annotations

from mt5_batch_optimize import (
    DEFAULT_MIN_SCALED_RISK,
    compute_scaled_risk,
    finalize_scaled_risk,
)


def test_compute_scaled_risk_scales_down_to_half() -> None:
    """RISK 1 at 10% DD with target 5% → RISK 0.5."""
    assert (
        compute_scaled_risk(
            baseline_risk=1.0,
            baseline_dd_pct=10.0,
            target_dd_pct=5.0,
            risk_round_decimals=1,
        )
        == 0.5
    )


def test_compute_scaled_risk_returns_none_when_baseline_dd_nonpositive() -> None:
    assert (
        compute_scaled_risk(
            baseline_risk=1.0,
            baseline_dd_pct=0.0,
            target_dd_pct=5.0,
            risk_round_decimals=1,
        )
        is None
    )
    assert (
        compute_scaled_risk(
            baseline_risk=1.0,
            baseline_dd_pct=-1.0,
            target_dd_pct=5.0,
            risk_round_decimals=1,
        )
        is None
    )


def test_finalize_scaled_risk_keeps_half_when_above_floor() -> None:
    """Scale-down below 1 is allowed; 0.5 is not rejected when above the floor."""
    assert finalize_scaled_risk(0.5, min_scaled_risk=0.1) == 0.5


def test_finalize_scaled_risk_clamps_to_configured_floor() -> None:
    """A higher floor still clamps (does not reject)."""
    assert finalize_scaled_risk(0.5, min_scaled_risk=1.0) == 1.0


def test_finalize_scaled_risk_clamps_below_floor_to_min() -> None:
    """Rounding to 0.0 would disable risk sizing; clamp to floor and continue."""
    # 0.04 rounded to 1 decimal is 0.0; clamp to min 0.1
    rounded = round(0.04, 1)
    assert rounded == 0.0
    assert finalize_scaled_risk(rounded, min_scaled_risk=0.1) == 0.1
    assert finalize_scaled_risk(0.04, min_scaled_risk=0.1) == 0.1


def test_finalize_scaled_risk_returns_none_for_invalid_inputs() -> None:
    assert finalize_scaled_risk(None, min_scaled_risk=0.1) is None
    assert finalize_scaled_risk(-0.5, min_scaled_risk=0.1) is None


def test_default_min_scaled_risk_is_technical_floor() -> None:
    assert DEFAULT_MIN_SCALED_RISK == 0.1
