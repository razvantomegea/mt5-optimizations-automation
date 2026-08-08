"""Shared MT5 Strategy Tester launch helpers (ini, reports, managed terminal)."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

REPORT_SUFFIXES = (".xml", ".htm", ".html")
# MT5 truncates Report= values around 181-183 chars (181 OK, 183 drops chars).
MT5_REPORT_PATH_MAX_LEN = 180

_managed_terminal: subprocess.Popen[Any] | None = None


def format_set_param_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return ""
    return str(v)


def write_ini(path: Path, cfg: dict[str, Any]) -> None:
    lines = ["[Tester]"]
    ordered_keys = [
        "Expert",
        "ExpertParameters",
        "Symbol",
        "Period",
        "Login",
        "Model",
        "ExecutionMode",
        "Optimization",
        "OptimizationCriterion",
        "FromDate",
        "ToDate",
        "ForwardMode",
        "ForwardDate",
        "Report",
        "ReplaceReport",
        "ShutdownTerminal",
        "Deposit",
        "Currency",
        "Leverage",
        "UseLocal",
        "UseRemote",
        "UseCloud",
        "Visual",
        "Port",
    ]
    for key in ordered_keys:
        value = cfg.get(key)
        if value not in (None, ""):
            lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _terminal_process_running() -> bool:
    if sys.platform != "win32":
        return False
    result = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq terminal64.exe", "/NH"],
        capture_output=True,
        text=True,
        check=False,
    )
    return "terminal64.exe" in (result.stdout or "").lower()


def stop_managed_terminal() -> None:
    """Terminate only the terminal process started by ``start_terminal``."""
    global _managed_terminal
    proc = _managed_terminal
    _managed_terminal = None
    if proc is None:
        return
    if proc.poll() is not None:
        return
    proc.kill()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass


def stop_running_terminal() -> None:
    """Stop the managed tester terminal only (does not taskkill other instances)."""
    stop_managed_terminal()


def ensure_terminal_available() -> None:
    """Ensure no foreign terminal64.exe blocks Strategy Tester /config launches."""
    stop_managed_terminal()
    if _terminal_process_running():
        raise RuntimeError(
            "terminal64.exe is already running; close it before launching Strategy Tester "
            "(MT5 ignores [Tester] config while another instance is open)."
        )


def start_terminal(
    cmd: list[str],
    *,
    cwd: str | Path,
) -> subprocess.Popen[Any]:
    """Launch terminal64 after ensuring no unmanaged instance is blocking."""
    global _managed_terminal
    ensure_terminal_available()
    proc = subprocess.Popen(cmd, cwd=str(cwd))
    _managed_terminal = proc
    return proc


def resolve_report_path(report_base: Path) -> Path:
    for suffix in REPORT_SUFFIXES:
        candidate = Path(str(report_base) + suffix)
        if candidate.is_file():
            return candidate
    # MT5 sometimes writes Report= path with no .htm/.html/.xml suffix.
    if report_base.is_file():
        return report_base
    tried = ", ".join(
        [str(report_base) + suffix for suffix in REPORT_SUFFIXES] + [str(report_base)]
    )
    raise FileNotFoundError(f"Backtest report not generated (tried: {tried})")


def _relpath_under_data(report_base: Path, data_dir: Path) -> str:
    return os.path.relpath(report_base, data_dir).replace("/", "\\")


def build_tester_report_target(
    *,
    data_dir: Path,
    work_dir: Path,
    stem: str,
) -> tuple[Path, str]:
    """Return (report_base, Report= relpath) within MT5's Report= length limit."""
    reports_dir = work_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_base = reports_dir / stem
    try:
        rel = _relpath_under_data(report_base, data_dir)
    except ValueError:
        # Cross-volume paths cannot use relpath — keep reports under data_dir.
        short_dir = data_dir / "te_bt"
        short_dir.mkdir(parents=True, exist_ok=True)
        report_base = short_dir / stem
        rel = _relpath_under_data(report_base, data_dir)

    if len(rel) <= MT5_REPORT_PATH_MAX_LEN:
        return report_base, rel

    short_dir = data_dir / "te_bt"
    short_dir.mkdir(parents=True, exist_ok=True)
    report_base = short_dir / stem
    rel = _relpath_under_data(report_base, data_dir)
    if len(rel) > MT5_REPORT_PATH_MAX_LEN:
        compact = hashlib.sha1(stem.encode("utf-8")).hexdigest()[:20]
        report_base = short_dir / compact
        rel = _relpath_under_data(report_base, data_dir)
    if len(rel) > MT5_REPORT_PATH_MAX_LEN:
        raise ValueError(
            f"MT5 Report path still too long ({len(rel)} > {MT5_REPORT_PATH_MAX_LEN}): {rel}"
        )
    return report_base, rel
