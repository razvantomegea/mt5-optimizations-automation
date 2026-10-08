"""Unit tests for terminal Server= parsing and server→company cache."""

from __future__ import annotations

from pathlib import Path

import pytest

from mt5_broker_identity import (
    BrokerMismatchError,
    load_server_companies,
    read_terminal_server,
    record_server_company,
    resolve_terminal_company,
)


def _write_common_ini(data_dir: Path, body: str, *, encoding: str = "utf-16") -> None:
    config = data_dir / "config"
    config.mkdir(parents=True, exist_ok=True)
    (config / "common.ini").write_text(body, encoding=encoding)


def test_read_terminal_server_utf16_common_section(tmp_path: Path) -> None:
    _write_common_ini(
        tmp_path,
        "[Common]\nLogin=12345\nServer=PepperstoneEU-Live\nProxyEnable=0\n",
    )
    assert read_terminal_server(tmp_path) == "PepperstoneEU-Live"


def test_read_terminal_server_ignores_other_sections(tmp_path: Path) -> None:
    _write_common_ini(
        tmp_path,
        "[Charts]\nServer=Wrong\n[Common]\nServer=FTMO-Demo\n",
    )
    assert read_terminal_server(tmp_path) == "FTMO-Demo"


def test_read_terminal_server_missing_file(tmp_path: Path) -> None:
    assert read_terminal_server(tmp_path) is None


def test_read_terminal_server_empty_value(tmp_path: Path) -> None:
    _write_common_ini(tmp_path, "[Common]\nServer=\n")
    assert read_terminal_server(tmp_path) is None


def test_record_and_load_server_companies_round_trip(tmp_path: Path) -> None:
    cache = tmp_path / "broker_servers.json"
    record_server_company(
        "PepperstoneEU-Live",
        "  Pepperstone EU Limited  ",
        path=cache,
    )
    record_server_company("Tradeslide-Live", "Tradeslide Trading Tech Limited", path=cache)
    mapping = load_server_companies(path=cache)
    assert mapping == {
        "PepperstoneEU-Live": "Pepperstone EU Limited",
        "Tradeslide-Live": "Tradeslide Trading Tech Limited",
    }


def test_resolve_terminal_company_unknown_server(tmp_path: Path) -> None:
    _write_common_ini(tmp_path, "[Common]\nServer=NewBroker-Live\n")
    cache = tmp_path / "broker_servers.json"
    server, company = resolve_terminal_company(tmp_path, cache_path=cache)
    assert server == "NewBroker-Live"
    assert company is None


def test_resolve_terminal_company_known_server(tmp_path: Path) -> None:
    _write_common_ini(tmp_path, "[Common]\nServer=PepperstoneEU-Live\n")
    cache = tmp_path / "broker_servers.json"
    record_server_company(
        "PepperstoneEU-Live",
        "Pepperstone EU Limited",
        path=cache,
    )
    server, company = resolve_terminal_company(tmp_path, cache_path=cache)
    assert server == "PepperstoneEU-Live"
    assert company == "Pepperstone EU Limited"


def test_broker_mismatch_error_is_not_value_or_runtime() -> None:
    error = BrokerMismatchError(
        expected_company="Pepperstone EU Limited",
        terminal_company="Tradeslide Trading Tech Limited",
    )
    assert not isinstance(error, ValueError)
    assert not isinstance(error, RuntimeError)
    assert error.expected_company == "Pepperstone EU Limited"
    assert error.terminal_company == "Tradeslide Trading Tech Limited"
    with pytest.raises(BrokerMismatchError):
        raise error
