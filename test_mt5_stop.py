"""Tests for mt5_stop process teardown (no live MT5 required)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from mt5_stop import free_tester_ports, main, stop_terminal64


def test_stop_terminal64_kills_metatester_after_terminal() -> None:
    cmds: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: object) -> MagicMock:
        cmds.append(list(cmd))
        return MagicMock(returncode=128, stdout="not found", stderr="")

    with patch("mt5_stop.subprocess.run", side_effect=fake_run):
        stop_terminal64()
    images = [cmd[2] for cmd in cmds if cmd[:2] == ["taskkill", "/IM"]]
    assert images == ["terminal64.exe", "metatester64.exe"]


def test_stop_terminal64_kills_metatester_even_if_terminal_taskkill_fails() -> None:
    cmds: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: object) -> MagicMock:
        cmds.append(list(cmd))
        image = cmd[2]
        if image == "terminal64.exe":
            return MagicMock(returncode=1, stdout="", stderr="Access denied")
        return MagicMock(returncode=128, stdout="not found", stderr="")

    with patch("mt5_stop.subprocess.run", side_effect=fake_run):
        try:
            stop_terminal64()
        except SystemExit as exc:
            assert exc.code == 1
        else:
            raise AssertionError("expected SystemExit")
    images = [cmd[2] for cmd in cmds if cmd[:2] == ["taskkill", "/IM"]]
    assert images == ["terminal64.exe", "metatester64.exe"]


def test_free_tester_ports_reports_reaped_pids(capsys: object) -> None:
    with patch("mt5_stop.free_local_tester_ports", return_value=[20352, 20353]):
        free_tester_ports()
    out = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "20352" in out
    assert "20353" in out


def test_free_tester_ports_reports_clear_when_nothing_to_reap(capsys: object) -> None:
    with patch("mt5_stop.free_local_tester_ports", return_value=[]):
        free_tester_ports()
    out = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "clear of MT5 tester agents" in out


def test_main_frees_tester_ports_after_stop() -> None:
    with (
        patch("mt5_stop.stop_python_batch_scripts") as stop_py,
        patch("mt5_stop.stop_terminal64") as stop_term,
        patch("mt5_stop.free_tester_ports") as free_ports,
    ):
        assert main() == 0
    stop_py.assert_called_once()
    stop_term.assert_called_once()
    free_ports.assert_called_once()
