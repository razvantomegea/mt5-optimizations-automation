#!/usr/bin/env python3
"""Poll TradeEcho API and run dashboard optimizer commands on this machine."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from mt5_heartbeat_core import (
    OptimizeConfig,
    OptimizerHeartbeat,
    SkipRobustnessCommand,
    build_optimize_argv,
    create_optimizer_heartbeat,
)
from mt5_paths import DEFAULT_BEST_DIR, DEFAULT_FAVORITES_DIR, resolve_terminal
from mt5_env import load_repo_env
from mt5_portfolio_favorites import refresh_all_favorites_portfolio
from mt5_trade_echo_api import TradeEchoOptimizerApi
from mt5_trade_echo_auth import assert_optimizer_access
from mt5_workspace import PACKAGE_ROOT

HEARTBEAT_MS = 10_000
BATCH_SCRIPT = PACKAGE_ROOT / "mt5_batch_optimize.py"
STOP_SCRIPT = PACKAGE_ROOT / "mt5_stop.py"
CLEAN_SCRIPT = PACKAGE_ROOT / "mt5_clean_cache.py"
FAVORITE_SCRIPT = PACKAGE_ROOT / "mt5_favorite_strategy.py"


def log(message: str) -> None:
    print(f"[mt5-heartbeat] {time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime())}Z {message}")


class HeartbeatHost:
    def __init__(self, heartbeat: OptimizerHeartbeat) -> None:
        self._heartbeat = heartbeat
        self._child_process: subprocess.Popen[str] | None = None
        self.shutting_down = False

    def set_child_process(self, process: subprocess.Popen[str] | None) -> None:
        self._child_process = process
        self._heartbeat.set_child_busy(process is not None)

    def _resolve_expert(self) -> str:
        expert = os.environ.get("MT5_EXPERT", "").strip()
        if not expert:
            raise RuntimeError("MT5_EXPERT is required in .env for dashboard Start/Resume")
        return expert

    def run_optimize(self, config: OptimizeConfig, run_id: str) -> None:
        argv = [
            sys.executable,
            *build_optimize_argv(
                config,
                script_path=str(BATCH_SCRIPT),
                expert=self._resolve_expert(),
            ),
        ]

        process = subprocess.Popen(argv, cwd=str(PACKAGE_ROOT), env=self._optimizer_env(run_id))
        self.set_child_process(process)
        return_code = process.wait()
        self.set_child_process(None)
        if return_code != 0:
            raise RuntimeError(f"mt5_batch_optimize.py exited with code {return_code}")

    def run_stop(self) -> None:
        result = subprocess.run(
            [sys.executable, str(STOP_SCRIPT)],
            cwd=str(PACKAGE_ROOT),
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"mt5_stop.py exited with code {result.returncode}")

    def run_clean(self) -> None:
        result = subprocess.run(
            [sys.executable, str(CLEAN_SCRIPT), "--no-stop"],
            cwd=str(PACKAGE_ROOT),
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"mt5_clean_cache.py exited with code {result.returncode}")

    def run_favorite(self, set_file: str, symbol: str, unfavorite: bool) -> None:
        favorites_set = DEFAULT_FAVORITES_DIR / "sets" / set_file
        best_set = DEFAULT_BEST_DIR / "sets" / set_file

        if unfavorite:
            if favorites_set.is_file():
                source_set = favorites_set
            elif best_set.is_file():
                log(f"Already in Best/: {set_file}")
                return
            else:
                raise RuntimeError(f"Set file not found for unfavorite: {set_file}")
        elif favorites_set.is_file():
            log(f"Already in Favorites/: {set_file}")
            return
        elif best_set.is_file():
            source_set = best_set
        else:
            raise RuntimeError(
                f"Set file not found under {DEFAULT_BEST_DIR / 'sets'}: {set_file}"
            )

        argv = [
            sys.executable,
            str(FAVORITE_SCRIPT),
            "--set-file",
            str(source_set),
            "--symbol",
            symbol,
            "--best-dir",
            str(DEFAULT_BEST_DIR),
            "--favorites-dir",
            str(DEFAULT_FAVORITES_DIR),
        ]
        if unfavorite:
            argv.append("--unfavorite")

        result = subprocess.run(
            argv,
            cwd=str(PACKAGE_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.stdout.strip():
            for line in result.stdout.strip().splitlines():
                log(line.strip())
        if result.stderr.strip():
            log(result.stderr.strip())
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(detail or "Favorite script failed")

    def run_skip_robustness(self, command: SkipRobustnessCommand) -> None:
        from mt5_set_files import resolve_mt5_data_dir
        from mt5_skip_robustness import (
            DEFAULT_CURRENCY,
            DEFAULT_DEPOSIT,
            DEFAULT_LEVERAGE,
            DEFAULT_SKIP_ROBUSTNESS_TIMEOUT_SEC,
            run_skip_robustness_job,
        )

        best_set = DEFAULT_BEST_DIR / "sets" / command.set_file
        fav_set = DEFAULT_FAVORITES_DIR / "sets" / command.set_file
        if best_set.is_file():
            source = best_set
        elif fav_set.is_file():
            source = fav_set
        else:
            raise RuntimeError(f"Set file not found for skip robustness: {command.set_file}")

        terminal = resolve_terminal()
        assert terminal is not None
        portable = os.environ.get("MT5_PORTABLE", "").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        data_dir = resolve_mt5_data_dir(
            terminal=terminal,
            portable=portable,
            mt5_data=os.environ.get("MT5_DATA") or None,
        )

        gate = run_skip_robustness_job(
            set_file=source,
            symbol=command.symbol,
            timeframe=command.timeframe,
            from_date=command.from_date,
            to_date=command.to_date,
            baseline_dd_pct=command.baseline_dd,
            scaled_risk=command.scaled_risk,
            expert=self._resolve_expert(),
            best_dir=DEFAULT_BEST_DIR,
            favorites_dir=DEFAULT_FAVORITES_DIR,
            work_dir=PACKAGE_ROOT,
            terminal=terminal,
            install_dir=terminal.parent,
            data_dir=data_dir,
            portable=portable,
            result_id=command.result_id,
            unfavorite_on_fail=True,
            deposit=DEFAULT_DEPOSIT,
            currency=DEFAULT_CURRENCY,
            leverage=DEFAULT_LEVERAGE,
            timeout_seconds=DEFAULT_SKIP_ROBUSTNESS_TIMEOUT_SEC,
        )
        if gate.incomplete:
            status = "INCOMPLETE"
        elif gate.passed:
            status = "PASS"
        else:
            status = "FAIL"
        log(
            f"Skip robustness {status} combos={gate.combo_count} "
            f"max_DD={gate.max_combo_dd_pct} ceiling={gate.ceiling_dd_pct}"
        )

    def run_portfolio_build(self) -> None:
        load_repo_env()
        assert_optimizer_access()
        api = TradeEchoOptimizerApi.from_env()
        result = refresh_all_favorites_portfolio(api)
        if result is None:
            log("Portfolio cleared (no favorites remain)")
            return
        log(
            "Portfolio updated: "
            f"{result['strategy_count']} strategies, "
            f"{result['total_trades']} trades"
        )

    def run_cycle(self) -> None:
        try:
            self._heartbeat.touch_heartbeat()
            self._heartbeat.poll_commands()
        except Exception as error:  # noqa: BLE001
            log(f"Heartbeat error: {error}")

    def shutdown(self) -> None:
        self.shutting_down = True
        if self._child_process is not None:
            self._child_process.terminate()
        self._heartbeat.shutdown()

    @staticmethod
    def _optimizer_env(run_id: str) -> dict[str, str]:
        env = os.environ.copy()
        env["OPTIMIZATION_RUN_ID"] = run_id
        return env


def build_host() -> HeartbeatHost:
    api = TradeEchoOptimizerApi.from_env()
    host_holder: dict[str, HeartbeatHost] = {}

    def run_optimize(config: OptimizeConfig, run_id: str) -> None:
        host_holder["host"].run_optimize(config, run_id)

    def run_stop() -> None:
        host_holder["host"].run_stop()

    def run_clean() -> None:
        host_holder["host"].run_clean()

    def run_favorite(set_file: str, symbol: str, unfavorite: bool) -> None:
        host_holder["host"].run_favorite(set_file, symbol, unfavorite)

    def run_skip_robustness(command: SkipRobustnessCommand) -> None:
        host_holder["host"].run_skip_robustness(command)

    def run_portfolio_build() -> None:
        host_holder["host"].run_portfolio_build()

    heartbeat = create_optimizer_heartbeat(
        worker_store=api,
        run_optimize=run_optimize,
        run_stop=run_stop,
        run_clean=run_clean,
        run_favorite=run_favorite,
        run_portfolio_build=run_portfolio_build,
        run_skip_robustness=run_skip_robustness,
        log=log,
    )
    host = HeartbeatHost(heartbeat)
    host_holder["host"] = host
    return host


def main() -> int:
    host = build_host()

    def handle_sigint(_signum: int, _frame: object) -> None:
        log("Shutting down")
        host.shutdown()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, handle_sigint)

    log("Starting optimizer heartbeat (10s poll)")
    while not host.shutting_down:
        host.run_cycle()
        if host.shutting_down:
            break
        time.sleep(HEARTBEAT_MS / 1000)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
