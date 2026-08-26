"""Tests for mt5_stop process teardown (no live MT5 required)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from mt5_stop import stop_terminal64


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
