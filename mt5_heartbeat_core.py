"""PositionRelay optimizer heartbeat — poll API and run dashboard commands."""

from __future__ import annotations

import math
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from mt5_position_relay_api import PositionRelayOptimizerApi

DATE_PATTERN = re.compile(r"^\d{4}\.\d{2}\.\d{2}$")
TOKEN_PATTERN = re.compile(r"^[A-Z0-9._]+$")
ALLOWED_STRATEGIES = frozenset({"Classic", "Multi", "SwingHA"})
_ALLOWED_STRATEGIES_BY_LOWER = {s.lower(): s for s in ALLOWED_STRATEGIES}
ALLOWED_OPTIMIZATION_MODES = frozenset({"1", "2"})
ALLOWED_CURRENCIES = frozenset(
    {"USD", "EUR", "GBP", "CHF", "JPY", "AUD", "CAD", "NZD"}
)
SET_FILE_PATTERN = re.compile(r"^[A-Za-z0-9._-]+\.set$")

DEFAULT_DEPOSIT = "100000"
DEFAULT_CURRENCY = "USD"
DEFAULT_MAX_EQUITY_DRAWDOWN_PERCENT = 15.0


def scaled_max_equity_drawdown_percent(target: float) -> float:
    """Reject ceiling after RISK scaling: target × 1.12 (float; e.g. 4 → 4.48)."""
    return round(target * 1.12, 4)


@dataclass(frozen=True)
class OptimizeConfig:
    from_date: str
    to_date: str
    symbols: list[str]
    timeframes: list[str]
    strategies: list[str]
    optimization_mode: str
    resume: bool
    skip_robustness: bool = True
    deposit: str = DEFAULT_DEPOSIT
    currency: str = DEFAULT_CURRENCY
    max_equity_drawdown_percent: float = DEFAULT_MAX_EQUITY_DRAWDOWN_PERCENT


@dataclass(frozen=True)
class SkipRobustnessCommand:
    set_file: str
    symbol: str
    timeframe: str
    from_date: str
    to_date: str
    baseline_dd: float
    scaled_risk: float | None
    result_id: str
    deposit: str | None = None
    currency: str | None = None


LogFn = Callable[[str], None]
RunOptimizeFn = Callable[[OptimizeConfig, str], None]
RunStopFn = Callable[[], None]
RunCleanFn = Callable[[], None]
RunFavoriteFn = Callable[[str, str, bool], None]
RunPortfolioBuildFn = Callable[[], None]
RunSkipRobustnessFn = Callable[[SkipRobustnessCommand], None]


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
    max_equity_dd = scaled_max_equity_drawdown_percent(config.max_equity_drawdown_percent)
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
        "--deposit",
        config.deposit,
        "--currency",
        config.currency,
        "--target-equity-dd",
        str(config.max_equity_drawdown_percent),
        "--max-equity-dd",
        str(max_equity_dd),
    ]
    if config.resume:
        argv.append("--resume")
    if not config.skip_robustness:
        argv.append("--no-skip-robustness")
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
        strategy = _ALLOWED_STRATEGIES_BY_LOWER.get(value.strip().lower())
        if strategy is None:
            raise ValueError("start/resume strategies contains an invalid value")
        if strategy not in normalized:
            normalized.append(strategy)
    return normalized


def _read_validated_optimization_mode(payload: dict[str, Any]) -> str:
    value = payload.get("optimizationMode")
    if not isinstance(value, str) or value.strip() not in ALLOWED_OPTIMIZATION_MODES:
        raise ValueError("start/resume optimizationMode is invalid")
    return value.strip()


def _read_skip_robustness(payload: dict[str, Any]) -> bool:
    """Optional start/resume flag; default True (run auto top-5 Skip Robustness)."""
    if "skipRobustness" not in payload:
        return True
    value = payload.get("skipRobustness")
    if not isinstance(value, bool):
        raise ValueError("start/resume skipRobustness must be a boolean")
    return value


def _read_currency(payload: dict[str, Any]) -> str:
    if "currency" not in payload or payload.get("currency") in (None, ""):
        return DEFAULT_CURRENCY
    value = str(payload.get("currency") or "").strip().upper()
    if value not in ALLOWED_CURRENCIES:
        raise ValueError("start/resume currency is invalid")
    return value


def _read_deposit(payload: dict[str, Any]) -> str:
    if "deposit" not in payload or payload.get("deposit") is None:
        return DEFAULT_DEPOSIT
    raw = payload.get("deposit")
    if isinstance(raw, bool):
        raise ValueError("start/resume deposit must be a positive number")
    try:
        parsed = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("start/resume deposit must be a positive number") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError("start/resume deposit must be a positive number")
    if isinstance(raw, int) or (isinstance(raw, float) and raw == int(raw)):
        return str(int(parsed))
    text = str(raw).strip()
    if not text:
        raise ValueError("start/resume deposit must be a positive number")
    return text


