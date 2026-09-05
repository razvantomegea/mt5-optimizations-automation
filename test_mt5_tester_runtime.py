"""Tests for tester report path resolution (no MT5 required)."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from mt5_tester_runtime import (
    SW_SHOWMINNOACTIVE,
    clear_report_artifacts,
    resolve_report_path,
    start_terminal,
    stop_managed_terminal,
)


def test_resolve_report_path_prefers_newer_html_over_stale_xml(tmp_path: Path) -> None:
    base = tmp_path / "pass962_m1"
    xml = Path(str(base) + ".xml")
    htm = Path(str(base) + ".htm")
    xml.write_text("<xml>old</xml>", encoding="utf-8")
    time.sleep(0.05)
    htm.write_text("<html>new</html>", encoding="utf-8")
    assert resolve_report_path(base) == htm


def test_resolve_report_path_prefers_html_when_mtime_ties(tmp_path: Path) -> None:
    base = tmp_path / "pass4802_m1"
    xml = Path(str(base) + ".xml")
    htm = Path(str(base) + ".htm")
    # Write HTML first then XML with matching mtime via os.utime if needed.
    htm.write_text("<html>htm</html>", encoding="utf-8")
    xml.write_text("<xml>xml</xml>", encoding="utf-8")
    shared = htm.stat().st_mtime_ns
    import os

    os.utime(htm, ns=(shared, shared))
    os.utime(xml, ns=(shared, shared))
    assert resolve_report_path(base) == htm


def test_clear_report_artifacts_removes_all_suffixes(tmp_path: Path) -> None:
    base = tmp_path / "cand_m1"
    Path(str(base) + ".htm").write_text("x", encoding="utf-8")
    Path(str(base) + ".xml").write_text("y", encoding="utf-8")
    base.write_text("z", encoding="utf-8")
    clear_report_artifacts(base)
    assert not Path(str(base) + ".htm").exists()
    assert not Path(str(base) + ".xml").exists()
    assert not base.exists()


def test_build_tester_report_target_prefer_short_avoids_dotdot(
    tmp_path: Path,
) -> None:
    from mt5_tester_runtime import build_tester_report_target

    data_dir = tmp_path / "AppData" / "MetaQuotes" / "Terminal" / "ABC"
    work_dir = tmp_path / "Projects" / "ea-sync" / "validate_staging" / "cand"
    data_dir.mkdir(parents=True)
    work_dir.mkdir(parents=True)
    report_base, rel = build_tester_report_target(
        data_dir=data_dir,
        work_dir=work_dir,
        stem="EURUSD_M15_Classic_pass27245_m1",
        prefer_short_path=True,
    )
    assert "te_bt" in report_base.parts
    assert ".." not in Path(rel).parts
    assert len(rel) < 180


def test_real_tick_terminal_starts_minimized_without_activation(tmp_path: Path) -> None:
    proc = MagicMock()
    proc.pid = 4242
    proc.poll.return_value = None
    with (
        patch("mt5_tester_runtime._image_running", return_value=False),
        patch("mt5_tester_runtime._listener_images_on_port", return_value=[]),
        patch("mt5_tester_runtime.free_local_tester_ports", return_value=[]),
        patch(
            "mt5_tester_runtime.reap_foreign_listeners_on_local_tester_port",
            return_value=[],
        ),
        patch("mt5_tester_runtime.subprocess.Popen", return_value=proc) as popen,
    ):
        try:
            started = start_terminal(["terminal64.exe"], cwd=tmp_path, show_window=False)
        finally:
            stop_managed_terminal()
    assert started is proc
    kwargs = popen.call_args.kwargs
    if sys.platform == "win32":
        info = kwargs["startupinfo"]
        assert info.wShowWindow == SW_SHOWMINNOACTIVE
        assert info.dwFlags & subprocess.STARTF_USESHOWWINDOW
    else:
        assert "startupinfo" not in kwargs


def test_default_terminal_launch_leaves_window_visible(tmp_path: Path) -> None:
    proc = MagicMock()
    proc.pid = 4242
    proc.poll.return_value = 0
    with (
        patch("mt5_tester_runtime._image_running", return_value=False),
        patch("mt5_tester_runtime._listener_images_on_port", return_value=[]),
        patch("mt5_tester_runtime.free_local_tester_ports", return_value=[]),
        patch(
            "mt5_tester_runtime.reap_foreign_listeners_on_local_tester_port",
            return_value=[],
        ),
        patch("mt5_tester_runtime.subprocess.Popen", return_value=proc) as popen,
    ):
        try:
            start_terminal(["terminal64.exe"], cwd=tmp_path)
        finally:
            stop_managed_terminal()
    assert "startupinfo" not in popen.call_args.kwargs


def test_force_kill_terminal64_kills_metatester_agents() -> None:
    from mt5_tester_runtime import force_kill_terminal64

    cmds: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: object) -> MagicMock:
        cmds.append(list(cmd))
        return MagicMock(returncode=0, stdout="", stderr="")

    with (
        patch("mt5_tester_runtime.sys.platform", "win32"),
        patch("mt5_tester_runtime.subprocess.run", side_effect=fake_run),
        patch("mt5_tester_runtime._image_running", return_value=False),
        patch("mt5_tester_runtime.time.sleep"),
    ):
        force_kill_terminal64()
    images = [cmd[2] for cmd in cmds if cmd[:2] == ["taskkill", "/IM"]]
    assert images == ["terminal64.exe", "metatester64.exe"]


def test_reap_mt5_listeners_kills_metatester_pid_on_port_3000() -> None:
    from mt5_tester_runtime import reap_mt5_listeners_on_tester_ports

    killed: list[int] = []

    with (
        patch("mt5_tester_runtime.sys.platform", "win32"),
        patch(
            "mt5_tester_runtime._listener_pids_on_tester_ports",
            return_value={3000: [20352], 3001: []},
        ),
        patch(
            "mt5_tester_runtime._image_name_for_pid",
            return_value="metatester64.exe",
        ),
        patch(
            "mt5_tester_runtime._taskkill_pid",
            side_effect=lambda pid: killed.append(pid),
        ),
    ):
        assert reap_mt5_listeners_on_tester_ports() == [20352]
    assert killed == [20352]


def test_reap_mt5_listeners_leaves_node_on_port_3000() -> None:
    from mt5_tester_runtime import reap_mt5_listeners_on_tester_ports

    with (
        patch("mt5_tester_runtime.sys.platform", "win32"),
        patch(
            "mt5_tester_runtime._listener_pids_on_tester_ports",
            return_value={3000: [999]},
        ),
        patch("mt5_tester_runtime._image_name_for_pid", return_value="node.exe"),
        patch("mt5_tester_runtime._taskkill_pid") as kill_pid,
    ):
        assert reap_mt5_listeners_on_tester_ports() == []
    kill_pid.assert_not_called()


def test_reap_foreign_listeners_kills_node_leaves_metatester() -> None:
    from mt5_tester_runtime import reap_foreign_listeners_on_local_tester_port

    killed: list[int] = []

    def image_for_pid(pid: int) -> str:
        return {999: "node.exe", 20352: "metatester64.exe"}[pid]

    with (
        patch("mt5_tester_runtime.sys.platform", "win32"),
        patch(
            "mt5_tester_runtime._listener_pids_on_tester_ports",
            return_value={3000: [999, 20352], 3001: [888]},
        ),
        patch("mt5_tester_runtime._image_name_for_pid", side_effect=image_for_pid),
        patch(
            "mt5_tester_runtime._taskkill_pid",
            side_effect=lambda pid: killed.append(pid),
        ),
    ):
        assert reap_foreign_listeners_on_local_tester_port() == [999]
    assert killed == [999]


def test_reap_foreign_listeners_skips_unresolved_image() -> None:
    from mt5_tester_runtime import reap_foreign_listeners_on_local_tester_port

    with (
        patch("mt5_tester_runtime.sys.platform", "win32"),
        patch(
            "mt5_tester_runtime._listener_pids_on_tester_ports",
            return_value={3000: [4242]},
        ),
        patch("mt5_tester_runtime._image_name_for_pid", return_value=None),
        patch("mt5_tester_runtime._taskkill_pid") as kill_pid,
    ):
        assert reap_foreign_listeners_on_local_tester_port() == []
    kill_pid.assert_not_called()


def test_wait_for_terminal_exit_force_kills_leftover_agents() -> None:
    from mt5_tester_runtime import wait_for_terminal_exit

    running = {"terminal64.exe": False, "metatester64.exe": True}

    def image_running(name: str) -> bool:
        return running.get(name, False)

    def taskkill(name: str) -> None:
        running[name] = False

    with (
        patch("mt5_tester_runtime._image_running", side_effect=image_running),
        patch("mt5_tester_runtime._taskkill_image", side_effect=taskkill),
        patch("mt5_tester_runtime.free_local_tester_ports", return_value=[]),
        patch("mt5_tester_runtime.time.sleep"),
    ):
        wait_for_terminal_exit(timeout_seconds=0.0, force_kill=True)
    assert running["metatester64.exe"] is False


def test_kill_orphan_tester_agents_raises_if_still_running() -> None:
    from mt5_tester_runtime import _kill_orphan_tester_agents

    clock = {"now": 0.0}

    def fake_time() -> float:
        return clock["now"]

    def fake_sleep(seconds: float) -> None:
        clock["now"] += seconds

    with (
        patch("mt5_tester_runtime._image_running", return_value=True),
        patch("mt5_tester_runtime._taskkill_image"),
        patch("mt5_tester_runtime.time.time", side_effect=fake_time),
        patch("mt5_tester_runtime.time.sleep", side_effect=fake_sleep),
    ):
        try:
            _kill_orphan_tester_agents()
        except RuntimeError as exc:
            assert "still running" in str(exc)
        else:
            raise AssertionError("expected RuntimeError")


def test_ensure_terminal_available_kills_orphan_agents() -> None:
    from mt5_tester_runtime import ensure_terminal_available

    running = {"terminal64.exe": False, "metatester64.exe": True}
    killed: list[str] = []

    def image_running(name: str) -> bool:
        return running.get(name, False)

    def taskkill(name: str) -> None:
        killed.append(name)
        running[name] = False

    with (
        patch("mt5_tester_runtime._image_running", side_effect=image_running),
        patch("mt5_tester_runtime._taskkill_image", side_effect=taskkill),
        patch("mt5_tester_runtime._listener_images_on_port", return_value=[]),
        patch("mt5_tester_runtime.free_local_tester_ports", return_value=[]),
        patch(
            "mt5_tester_runtime.reap_foreign_listeners_on_local_tester_port",
            return_value=[],
        ),
        patch("mt5_tester_runtime.time.sleep"),
    ):
        ensure_terminal_available()
    assert killed == ["metatester64.exe"]


def test_ensure_terminal_available_kills_node_on_port_3000() -> None:
    from mt5_tester_runtime import ensure_terminal_available

    with (
        patch("mt5_tester_runtime._image_running", return_value=False),
        patch("mt5_tester_runtime.free_local_tester_ports", return_value=[]),
        patch(
            "mt5_tester_runtime.reap_foreign_listeners_on_local_tester_port",
            return_value=[999],
        ) as reap_foreign,
        patch("mt5_tester_runtime._listener_images_on_port", return_value=[]),
        patch("mt5_tester_runtime.time.sleep") as sleep,
    ):
        ensure_terminal_available()
    reap_foreign.assert_called_once_with()
    sleep.assert_called()


def test_ensure_terminal_available_raises_when_foreign_remains_after_reap() -> None:
    from mt5_tester_runtime import ensure_terminal_available

    with (
        patch("mt5_tester_runtime._image_running", return_value=False),
        patch("mt5_tester_runtime.free_local_tester_ports", return_value=[]),
        patch(
            "mt5_tester_runtime.reap_foreign_listeners_on_local_tester_port",
            return_value=[999],
        ),
        patch(
            "mt5_tester_runtime._listener_images_on_port",
            return_value=["node.exe"],
        ),
        patch("mt5_tester_runtime.time.sleep"),
    ):
        try:
            ensure_terminal_available()
        except RuntimeError as exc:
            assert "localhost:3000" in str(exc)
            assert "node.exe" in str(exc)
        else:
            raise AssertionError("expected RuntimeError")


def test_listening_pids_from_netstat_ignores_30000() -> None:
    from mt5_tester_runtime import listening_pids_from_netstat

    output = (
        "TCP    127.0.0.1:3000    0.0.0.0:0    LISTENING    111\n"
        "TCP    0.0.0.0:30000     0.0.0.0:0    LISTENING    222\n"
        "TCP    [::]:3000         [::]:0       LISTENING    333\n"
    )
    assert listening_pids_from_netstat(output, 3000) == [111, 333]


def test_listening_pids_from_netstat_ports_covers_agent_span() -> None:
    from mt5_tester_runtime import listening_pids_from_netstat_ports

    output = (
        "TCP    127.0.0.1:3000    0.0.0.0:0    LISTENING    111\n"
        "TCP    127.0.0.1:3015    0.0.0.0:0    LISTENING    222\n"
        "TCP    127.0.0.1:3016    0.0.0.0:0    LISTENING    333\n"
    )
    by_port = listening_pids_from_netstat_ports(output, {3000, 3015, 3016})
    assert by_port[3000] == [111]
    assert by_port[3015] == [222]
    assert by_port[3016] == [333]


def test_ensure_terminal_available_raises_for_foreign_terminal() -> None:
    from mt5_tester_runtime import ensure_terminal_available

    def image_running(name: str) -> bool:
        return name == "terminal64.exe"

    with (
        patch("mt5_tester_runtime._image_running", side_effect=image_running),
        patch("mt5_tester_runtime._taskkill_image") as kill,
    ):
        try:
            ensure_terminal_available()
        except RuntimeError as exc:
            assert "already running" in str(exc)
        else:
            raise AssertionError("expected RuntimeError")
    kill.assert_not_called()
