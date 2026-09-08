"""EA input-name knobs (defaults match PositionRelay TrendReversal family).

Override via env for other EAs without forking the pipeline:
  MT5_RISK_INPUT, MT5_SKIP_DAY_INPUT, MT5_SKIP_MONTH_INPUT,
  MT5_SKIP_DAY_GRID, MT5_SKIP_MONTH_GRID

Call ``mt5_env.load_repo_env()`` before importing this module so ``.env``
overrides apply to these module-level defaults.
"""

from __future__ import annotations

import os


def _env_or(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value if value else default


RISK_INPUT_NAME = _env_or("MT5_RISK_INPUT", "RISK")
SKIP_TRADE_DAY_INPUT = _env_or("MT5_SKIP_DAY_INPUT", "SKIP_TRADE_DAY")
SKIP_MONTH_INPUT = _env_or("MT5_SKIP_MONTH_INPUT", "SKIP_MONTH")
# MT5 optimize grid: current||start||step||stop||Y
SKIP_TRADE_DAY_GRID = _env_or("MT5_SKIP_DAY_GRID", "0||1||1||5||Y")
SKIP_MONTH_GRID = _env_or("MT5_SKIP_MONTH_GRID", "0||1||1||12||Y")


def optimization_grid_step_count(grid: str) -> int:
    """Count discrete optimize steps for ``value||start||step||stop||Y`` grids."""
    parts = [p.strip() for p in grid.split("||")]
    if len(parts) < 5 or parts[4].upper() != "Y":
        return 1
    try:
        start = float(parts[1])
        step = float(parts[2])
        stop = float(parts[3])
    except ValueError:
        return 1
    if step == 0:
        return 1
    # Floor division: exclude partial final intervals (0..3 step 2 → 2 values).
    return int((stop - start) // step) + 1


def expected_skip_combinations(
    *,
    day_grid: str = SKIP_TRADE_DAY_GRID,
    month_grid: str = SKIP_MONTH_GRID,
) -> int:
    return optimization_grid_step_count(day_grid) * optimization_grid_step_count(month_grid)


EXPECTED_SKIP_COMBINATIONS = expected_skip_combinations()