def _read_max_equity_drawdown_percent(payload: dict[str, Any]) -> float:
    if "maxEquityDrawdownPercent" not in payload:
        return DEFAULT_MAX_EQUITY_DRAWDOWN_PERCENT
    raw = payload.get("maxEquityDrawdownPercent")
    if isinstance(raw, bool):
        raise ValueError("start/resume maxEquityDrawdownPercent must be a positive number")
    try:
        parsed = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "start/resume maxEquityDrawdownPercent must be a positive number"
        ) from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError("start/resume maxEquityDrawdownPercent must be a positive number")
    return parsed


def read_start_payload(*, action: str, payload: dict[str, Any]) -> OptimizeConfig:
    return OptimizeConfig(
        from_date=_read_validated_date(payload, "fromDate"),
        to_date=_read_validated_date(payload, "toDate"),
        symbols=_read_validated_tokens(payload, "symbols"),
        timeframes=_read_validated_tokens(payload, "timeframes"),
        strategies=_read_validated_strategies(payload),
        optimization_mode=_read_validated_optimization_mode(payload),
        resume=action == "resume",
        skip_robustness=_read_skip_robustness(payload),
        deposit=_read_deposit(payload),
        currency=_read_currency(payload),
        max_equity_drawdown_percent=_read_max_equity_drawdown_percent(payload),
    )


def _resolve_run_id(
    *,
    action: str,
    payload: dict[str, Any],
    random_id: Callable[[], str],
) -> str:
    if action != "resume":
        return random_id()
    run_id = payload.get("runId")
    if isinstance(run_id, str) and run_id.strip():
        return run_id.strip()
    return random_id()


def read_favorite_payload(payload: dict[str, Any]) -> tuple[str, str]:
    set_file = payload.get("setFile")
    symbol = payload.get("symbol")
    if not isinstance(set_file, str) or not SET_FILE_PATTERN.match(set_file.strip()):
        raise ValueError("favorite/unfavorite requires a valid setFile")
    if not isinstance(symbol, str):
        raise ValueError("favorite/unfavorite requires a valid symbol")
    normalized_symbol = symbol.strip().upper()
    if not TOKEN_PATTERN.match(normalized_symbol):
        raise ValueError("favorite/unfavorite requires a valid symbol")
    return set_file.strip(), normalized_symbol


