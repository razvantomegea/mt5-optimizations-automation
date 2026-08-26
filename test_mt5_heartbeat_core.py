"""Tests for optimizer heartbeat command handling."""

from __future__ import annotations

from unittest.mock import ANY, MagicMock

from mt5_heartbeat_core import (
    OptimizeConfig,
    _resolve_run_id,
    build_optimize_argv,
    create_optimizer_heartbeat,
    read_favorite_payload,
    read_skip_robustness_payload,
    read_start_payload,
    scaled_max_equity_drawdown_percent,
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


def test_build_optimize_argv_includes_no_skip_robustness() -> None:
    config = OptimizeConfig(
        from_date="2016.07.02",
        to_date="2026.07.02",
        symbols=["EURUSD"],
        timeframes=["H1"],
        strategies=["Classic"],
        optimization_mode="2",
        resume=False,
        skip_robustness=False,
    )
    argv = build_optimize_argv(
        config,
        script_path="mt5_batch_optimize.py",
        expert="MyEA.ex5",
    )
    assert "--no-skip-robustness" in argv


def test_build_optimize_argv_omits_no_skip_robustness_by_default() -> None:
    config = OptimizeConfig(
        from_date="2016.07.02",
        to_date="2026.07.02",
        symbols=["EURUSD"],
        timeframes=["H1"],
        strategies=["Classic"],
        optimization_mode="2",
        resume=False,
    )
    argv = build_optimize_argv(
        config,
        script_path="mt5_batch_optimize.py",
        expert="MyEA.ex5",
    )
    assert "--no-skip-robustness" not in argv
    assert config.skip_robustness is True


def test_build_optimize_argv_resume_and_no_skip_robustness() -> None:
    config = OptimizeConfig(
        from_date="2016.07.02",
        to_date="2026.07.02",
        symbols=["EURUSD"],
        timeframes=["H1"],
        strategies=["Classic"],
        optimization_mode="1",
        resume=True,
        skip_robustness=False,
    )
    argv = build_optimize_argv(
        config,
        script_path="mt5_batch_optimize.py",
        expert="MyEA.ex5",
    )
    assert "--resume" in argv
    assert "--no-skip-robustness" in argv


def test_scaled_max_equity_drawdown_percent() -> None:
    assert scaled_max_equity_drawdown_percent(15.0) == 16.8
    assert scaled_max_equity_drawdown_percent(10.0) == 11.2
    assert scaled_max_equity_drawdown_percent(20.0) == 22.4
    assert scaled_max_equity_drawdown_percent(4.0) == 4.48


def test_build_optimize_argv_includes_account_settings() -> None:
    config = OptimizeConfig(
        from_date="2016.07.02",
        to_date="2026.07.02",
        symbols=["EURUSD"],
        timeframes=["H1"],
        strategies=["Classic"],
        optimization_mode="2",
        resume=False,
        deposit="25000",
        currency="EUR",
        max_equity_drawdown_percent=10.0,
    )
    argv = build_optimize_argv(
        config,
        script_path="mt5_batch_optimize.py",
        expert="MyEA.ex5",
    )
    assert argv[argv.index("--deposit") + 1] == "25000"
    assert argv[argv.index("--currency") + 1] == "EUR"
    assert argv[argv.index("--target-equity-dd") + 1] == "10.0"
    assert argv[argv.index("--max-equity-dd") + 1] == "11.2"


def test_read_start_payload_normalizes_strategies() -> None:
    config = read_start_payload(
        action="start",
        payload={
            **_start_payload(),
            "strategies": ["classic", "MULTI", "swingha"],
        },
    )
    assert config.strategies == ["Classic", "Multi", "SwingHA"]
    assert config.skip_robustness is True


def test_read_start_payload_skip_robustness_false() -> None:
    config = read_start_payload(
        action="start",
        payload={**_start_payload(), "skipRobustness": False},
    )
    assert config.skip_robustness is False


def test_read_start_payload_rejects_non_bool_skip_robustness() -> None:
    import pytest

    with pytest.raises(ValueError, match="skipRobustness"):
        read_start_payload(
            action="start",
            payload={**_start_payload(), "skipRobustness": "false"},
        )


def test_read_start_payload_defaults_account_settings() -> None:
    config = read_start_payload(action="start", payload=_start_payload())
    assert config.deposit == "100000"
    assert config.currency == "USD"
    assert config.max_equity_drawdown_percent == 15.0


def test_read_start_payload_account_settings() -> None:
    config = read_start_payload(
        action="start",
        payload={
            **_start_payload(),
            "deposit": 25000,
            "currency": "eur",
            "maxEquityDrawdownPercent": 10,
        },
    )
    assert config.deposit == "25000"
    assert config.currency == "EUR"
    assert config.max_equity_drawdown_percent == 10.0


def test_read_start_payload_rejects_invalid_account_settings() -> None:
    import pytest

    with pytest.raises(ValueError, match="currency"):
        read_start_payload(
            action="start",
            payload={**_start_payload(), "currency": "XYZ"},
        )
    with pytest.raises(ValueError, match="deposit"):
        read_start_payload(
            action="start",
            payload={**_start_payload(), "deposit": 0},
        )
    with pytest.raises(ValueError, match="deposit"):
        read_start_payload(
            action="start",
            payload={**_start_payload(), "deposit": ""},
        )
    with pytest.raises(ValueError, match="maxEquityDrawdownPercent"):
        read_start_payload(
            action="start",
            payload={**_start_payload(), "maxEquityDrawdownPercent": -1},
        )


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
    run_portfolio_build = MagicMock()
    heartbeat = create_optimizer_heartbeat(
        worker_store=worker_store,
        run_optimize=MagicMock(),
        run_stop=MagicMock(),
        run_clean=MagicMock(),
        run_favorite=run_favorite,
        run_portfolio_build=run_portfolio_build,
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
    run_portfolio_build.assert_called_once()
    worker_store.mark_command_done.assert_called_with(
        command_id="cmd-favorite",
        status="done",
    )


def test_process_favorite_command_failure_does_not_fail_running_runs() -> None:
    worker_store = MagicMock()
    run_favorite = MagicMock(side_effect=RuntimeError("Set file not found"))
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
                "setFile": "missing.set",
                "symbol": "EURUSD",
            },
        }
    )

    worker_store.mark_command_done.assert_called_with(
        command_id="cmd-favorite",
        status="failed",
        error="Set file not found",
    )
    worker_store.fail_running_runs.assert_not_called()
    worker_store.set_worker_idle.assert_not_called()


