"""TradeEcho optimizer heartbeat — poll API and run dashboard commands."""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from mt5_trade_echo_api import TradeEchoOptimizerApi

DATE_PATTERN = re.compile(r"^\d{4}\.\d{2}\.\d{2}$")
TOKEN_PATTERN = re.compile(r"^[A-Z0-9._]+$")
ALLOWED_STRATEGIES = frozenset({"Classic", "Multi"})
ALLOWED_OPTIMIZATION_MODES = frozenset({"1", "2"})
SET_FILE_PATTERN = re.compile(r"^[A-Za-z0-9._:\\/\\-]+$")


@dataclass(frozen=True)
class OptimizeConfig:
    from_date: str
    to_date: str
    symbols: list[str]
    timeframes: list[str]
    strategies: list[str]
    optimization_mode: str
    resume: bool


LogFn = Callable[[str], None]
RunOptimizeFn = Callable[[OptimizeConfig, str], None]
RunStopFn = Callable[[], None]
RunCleanFn = Callable[[], None]
RunFavoriteFn = Callable[[str, str, bool], None]


class WorkerStore(Protocol):
    def touch_heartbeat(self, *, busy: bool = False) -> None: ...
    def mark_command_done(
        self, *, command_id: str, status: str, error: str | None = None
    ) -> None: ...
    def start_run(
        self,
        *,
        command_id: str,
        from_date: str,
        to_date: str,
        symbols: list[str],
        timeframes: list[str],
        resume: bool,
        run_id: str,
    ) -> str: ...
    def set_worker_idle(self) -> None: ...
    def mark_running_runs_stopped(self) -> None: ...
    def fail_running_runs(self, *, error: str) -> None: ...
    def clear_optimization_data(self) -> None: ...
    def claim_pending_command(
        self, *, interruptible_only: bool = False
    ) -> dict[str, Any] | None: ...


def build_optimize_argv(config: OptimizeConfig, *, script_path: str, expert: str) -> list[str]:
    argv = [
        script_path,
        "--from-date",
        config.from_date,
        "--to-date",
        config.to_date,
        "--symbols",
        *config.symbols,
        "--timeframes",
        *config.timeframes,
        "--strategies",
        *config.strategies,
        "--optimization",
        config.optimization_mode,
        "--expert",
        expert,
    ]
    if config.resume:
        argv.append("--resume")
    return argv