def read_skip_robustness_payload(payload: dict[str, Any]) -> SkipRobustnessCommand:
    set_file, symbol = read_favorite_payload(payload)
    timeframe = str(payload.get("timeframe") or "").strip().upper()
    if not TOKEN_PATTERN.match(timeframe):
        raise ValueError("skip_robustness requires a valid timeframe")
    from_date = str(payload.get("fromDate") or "").strip()
    to_date = str(payload.get("toDate") or "").strip()
    if not DATE_PATTERN.match(from_date) or not DATE_PATTERN.match(to_date):
        raise ValueError("skip_robustness requires fromDate/toDate as YYYY.MM.DD")
    baseline_raw = payload.get("baselineDd")
    try:
        baseline_dd = float(baseline_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("skip_robustness requires baselineDd") from exc
    if not math.isfinite(baseline_dd):
        raise ValueError("skip_robustness requires baselineDd")
    if baseline_dd < 0:
        raise ValueError("skip_robustness requires baselineDd")
    scaled_raw = payload.get("scaledRisk")
    scaled_risk: float | None
    if scaled_raw is None:
        scaled_risk = None
    else:
        if isinstance(scaled_raw, bool):
            raise ValueError("skip_robustness scaledRisk must be a finite number")
        try:
            scaled_risk = float(scaled_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("skip_robustness scaledRisk must be a finite number") from exc
        if not math.isfinite(scaled_risk) or scaled_risk <= 0:
            raise ValueError("skip_robustness scaledRisk must be a finite number")
    result_id = str(payload.get("resultId") or "").strip()
    deposit: str | None = None
    currency: str | None = None
    if "deposit" in payload and payload.get("deposit") not in (None, ""):
        deposit = _read_deposit(payload)
    if "currency" in payload and payload.get("currency") not in (None, ""):
        currency = _read_currency(payload)
    return SkipRobustnessCommand(
        set_file=set_file,
        symbol=symbol,
        timeframe=timeframe,
        from_date=from_date,
        to_date=to_date,
        baseline_dd=baseline_dd,
        scaled_risk=scaled_risk,
        result_id=result_id,
        deposit=deposit,
        currency=currency,
    )


class OptimizerHeartbeat:
    def __init__(
        self,
        *,
        worker_store: WorkerStore,
        run_optimize: RunOptimizeFn,
        run_stop: RunStopFn,
        run_clean: RunCleanFn,
        run_favorite: RunFavoriteFn,
        run_portfolio_build: RunPortfolioBuildFn | None = None,
        run_skip_robustness: RunSkipRobustnessFn | None = None,
        log: LogFn | None = None,
        random_id: Callable[[], str] | None = None,
    ) -> None:
        self._worker_store = worker_store
        self._run_optimize = run_optimize
        self._run_stop = run_stop
        self._run_clean = run_clean
        self._run_favorite = run_favorite
        self._run_portfolio_build = run_portfolio_build or (lambda: None)
        self._run_skip_robustness = run_skip_robustness or (lambda _payload: None)
        self._log = log or (lambda _message: None)
        self._random_id = random_id or (lambda: str(uuid.uuid4()))
        self._child_busy = False
        self._active_run: Any = None


    def set_child_busy(self, busy: bool) -> None:
        self._child_busy = busy

    def touch_heartbeat(self) -> None:
        self._worker_store.touch_heartbeat(busy=self._child_busy)

    def await_active_run(self, timeout: float | None = None) -> None:
        active_run = self._active_run
        if active_run is None:
            return
        active_run.join(timeout=timeout)

    def shutdown(self) -> None:
        self._run_stop()
        # Bound join so skip-robustness / long MT5 waits cannot block shutdown for hours.
        self.await_active_run(timeout=30.0)

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
            if action == "skip_robustness":
                self._process_skip_robustness_command(command_id, payload)
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
            if action in {"start", "resume"}:
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
        try:
            self._run_portfolio_build()
        except Exception as error:  # noqa: BLE001 - keep favorite move result
            self._log(f"Portfolio refresh failed: {error}")
        self._worker_store.mark_command_done(command_id=command_id, status="done")

    def _process_skip_robustness_command(
        self,
        command_id: str,
        payload: dict[str, Any],
    ) -> None:
        if self._child_busy:
            raise RuntimeError("Cannot run skip_robustness while optimizer child is busy")
        parsed = read_skip_robustness_payload(payload)
        self._launch_skip_robustness(command_id, parsed)

    def _launch_skip_robustness(
        self,
        command_id: str,
        parsed: SkipRobustnessCommand,
    ) -> None:
        import threading

        def runner() -> None:
            try:
                self._run_skip_robustness(parsed)
                try:
                    self._run_portfolio_build()
                except Exception as error:  # noqa: BLE001
                    self._log(f"Portfolio refresh failed: {error}")
                self._worker_store.mark_command_done(
                    command_id=command_id,
                    status="done",
                )
            except Exception as error:  # noqa: BLE001
                message = str(error)
                self._log(f"Skip robustness exited abnormally: {message}")
                self._worker_store.mark_command_done(
                    command_id=command_id,
                    status="failed",
                    error=message,
                )
            finally:
                self.set_child_busy(False)
                self._active_run = None

        self.set_child_busy(True)
        thread = threading.Thread(target=runner, daemon=True)
        self._active_run = thread
        thread.start()

    def _process_start_command(
        self,
        command_id: str,
        action: str,
        payload: dict[str, Any],
    ) -> None:
        if self._child_busy:
            raise RuntimeError("Optimizer already running")

        config = read_start_payload(action=action, payload=payload)
        run_id = _resolve_run_id(
            action=action,
            payload=payload,
            random_id=self._random_id,
        )
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
    worker_store: PositionRelayOptimizerApi | WorkerStore,
    run_optimize: RunOptimizeFn,
    run_stop: RunStopFn,
    run_clean: RunCleanFn,
    run_favorite: RunFavoriteFn | None = None,
    run_portfolio_build: RunPortfolioBuildFn | None = None,
    run_skip_robustness: RunSkipRobustnessFn | None = None,
    log: LogFn | None = None,
    random_id: Callable[[], str] | None = None,
) -> OptimizerHeartbeat:
    return OptimizerHeartbeat(
        worker_store=worker_store,
        run_optimize=run_optimize,
        run_stop=run_stop,
        run_clean=run_clean,
        run_favorite=run_favorite or (lambda _set_file, _symbol, _unfavorite: None),
        run_portfolio_build=run_portfolio_build or (lambda: None),
        run_skip_robustness=run_skip_robustness,
        log=log,
        random_id=random_id,
    )
