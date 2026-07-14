#!/usr/bin/env python3
"""Stop MT5 terminal64.exe and running MT5 batch Python scripts."""

from __future__ import annotations

import subprocess
import sys

PYTHON_SCRIPTS = ("mt5_batch_optimize",)


def _run_powershell(script: str) -> None:
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        check=False,
    )


def stop_python_batch_scripts() -> None:
    match = " -or ".join(
        f"$_.CommandLine -like '*{name}*'" for name in PYTHON_SCRIPTS
    )
    _run_powershell(
        "$procs = Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" "
        f"| Where-Object {{ {match} }}; "
        "if ($procs) { $procs | ForEach-Object { "
        "Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; "
        "Write-Host ('Stopped python pid ' + $_.ProcessId) } } "
        "else { Write-Host 'No MT5 python batch script running' }"
    )


def stop_terminal64() -> None:
    result = subprocess.run(
        ["taskkill", "/IM", "terminal64.exe", "/F"],
        check=False,
    )
    if result.returncode != 0:
        print("No terminal64.exe process running")


def main() -> int:
    stop_python_batch_scripts()
    stop_terminal64()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
