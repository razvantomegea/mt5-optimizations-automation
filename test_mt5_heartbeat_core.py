"""Tests for optimizer heartbeat command handling."""

from __future__ import annotations

from unittest.mock import ANY, MagicMock

from mt5_heartbeat_core import (
    OptimizeConfig,
    _resolve_run_id,
    build_optimize_argv,
    create_optimizer_heartbeat,
    read_favorite_payload,
    read_start_payload,
)

def _start_payload() -> dict:
    return {
        "fromDate": "2016.07.02",
        "toDate": "2026.07.02",
        "symbols": ["EURUSD"],
        "timeframes": ["H1"],
        "strategies": ["Classic", "Multi"],
        "optimizationMode": "2",
    }


def test_build_optimize_argv_includes_resume_flag() -> None:
    config = OptimizeConfig(
        from_date="2016.07.02",
        to_date="2026.07.02",
        symbols=["EURUSD"],
        timeframes=["H1"],
        strategies=["Classic"],
        optimization_mode="1",
        resume=True,
    )
    argv = build_optimize_argv(
        config,
        script_path="mt5_batch_optimize.py",
        expert="MyEA.ex5",
    )
    assert argv[-1] == "--resume"


def test_read_start_payload_normalizes_strategies() -> None:
    config = read_start_payload(
        action="start",
        payload={
            **_start_payload(),
            "strategies": ["classic", "MULTI"],
        },
    )
    assert config.strategies == ["Classic", "Multi"]


def test_process_start_command_launches_optimize_without_blocking() -> None:
    worker_store = MagicMock()
    run_optimize = MagicMock()
    heartbeat = create_optimizer_heartbeat(
        worker_store=worker_store,
        run_optimize=run_optimize,
        run_stop=MagicMock(),
        run_clean=MagicMock(),
        random_id=lambda: "00000000-0000-0000-0000-000000000001",
    )

    heartbeat.process_command(
        {
            "id": "cmd-1",
            "action": "start",
            "payload": _start_payload(),
        }
    )

    worker_store.start_run.assert_called_once()
    worker_store.mark_command_done.assert_called_with(
        command_id="cmd-1",
        status="done",
    )
    heartbeat.await_active_run()
    run_optimize.assert_called_once()
    worker_store.set_worker_idle.assert_called()


def test_process_stop_command_runs_stop_steps() -> None:
    worker_store = MagicMock()
    run_stop = MagicMock()
    heartbeat = create_optimizer_heartbeat(
        worker_store=worker_store,
        run_optimize=MagicMock(),
        run_stop=run_stop,
        run_clean=MagicMock(),
    )

    heartbeat.process_command({"id": "cmd-stop", "action": "stop"})

    run_stop.assert_called_once()
    worker_store.mark_running_runs_stopped.assert_called_once()
    worker_store.mark_command_done.assert_called_with(
        command_id="cmd-stop",
        status="done",
    )


def test_process_favorite_command_moves_files() -> None:
    worker_store = MagicMock()
    run_favorite = MagicMock()
    heartbeat = create_optimizer_heartbeat(
        worker_store=worker_store,
        run_optimize=MagicMock(),
        run_stop=MagicMock(),
        run_clean=MagicMock(),
        run_favorite=run_favorite,
    )

    heartbeat.process_command(
        {
            "id": "cmd-favorite",
            "action": "favorite",
            "payload": {
                "setFile": "foo.set",
                "symbol": "EURUSD",
            },
        }
    )

    run_favorite.assert_called_with(
        "foo.set",
        "EURUSD",
        False,
    )
    worker_store.mark_command_done.assert_called_with(
        command_id="cmd-favorite",
        status="done",
    )


def test_read_favorite_payload_normalizes_symbol_before_validate() -> None:
    set_file, symbol = read_favorite_payload(
        {"setFile": "foo.set", "symbol": "eurusd"},
    )
    assert set_file == "foo.set"
    assert symbol == "EURUSD"


def test_read_favorite_payload_rejects_path_like_set_file() -> None:
    import pytest

    with pytest.raises(ValueError, match="valid setFile"):
        read_favorite_payload(
            {"setFile": r"C:\reports\Best\sets\foo.set", "symbol": "EURUSD"},
        )


def test_resolve_run_id_reuses_payload_on_resume() -> None:
    resume_id = "00000000-0000-0000-0000-000000000099"
    assert (
        _resolve_run_id(
            action="resume",
            payload={"runId": resume_id},
            random_id=lambda: "00000000-0000-0000-0000-000000000001",
        )
        == resume_id
    )
    assert (
        _resolve_run_id(
            action="start",
            payload={"runId": resume_id},
            random_id=lambda: "00000000-0000-0000-0000-000000000001",
        )
        == "00000000-0000-0000-0000-000000000001"
    )


def test_poll_commands_fails_invalid_resume_payload() -> None:
    worker_store = MagicMock()
    worker_store.claim_pending_command.return_value = {
        "id": "cmd-resume",
        "action": "resume",
        "payload": {},
    }
    heartbeat = create_optimizer_heartbeat(
        worker_store=worker_store,
        run_optimize=MagicMock(),
        run_stop=MagicMock(),
        run_clean=MagicMock(),
    )

    heartbeat.poll_commands()

    worker_store.fail_running_runs.assert_called_once()
    worker_store.mark_command_done.assert_called_with(
        command_id="cmd-resume",
        status="failed",
        error=ANY,
    )
