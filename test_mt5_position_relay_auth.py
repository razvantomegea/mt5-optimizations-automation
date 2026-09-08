from __future__ import annotations

import pytest

from mt5_position_relay_auth import (
    assert_optimizer_access,
    resolve_position_relay_api_base,
    resolve_position_relay_user_id,
)

_AUTH_ENV = (
    "POSITIONRELAY_API_BASE_URL",
    "TRADEECHO_API_BASE_URL",
    "POSITIONRELAY_USER_ID",
    "TRADEECHO_USER_ID",
    "POSITIONRELAY_SKIP_ACCESS_CHECK",
    "TRADEECHO_SKIP_ACCESS_CHECK",
)


@pytest.fixture
def clear_auth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _AUTH_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, "https://positionrelay.com"),
        ({"POSITIONRELAY_API_BASE_URL": "https://a.example/"}, "https://a.example"),
        ({"TRADEECHO_API_BASE_URL": "https://legacy.example/"}, "https://legacy.example"),
        (
            {
                "POSITIONRELAY_API_BASE_URL": "https://a.example/",
                "TRADEECHO_API_BASE_URL": "https://b.example",
            },
            "https://a.example",
        ),
    ],
)
def test_api_base_resolution(
    clear_auth_env: None,
    monkeypatch: pytest.MonkeyPatch,
    env: dict[str, str],
    expected: str,
) -> None:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert resolve_position_relay_api_base() == expected


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"POSITIONRELAY_USER_ID": "new-id", "TRADEECHO_USER_ID": "old-id"}, "new-id"),
        ({"TRADEECHO_USER_ID": "old-id"}, "old-id"),
    ],
)
def test_user_id_resolution(
    clear_auth_env: None,
    monkeypatch: pytest.MonkeyPatch,
    env: dict[str, str],
    expected: str,
) -> None:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert resolve_position_relay_user_id() == expected


def test_missing_user_id_exits(clear_auth_env: None) -> None:
    with pytest.raises(SystemExit):
        resolve_position_relay_user_id()


@pytest.mark.parametrize("flag", ["POSITIONRELAY_SKIP_ACCESS_CHECK", "TRADEECHO_SKIP_ACCESS_CHECK"])
def test_skip_access_check_bypasses_api(
    clear_auth_env: None,
    monkeypatch: pytest.MonkeyPatch,
    flag: str,
) -> None:
    monkeypatch.setenv(flag, "1")
    assert_optimizer_access()
