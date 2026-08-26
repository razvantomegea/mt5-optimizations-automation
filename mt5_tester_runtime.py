"""Shared MT5 Strategy Tester launch helpers (ini, reports, managed terminal)."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPORT_SUFFIXES = (".htm", ".html", ".xml")
# MT5 truncates Report= values around 181-183 chars (181 OK, 183 drops chars).
MT5_REPORT_PATH_MAX_LEN = 180
_TERMINAL_EXIT_WAIT_SEC = 60.0
_TERMINAL_EXIT_POLL_SEC = 0.5
# After killing leftover agents, wait for 127.0.0.1:3000+ to be reusable.
_AGENT_PORT_SETTLE_SEC = 1.0
_TERMINAL_IMAGE = "terminal64.exe"
_TESTER_AGENT_IMAGE = "metatester64.exe"
# MT5 local agents bind 127.0.0.1:3000 (same default as Next.js `pnpm dev`).
LOCAL_TESTER_AGENT_PORT = 3000
_ALLOWED_ON_TESTER_PORT = frozenset({"metatester64.exe", "terminal64.exe"})
# Win32 ShowWindow: minimize without activating (real-tick single tests freeze the UI).
SW_SHOWMINNOACTIVE = 7

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


def _image_running(image_name: str) -> bool:
    if sys.platform != "win32":
        return False
    result = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/NH"],
        capture_output=True,
        text=True,
        check=False,
    )
    return image_name.lower() in (result.stdout or "").lower()


def _terminal_process_running() -> bool:
    return _image_running(_TERMINAL_IMAGE)


def _tester_stack_running() -> bool:
    return _image_running(_TERMINAL_IMAGE) or _image_running(_TESTER_AGENT_IMAGE)


def _taskkill_image(image_name: str) -> None:
    if sys.platform != "win32":
        return
    subprocess.run(
        ["taskkill", "/IM", image_name, "/F"],
        check=False,
        capture_output=True,
        text=True,
    )


def listening_pids_from_netstat(output: str, port: int) -> list[int]:
    """Parse PIDs in LISTENING state on ``port`` from ``netstat -ano`` output."""
    pids: list[int] = []
    for raw in output.splitlines():
        line = raw.strip()
        if "LISTENING" not in line.upper():
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        local = parts[1] if parts[0].upper() in {"TCP", "UDP"} else parts[0]
        if _local_addr_port(local) != port:
            continue
        try:
            pids.append(int(parts[-1]))
        except ValueError:
            continue
    return pids


def _local_addr_port(addr: str) -> int | None:
    if addr.startswith("["):
        _host, sep, port_s = addr.rpartition("]:")
        if sep != "]:":
            return None
    else:
        _host, sep, port_s = addr.rpartition(":")
        if sep != ":":
            return None
    try:
        return int(port_s)
    except ValueError:
        return None


def _image_name_for_pid(pid: int) -> str | None:
    if sys.platform != "win32" or pid <= 0:
        return None
    result = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        check=False,
    )
    line = (result.stdout or "").strip()
    if not line or line.lower().startswith("info:"):
        return None
    name = line.split(",")[0].strip().strip('"')
    return name or None


def _listener_images_on_port(port: int) -> list[str]:
    if sys.platform != "win32":
        return []
    result = subprocess.run(
        ["netstat", "-ano", "-p", "TCP"],
        capture_output=True,
        text=True,
        check=False,
    )
    names: list[str] = []
    for pid in listening_pids_from_netstat(result.stdout or "", port):
        image = _image_name_for_pid(pid)
        if image:
            names.append(image)
    return names


def assert_local_tester_port_free() -> None:
    """Fail if a non-MT5 process owns the local tester agent port (default 3000)."""
    images = _listener_images_on_port(LOCAL_TESTER_AGENT_PORT)
    foreign = sorted(
        {name for name in images if name.lower() not in _ALLOWED_ON_TESTER_PORT}
    )
    if not foreign:
        return
    listed = ", ".join(foreign)
    raise RuntimeError(
        f"localhost:{LOCAL_TESTER_AGENT_PORT} is in use by {listed}. "
        "MT5 local tester agents bind that port; a foreign listener causes "
        "'tester agent authorization error' and empty stub reports. "
        "Stop it (often `pnpm dev` / Next.js) and retry."
    )


def _wait_until_image_exits(image_name: str, *, timeout_seconds: float) -> None:
    deadline = time.time() + max(0.0, timeout_seconds)
    while time.time() < deadline:
        if not _image_running(image_name):
            return
        time.sleep(_TERMINAL_EXIT_POLL_SEC)


def force_kill_terminal64() -> None:
    """Force-kill terminal64 and leftover local tester agents.

    Local agents listen on 127.0.0.1:3000+. Stop/timeout recovery must free
    that port so the next tester launch (or ``pnpm dev``) can bind it.
    """
    # Terminal first so it cannot respawn agents; then leftover testers.
    _taskkill_image(_TERMINAL_IMAGE)
    _taskkill_image(_TESTER_AGENT_IMAGE)


def _kill_orphan_tester_agents() -> None:
    """Kill metatester64.exe left behind after the terminal already exited."""
    if not _image_running(_TESTER_AGENT_IMAGE):
        return
    _taskkill_image(_TESTER_AGENT_IMAGE)
    _wait_until_image_exits(_TESTER_AGENT_IMAGE, timeout_seconds=10.0)
    if _image_running(_TESTER_AGENT_IMAGE):
        raise RuntimeError(
            f"{_TESTER_AGENT_IMAGE} is still running after taskkill; "
            "cannot start a new Strategy Tester session. "
            "Run: python mt5_stop.py"
        )
    time.sleep(_AGENT_PORT_SETTLE_SEC)


def wait_for_terminal_exit(
    *,
    timeout_seconds: float = _TERMINAL_EXIT_WAIT_SEC,
    force_kill: bool = False,
) -> None:
    """Block until terminal64.exe exits.

    ``force_kill=True`` also reaps leftover ``metatester64.exe`` so port 3000
    is free. Default is wait-only for the terminal (agents can outlive it).
    """
    deadline = time.time() + max(0.0, timeout_seconds)
    while time.time() < deadline:
        if not _terminal_process_running():
            break
        time.sleep(_TERMINAL_EXIT_POLL_SEC)
    if _terminal_process_running():
        if not force_kill:
            return
        force_kill_terminal64()
        settle_deadline = time.time() + 10.0
        while time.time() < settle_deadline and _tester_stack_running():
            time.sleep(_TERMINAL_EXIT_POLL_SEC)
        return
    if force_kill:
        _kill_orphan_tester_agents()


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
    """Stop the managed tester terminal; force-kill leftovers from that session."""
    stop_managed_terminal()
    wait_for_terminal_exit(timeout_seconds=15.0, force_kill=True)


def ensure_terminal_available() -> None:
    """Ensure no unmanaged terminal64.exe blocks Strategy Tester /config launches.

    Never force-kills foreign terminals — raise so the operator closes them
    (or runs ``python mt5_stop.py``). Orphan tester agents are reaped, then
    localhost:3000 is checked so Next.js / ``pnpm dev`` cannot occupy the
    MT5 local-agent port.
    """
    stop_managed_terminal()
    if _terminal_process_running():
        raise RuntimeError(
            "terminal64.exe is already running; close it before launching Strategy Tester "
            "(MT5 ignores [Tester] config while another instance is open). "
            "Run: python mt5_stop.py"
        )
    _kill_orphan_tester_agents()
    assert_local_tester_port_free()


def _minimize_windows_for_pid(pid: int) -> None:
    """Minimize visible windows owned by ``pid`` (no-op off Windows)."""
    if sys.platform != "win32" or pid <= 0:
        return
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _enum(hwnd: int, _lparam: int) -> bool:
        proc_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc_id))
        if proc_id.value == pid and user32.IsWindowVisible(hwnd):
            user32.ShowWindow(hwnd, SW_SHOWMINNOACTIVE)
        return True

    user32.EnumWindows(_enum, 0)


def start_terminal(
    cmd: list[str],
    *,
    cwd: str | Path,
    show_window: bool = True,
) -> subprocess.Popen[Any]:
    """Launch terminal64 after ensuring no unmanaged instance is blocking.

    ``show_window=False`` starts minimized (SW_SHOWMINNOACTIVE). Used for
    in-terminal Model=4 single tests, which freeze the MT5 UI until ticks finish.
    """
    global _managed_terminal
    ensure_terminal_available()
    popen_kwargs: dict[str, Any] = {"cwd": str(cwd)}
    if sys.platform == "win32" and not show_window:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = SW_SHOWMINNOACTIVE
        popen_kwargs["startupinfo"] = startupinfo
    proc = subprocess.Popen(cmd, **popen_kwargs)
    _managed_terminal = proc
    if not show_window:
        _minimize_windows_for_pid(proc.pid)
    return proc


def resolve_report_path(report_base: Path) -> Path:
    """Return the newest report artifact for ``report_base`` (prefer HTML over XML)."""
    candidates: list[Path] = []
    for suffix in REPORT_SUFFIXES:
        candidate = Path(str(report_base) + suffix)
        if candidate.is_file():
            candidates.append(candidate)
    # MT5 sometimes writes Report= path with no .htm/.html/.xml suffix.
    if report_base.is_file():
        candidates.append(report_base)
    if not candidates:
        tried = ", ".join(
            [str(report_base) + suffix for suffix in REPORT_SUFFIXES] + [str(report_base)]
        )
        raise FileNotFoundError(f"Backtest report not generated (tried: {tried})")

    def _rank(path: Path) -> tuple[int, int]:
        # Newest mtime first; on ties prefer .htm/.html over .xml.
        suffix = path.suffix.lower()
        html_pref = 0 if suffix in {".htm", ".html"} else (1 if suffix == ".xml" else 2)
        try:
            mtime_ns = path.stat().st_mtime_ns
        except OSError:
            mtime_ns = 0
        return (-mtime_ns, html_pref)

    return sorted(candidates, key=_rank)[0]


def clear_report_artifacts(report_base: Path) -> None:
    """Remove prior report files so ReplaceReport cannot leave a stale DD=0 read."""
    for suffix in REPORT_SUFFIXES:
        path = Path(str(report_base) + suffix)
        if path.is_file():
            path.unlink()
    if report_base.is_file():
        report_base.unlink()


def _relpath_under_data(report_base: Path, data_dir: Path) -> str:
    return os.path.relpath(report_base, data_dir).replace("/", "\\")


def build_tester_report_target(
    *,
    data_dir: Path,
    work_dir: Path,
    stem: str,
    prefer_short_path: bool = False,
) -> tuple[Path, str]:
    """Return (report_base, Report= relpath) within MT5's Report= length limit.

    When ``prefer_short_path`` is True (validation backtests), write under
    ``data_dir/te_bt`` so Report= stays short and inside the MT5 data tree —
    long ``..\\..\\Projects\\...`` paths often yield empty stub reports when
    the terminal is under load or hung.
    """
    reports_dir = work_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_base = reports_dir / stem
    try:
        rel = _relpath_under_data(report_base, data_dir)
    except ValueError:
        prefer_short_path = True
        rel = ""

    if prefer_short_path or ".." in Path(rel).parts or len(rel) > MT5_REPORT_PATH_MAX_LEN:
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
