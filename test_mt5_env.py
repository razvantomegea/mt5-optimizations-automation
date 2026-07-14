from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest


@pytest.fixture
def mt5_env_module(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    package_root = tmp_path / "package"
    package_root.mkdir()

    monkeypatch.delenv("TRADEECHO_USER_ID", raising=False)

    import mt5_env
    import mt5_workspace

    monkeypatch.setattr(mt5_workspace, "PACKAGE_ROOT", package_root)
    monkeypatch.setattr(mt5_env, "PACKAGE_ROOT", package_root)

    return importlib.reload(mt5_env)


def test_load_repo_env_reads_env_local(
    mt5_env_module,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_root = mt5_env_module.PACKAGE_ROOT

    (package_root / ".env.local").write_text(
        "TRADEECHO_USER_ID=from-env-local\n",
        encoding="utf-8",
    )

    monkeypatch.delenv("TRADEECHO_USER_ID", raising=False)
    mt5_env_module.load_repo_env()

    assert os.environ["TRADEECHO_USER_ID"] == "from-env-local"


def test_load_repo_env_prefers_env_local_over_env(
    mt5_env_module,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_root = mt5_env_module.PACKAGE_ROOT

    (package_root / ".env.local").write_text(
        "TRADEECHO_USER_ID=from-env-local\n",
        encoding="utf-8",
    )
    (package_root / ".env").write_text(
        "TRADEECHO_USER_ID=from-env\n",
        encoding="utf-8",
    )

    monkeypatch.delenv("TRADEECHO_USER_ID", raising=False)
    mt5_env_module.load_repo_env()

    assert os.environ["TRADEECHO_USER_ID"] == "from-env-local"


def test_load_repo_env_does_not_overwrite_existing_env(
    mt5_env_module,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_root = mt5_env_module.PACKAGE_ROOT

    (package_root / ".env.local").write_text(
        "TRADEECHO_USER_ID=from-file\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("TRADEECHO_USER_ID", "already-set")
    mt5_env_module.load_repo_env()

    assert os.environ["TRADEECHO_USER_ID"] == "already-set"
