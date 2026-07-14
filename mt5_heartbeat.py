#!/usr/bin/env python3
"""Poll TradeEcho API and run dashboard optimizer commands on this machine."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from mt5_heartbeat_core import OptimizeConfig, OptimizerHeartbeat, create_optimizer_heartbeat
from mt5_paths import DEFAULT_BEST_DIR, DEFAULT_FAVORITES_DIR
from mt5_trade_echo_api import TradeEchoOptimizerApi
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
            str(BATCH_SCRIPT),
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
            self._resolve_expert(),
        ]
        if config.resume:
            argv.append("--resume")

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
        argv = [
            sys.executable,
            str(FAVORITE_SCRIPT),
            "--set-file",
            set_file,
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
        if result.stderr.strip():
            log(result.stderr.strip())
        if result.returncode != 0 or not result.stdout.strip():
            raise RuntimeError("Favorite script did not move any files")

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

    heartbeat = create_optimizer_heartbeat(
        worker_store=api,
        run_optimize=run_optimize,
        run_stop=run_stop,
        run_clean=run_clean,
        run_favorite=run_favorite,
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
