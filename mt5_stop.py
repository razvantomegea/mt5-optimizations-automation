#!/usr/bin/env python3
"""Stop MT5 terminal64.exe, leftover metatester64.exe agents, and batch Python.

Also frees localhost:3000–3015 held by orphan tester agents so the next
Strategy Tester launch (or ``pnpm dev``) can bind them.
"""

from __future__ import annotations

import subprocess
import sys

from mt5_tester_runtime import free_local_tester_ports

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


def _stop_image(image_name: str) -> None:
    result = subprocess.run(
        ["taskkill", "/IM", image_name, "/F"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return
    output = f"{result.stdout}\n{result.stderr}".lower()
    if result.returncode == 128 or "not found" in output:
        print(f"No {image_name} process running")
        return
    message = (
        (result.stderr or result.stdout or "").strip()
        or f"taskkill {image_name} failed (code {result.returncode})"
    )
    print(message, file=sys.stderr)
    raise SystemExit(result.returncode)


def stop_terminal64() -> None:
    # Terminal first so it cannot respawn agents, then leftover testers.
    # Always attempt both; re-raise the first failure after cleanup.
    first_error: BaseException | None = None
    for image_name in ("terminal64.exe", "metatester64.exe"):
        try:
            _stop_image(image_name)
        except SystemExit as exc:
            if first_error is None:
                first_error = exc
    if first_error is not None:
        raise first_error


def free_tester_ports() -> None:
    """Reap MT5 listeners still holding localhost:3000–3015 after image kill."""
    killed = free_local_tester_ports()
    if killed:
        print(
            "Freed localhost tester port(s) held by pid(s): "
            + ", ".join(str(pid) for pid in killed)
        )
    else:
        print("localhost:3000–3015 clear of MT5 tester agents")


def main() -> int:
    stop_python_batch_scripts()
    stop_terminal64()
    free_tester_ports()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