def _read_required_value(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not value:
        raise ValueError(
            "start/resume requires fromDate, toDate, symbols, timeframes, "
            "strategies, and optimizationMode"
        )
    return str(value)


def _read_required_array(payload: dict[str, Any], key: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list) or len(value) == 0:
        raise ValueError(
            "start/resume requires fromDate, toDate, symbols, timeframes, "
            "strategies, and optimizationMode"
        )
    return [str(item) for item in value]


def _read_validated_date(payload: dict[str, Any], key: str) -> str:
    value = _read_required_value(payload, key)
    if not DATE_PATTERN.match(value):
        raise ValueError(f"start/resume {key} must be formatted YYYY.MM.DD")
    return value


def _read_validated_tokens(payload: dict[str, Any], key: str) -> list[str]:
    values = _read_required_array(payload, key)
    for value in values:
        if not TOKEN_PATTERN.match(value):
            raise ValueError(f"start/resume {key} contains an invalid value")
    return values


def _read_validated_strategies(payload: dict[str, Any]) -> list[str]:
    values = _read_required_array(payload, "strategies")
    normalized: list[str] = []
    for value in values:
        strategy = value.strip().capitalize()
        if strategy not in ALLOWED_STRATEGIES:
            raise ValueError("start/resume strategies contains an invalid value")
        if strategy not in normalized:
            normalized.append(strategy)
    return normalized


def _read_validated_optimization_mode(payload: dict[str, Any]) -> str:
    value = payload.get("optimizationMode")
    if not isinstance(value, str) or value.strip() not in ALLOWED_OPTIMIZATION_MODES:
        raise ValueError("start/resume optimizationMode is invalid")
    return value.strip()


def read_start_payload(*, action: str, payload: dict[str, Any]) -> OptimizeConfig:
    return OptimizeConfig(
        from_date=_read_validated_date(payload, "fromDate"),
        to_date=_read_validated_date(payload, "toDate"),
        symbols=_read_validated_tokens(payload, "symbols"),
        timeframes=_read_validated_tokens(payload, "timeframes"),
        strategies=_read_validated_strategies(payload),
        optimization_mode=_read_validated_optimization_mode(payload),
        resume=action == "resume",
    )


def read_favorite_payload(payload: dict[str, Any]) -> tuple[str, str]:
    set_file = payload.get("setFile")
    symbol = payload.get("symbol")
    if not isinstance(set_file, str) or not SET_FILE_PATTERN.match(set_file.strip()):
        raise ValueError("favorite/unfavorite requires a valid setFile")
    if not isinstance(symbol, str) or not TOKEN_PATTERN.match(symbol.strip()):
        raise ValueError("favorite/unfavorite requires a valid symbol")
    return set_file.strip(), symbol.strip().upper()


class OptimizerHeartbeat:
    def __init__(
        self,
        *,
        worker_store: WorkerStore,
        run_optimize: RunOptimizeFn,
        run_stop: RunStopFn,
        run_clean: RunCleanFn,
        run_favorite: RunFavoriteFn,
        log: LogFn | None = None,
        random_id: Callable[[], str] | None = None,
    ) -> None:
        self._worker_store = worker_store
        self._run_optimize = run_optimize
        self._run_stop = run_stop
        self._run_clean = run_clean
        self._run_favorite = run_favorite
        self._log = log or (lambda _message: None)
        self._random_id = random_id or (lambda: str(uuid.uuid4()))
        self._child_busy = False
        self._active_run: Any = None

    def set_child_busy(self, busy: bool) -> None:
        self._child_busy = busy

    def touch_heartbeat(self) -> None:
        self._worker_store.touch_heartbeat(busy=self._child_busy)

    def await_active_run(self) -> None:
        if self._active_run is None:
            return
        self._active_run.join()

    def shutdown(self) -> None:
        terminate = getattr(self._active_run, "terminate", None)
        if callable(terminate):
            terminate()
        self.await_active_run()

    def poll_commands(self) -> None:
        command = self._worker_store.claim_pending_command(
            interruptible_only=self._child_busy
        )
        if command:
            self.process_command(command)

    def process_command(self, command: dict[str, Any]) -> None:
        command_id = str(command["id"])
        action = str(command["action"])
        payload = command.get("payload")
        if not isinstance(payload, dict):
            payload = {}

        self._log(f"Processing command {action} ({command_id})")

        try:
            if action in {"start", "resume"}:
                self._process_start_command(command_id, action, payload)
                return
            if action == "stop":
                self._process_stop_command(command_id)
                return
            if action == "clean":
                self._process_clean_command(command_id)
                return
            if action in {"favorite", "unfavorite"}:
                self._process_favorite_command(command_id, action, payload)
                return
            raise ValueError(f"Unknown action: {action}")
        except Exception as error:  # noqa: BLE001 — mirror JS failCommand
            message = str(error)
            self._log(f"Command failed: {message}")
            self._worker_store.mark_command_done(
                command_id=command_id,
                status="failed",
                error=message,
            )
            self._worker_store.fail_running_runs(error=message)
            self._worker_store.set_worker_idle()

    def _process_favorite_command(
        self,
        command_id: str,
        action: str,
        payload: dict[str, Any],
    ) -> None:
        set_file, symbol = read_favorite_payload(payload)
        self._run_favorite(set_file, symbol, action == "unfavorite")
        self._worker_store.mark_command_done(command_id=command_id, status="done")

    def _process_start_command(
        self,
        command_id: str,
        action: str,
        payload: dict[str, Any],
    ) -> None:
        if self._child_busy:
            raise RuntimeError("Optimizer already running")

        config = read_start_payload(action=action, payload=payload)
        run_id = self._random_id()
        self._worker_store.start_run(
            command_id=command_id,
            from_date=config.from_date,
            to_date=config.to_date,
            symbols=config.symbols,
            timeframes=config.timeframes,
            resume=config.resume,
            run_id=run_id,
        )
        self._worker_store.mark_command_done(command_id=command_id, status="done")
        self._launch_optimizer(config, run_id)

    def _launch_optimizer(self, config: OptimizeConfig, run_id: str) -> None:
        import threading

        def runner() -> None:
            try:
                self._run_optimize(config, run_id)
            except Exception as error:  # noqa: BLE001
                self._log(f"Optimizer run exited abnormally: {error}")
            finally:
                self.set_child_busy(False)
                self._active_run = None
                self._worker_store.set_worker_idle()

        self.set_child_busy(True)
        thread = threading.Thread(target=runner, daemon=True)
        self._active_run = thread
        thread.start()

    def _run_stop_steps(self) -> None:
        self._run_stop()
        self._worker_store.mark_running_runs_stopped()
        self._worker_store.set_worker_idle()

    def _process_stop_command(self, command_id: str) -> None:
        self._run_stop_steps()
        self._worker_store.mark_command_done(command_id=command_id, status="done")

    def _process_clean_command(self, command_id: str) -> None:
        self._run_stop_steps()
        self._run_clean()
        self._worker_store.clear_optimization_data()
        self._worker_store.mark_command_done(command_id=command_id, status="done")


def create_optimizer_heartbeat(
    *,
    worker_store: TradeEchoOptimizerApi | WorkerStore,
    run_optimize: RunOptimizeFn,
    run_stop: RunStopFn,
    run_clean: RunCleanFn,
    run_favorite: RunFavoriteFn | None = None,
    log: LogFn | None = None,
    random_id: Callable[[], str] | None = None,
) -> OptimizerHeartbeat:
    return OptimizerHeartbeat(
        worker_store=worker_store,
        run_optimize=run_optimize,
        run_stop=run_stop,
        run_clean=run_clean,
        run_favorite=run_favorite or (lambda _set_file, _symbol, _unfavorite: None),
        log=log,
        random_id=random_id,
    )