def test_process_favorite_command_marks_done_when_portfolio_refresh_fails() -> None:
    worker_store = MagicMock()
    run_favorite = MagicMock()
    run_portfolio_build = MagicMock(side_effect=RuntimeError("Portfolio build failed"))
    heartbeat = create_optimizer_heartbeat(
        worker_store=worker_store,
        run_optimize=MagicMock(),
        run_stop=MagicMock(),
        run_clean=MagicMock(),
        run_favorite=run_favorite,
        run_portfolio_build=run_portfolio_build,
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

    run_favorite.assert_called_once()
    run_portfolio_build.assert_called_once()
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


def _skip_payload() -> dict:
    return {
        "setFile": "foo.set",
        "symbol": "EURUSD",
        "timeframe": "H1",
        "fromDate": "2016.07.02",
        "toDate": "2026.07.02",
        "baselineDd": 11.4,
        "scaledRisk": 2.0,
        "resultId": "abc",
    }


def test_read_skip_robustness_payload_rejects_negative_baseline() -> None:
    import pytest

    with pytest.raises(ValueError, match="baselineDd"):
        read_skip_robustness_payload({**_skip_payload(), "baselineDd": -1.5})


def test_read_skip_robustness_payload_accepts_zero_baseline() -> None:
    parsed = read_skip_robustness_payload({**_skip_payload(), "baselineDd": 0})
    assert parsed.baseline_dd == 0.0


def test_read_skip_robustness_payload_accepts_positive_baseline() -> None:
    parsed = read_skip_robustness_payload(_skip_payload())
    assert parsed.baseline_dd == 11.4
    assert parsed.deposit is None
    assert parsed.currency is None


def test_read_skip_robustness_payload_inherits_account_settings() -> None:
    parsed = read_skip_robustness_payload(
        {**_skip_payload(), "deposit": "25000", "currency": "eur"}
    )
    assert parsed.deposit == "25000"
    assert parsed.currency == "EUR"
    assert parsed.scaled_risk == 2.0


def test_read_skip_robustness_payload_rejects_non_positive_scaled_risk() -> None:
    import pytest

    with pytest.raises(ValueError, match="scaledRisk"):
        read_skip_robustness_payload({**_skip_payload(), "scaledRisk": 0})
    with pytest.raises(ValueError, match="scaledRisk"):
        read_skip_robustness_payload({**_skip_payload(), "scaledRisk": -1})
    with pytest.raises(ValueError, match="scaledRisk"):
        read_skip_robustness_payload({**_skip_payload(), "scaledRisk": True})


def test_process_skip_robustness_launches_without_blocking() -> None:
    import threading

    worker_store = MagicMock()
    started = threading.Event()
    release = threading.Event()

    def run_skip(_command: object) -> None:
        started.set()
        assert release.wait(timeout=2)

    run_portfolio = MagicMock()
    heartbeat = create_optimizer_heartbeat(
        worker_store=worker_store,
        run_optimize=MagicMock(),
        run_stop=MagicMock(),
        run_clean=MagicMock(),
        run_favorite=MagicMock(),
        run_portfolio_build=run_portfolio,
        run_skip_robustness=run_skip,
    )

    heartbeat.process_command(
        {
            "id": "cmd-skip",
            "action": "skip_robustness",
            "payload": _skip_payload(),
        }
    )

    assert started.wait(timeout=2)
    worker_store.mark_command_done.assert_not_called()
    release.set()
    heartbeat.await_active_run()
    run_portfolio.assert_called_once()
    worker_store.mark_command_done.assert_called_with(
        command_id="cmd-skip",
        status="done",
    )


def test_process_skip_robustness_marks_failed_on_error() -> None:
    worker_store = MagicMock()
    run_skip = MagicMock(side_effect=RuntimeError("stress boom"))
    heartbeat = create_optimizer_heartbeat(
        worker_store=worker_store,
        run_optimize=MagicMock(),
        run_stop=MagicMock(),
        run_clean=MagicMock(),
        run_favorite=MagicMock(),
        run_skip_robustness=run_skip,
    )

    heartbeat.process_command(
        {
            "id": "cmd-skip-fail",
            "action": "skip_robustness",
            "payload": _skip_payload(),
        }
    )
    heartbeat.await_active_run()
    worker_store.mark_command_done.assert_called_with(
        command_id="cmd-skip-fail",
        status="failed",
        error="stress boom",
    )
