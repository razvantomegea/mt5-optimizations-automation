"""Survivor ranking helpers shared by validate and Skip Robustness."""

from __future__ import annotations

from typing import Any

from mt5_opt_report import to_float, to_int

# Missing scores sort last under reverse=True ranking.
_MISSING_RANK = float("-inf")


def validation_rank_key(row: dict[str, Any]) -> tuple[float, float, int]:
    score = to_float(row.get("validation_score"))
    recovery = to_float(row.get("validation_recovery"))
    return (
        _MISSING_RANK if score is None else score,
        _MISSING_RANK if recovery is None else recovery,
        to_int(row.get("pass_id")),
    )


def is_validation_pass(row: dict[str, Any]) -> bool:
    value = row.get("validation_pass")
    return value is True or value in ("True", "true", "1", 1)


def row_is_survivor(row: dict[str, Any]) -> bool:
    keep = row.get("keep")
    return keep is True or keep in ("True", "true", "1", 1)
