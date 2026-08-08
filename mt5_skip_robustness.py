"""Skip Robustness: calendar-skip stress on Survivors (60 SKIP_* combos).

See CONTEXT.md — Skip Robustness Optimization / Gate / Status.
"""

from __future__ import annotations

import argparse
import csv
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from mt5_env import load_repo_env

load_repo_env()

from mt5_ea_inputs import (
    EXPECTED_SKIP_COMBINATIONS,
    RISK_INPUT_NAME,
    SKIP_MONTH_GRID,
    SKIP_MONTH_INPUT,
    SKIP_TRADE_DAY_GRID,
    SKIP_TRADE_DAY_INPUT,
)
from mt5_opt_report import (
    ColumnOverrides,
    resolve_column_mapping,
    to_float,
    worksheet_rows,
)
from mt5_paths import DEFAULT_BEST_DIR, DEFAULT_FAVORITES_DIR, resolve_terminal
from mt5_report_completeness import REPORT_ARTIFACT_SUFFIXES
from mt5_set_files import read_set_file_text, resolve_mt5_data_dir
from mt5_survivor_rank import row_is_survivor, validation_rank_key
from mt5_tester_runtime import (
    build_tester_report_target,
    format_set_param_value,
    resolve_report_path,
    start_terminal,
    write_ini,
)
from mt5_workspace import PACKAGE_ROOT

SKIP_ROBUSTNESS_DD_MARGIN = 1.0
SKIP_ROBUSTNESS_KEEP_LIMIT = 5
ROBUSTNESS_FAILED_REASON = "robustness_failed"
ROBUSTNESS_INCOMPLETE_REASON = "robustness_incomplete"
SKIP_ROBUSTNESS_PASS_KEY = "skip_robustness_pass"
SKIP_ROBUSTNESS_RUN_KEY = "skip_robustness_run"
DEFAULT_LEVERAGE = "1:33"
DEFAULT_DEPOSIT = "100000"
DEFAULT_CURRENCY = "USD"
# Complete 60-combo real-ticks stress can run for hours; must stay finite for heartbeat.
DEFAULT_SKIP_ROBUSTNESS_TIMEOUT_SEC = 14_400.0
EXIT_INCOMPLETE = 3

# Header-only CSV when survivors list is empty (README / Best summary contract).
SUMMARY_CSV_FIELDNAMES = [
    "pass_id",
    "symbol",
    "timeframe",
    "profile",
    "validation_pass",
    "validation_score",
    "validation_recovery",
    "validation_sharpe",
    "validation_cagr_pct",
    "realticks_equity_dd_pct",
    "baseline_risk",
    "scaled_risk",
    "keep",
    "reject_reason",
    "set_file",
    SKIP_ROBUSTNESS_RUN_KEY,
    SKIP_ROBUSTNESS_PASS_KEY,
]


@dataclass(frozen=True)
class SkipRobustnessGateResult:
    passed: bool
    baseline_dd_pct: float
    ceiling_dd_pct: float
    combo_count: int
    max_combo_dd_pct: float | None
    reject_reason: str
    incomplete: bool = False


def skip_robustness_ceiling(
    baseline_dd_pct: float,
    *,
    margin: float = SKIP_ROBUSTNESS_DD_MARGIN,
) -> float:
    return baseline_dd_pct + margin


def evaluate_skip_robustness_gate(
    *,
    combo_equity_dds: list[float],
    baseline_dd_pct: float,
    margin: float = SKIP_ROBUSTNESS_DD_MARGIN,
    expected_combos: int = EXPECTED_SKIP_COMBINATIONS,
) -> SkipRobustnessGateResult:
    ceiling = skip_robustness_ceiling(baseline_dd_pct, margin=margin)
    if len(combo_equity_dds) < expected_combos:
        return SkipRobustnessGateResult(
            passed=False,
            incomplete=True,
            baseline_dd_pct=baseline_dd_pct,
            ceiling_dd_pct=ceiling,
            combo_count=len(combo_equity_dds),
            max_combo_dd_pct=max(combo_equity_dds) if combo_equity_dds else None,
            reject_reason=ROBUSTNESS_INCOMPLETE_REASON,
        )
    max_dd = max(combo_equity_dds)
    passed = all(dd <= ceiling for dd in combo_equity_dds)
    return SkipRobustnessGateResult(
        passed=passed,
        incomplete=False,
        baseline_dd_pct=baseline_dd_pct,
        ceiling_dd_pct=ceiling,
        combo_count=len(combo_equity_dds),
        max_combo_dd_pct=max_dd,
        reject_reason="" if passed else ROBUSTNESS_FAILED_REASON,
    )


def select_auto_stress_survivors(
    rows: list[dict[str, Any]],
    *,
    limit: int = SKIP_ROBUSTNESS_KEEP_LIMIT,
) -> list[dict[str, Any]]:
    survivors = [r for r in rows if row_is_survivor(r)]
    survivors.sort(key=validation_rank_key, reverse=True)
    return survivors[: min(limit, len(survivors))]


def set_supports_skip_robustness(set_path: Path) -> bool:
    """True when the .set declares both configured skip-day and skip-month inputs."""
    keys: set[str] = set()
    for raw in read_set_file_text(set_path).splitlines():
        line = raw.strip()
        if not line or line.startswith(";") or "=" not in line:
            continue
        keys.add(line.split("=", 1)[0].strip())
    return SKIP_TRADE_DAY_INPUT in keys and SKIP_MONTH_INPUT in keys


def write_skip_stress_set(
    *,
    source_set: Path,
    dest_set: Path,
    scaled_risk: float | None,
) -> None:
    """Freeze winning params; enable only skip day / month grids."""
    lines: list[str] = []
    seen_skip_day = False
    seen_skip_month = False
    seen_risk = False
    for raw in read_set_file_text(source_set).splitlines():
        line = raw.strip()
        if not line or line.startswith(";") or "=" not in line:
            if line.startswith(";"):
                lines.append(raw.rstrip("\r\n"))
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key == SKIP_TRADE_DAY_INPUT:
            lines.append(f"{SKIP_TRADE_DAY_INPUT}={SKIP_TRADE_DAY_GRID}")
            seen_skip_day = True
            continue
        if key == SKIP_MONTH_INPUT:
            lines.append(f"{SKIP_MONTH_INPUT}={SKIP_MONTH_GRID}")
            seen_skip_month = True
            continue
        if key == RISK_INPUT_NAME and scaled_risk is not None:
            lines.append(f"{RISK_INPUT_NAME}={format_set_param_value(scaled_risk)}")
            seen_risk = True
            continue
        effective = value.split("||", 1)[0].strip()
        lines.append(f"{key}={effective}")

    if not seen_skip_day:
        lines.append(f"{SKIP_TRADE_DAY_INPUT}={SKIP_TRADE_DAY_GRID}")
    if not seen_skip_month:
        lines.append(f"{SKIP_MONTH_INPUT}={SKIP_MONTH_GRID}")
    if scaled_risk is not None and not seen_risk:
        lines.append(f"{RISK_INPUT_NAME}={format_set_param_value(scaled_risk)}")

    content = "\r\n".join(lines) + "\r\n"
    dest_set.parent.mkdir(parents=True, exist_ok=True)
    dest_set.write_bytes(b"\xff\xfe" + content.encode("utf-16-le"))


def load_optimization_equity_dds(xml_path: Path) -> list[float]:
    _title, headers, records = worksheet_rows(xml_path)
    mapping = resolve_column_mapping(headers, ColumnOverrides())
    if not mapping.equity_dd:
        raise RuntimeError(
            f"Equity DD column unresolved in {xml_path.name}; "
            "cannot evaluate skip robustness without drawdown measurements"
        )
    dds: list[float] = []
    for rec in records:
        value = to_float(rec.get(mapping.equity_dd))
        if value is None:
            continue
        dds.append(float(value))
    return dds


def _row_truthy(row: dict[str, Any], key: str) -> bool:
    value = row.get(key)
    return value is True or value in ("True", "true", "1", 1)


def apply_skip_robustness_outcome(
    row: dict[str, Any],
    gate: SkipRobustnessGateResult,
) -> dict[str, Any]:
    updated = dict(row)
    if gate.incomplete:
        # Incomplete stress must not mutate keep / pass / reject_reason.
        return updated

    updated[SKIP_ROBUSTNESS_RUN_KEY] = True
    if gate.passed:
        updated[SKIP_ROBUSTNESS_PASS_KEY] = True
        updated["validation_pass"] = True
        return updated

    updated[SKIP_ROBUSTNESS_PASS_KEY] = False
    updated["validation_pass"] = False
    updated["keep"] = False
    existing = str(updated.get("reject_reason") or "").strip()
    reasons = [r for r in existing.split(",") if r.strip()]
    reason = gate.reject_reason or ROBUSTNESS_FAILED_REASON
    if reason not in reasons:
        reasons.append(reason)
    updated["reject_reason"] = ",".join(reasons)
    return updated


def _read_csv_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _write_csv_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=SUMMARY_CSV_FIELDNAMES, extrasaction="ignore")
            writer.writeheader()
        return
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                fieldnames.append(key)
                seen.add(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def update_best_csvs_for_outcome(
    *,
    best_dir: Path,
    set_file: Path,
    gate: SkipRobustnessGateResult,
) -> dict[str, Any] | None:
    summary_csv = best_dir / "best_summary.csv"
    survivors_csv = best_dir / "best_survivors.csv"
    set_name = set_file.name
    set_stem = set_file.stem

    summary_rows = _read_csv_rows(summary_csv)
    matched: dict[str, Any] | None = None
    new_summary: list[dict[str, Any]] = []
    for row in summary_rows:
        row_set = str(row.get("set_file") or "")
        row_name = Path(row_set).name if row_set else ""
        row_stem = Path(row_set).stem if row_set else ""
        if row_name == set_name or row_stem == set_stem:
            matched = apply_skip_robustness_outcome(row, gate)
            new_summary.append(matched)
        else:
            new_summary.append(row)

    if matched is None:
        return None

    _write_csv_rows(summary_csv, new_summary)
    survivors = [r for r in new_summary if _row_truthy(r, "keep")]
    _write_csv_rows(survivors_csv, survivors)
    return matched


def remove_best_artifacts(
    *,
    best_dir: Path,
    set_file: Path,
    symbol: str,
) -> None:
    stem = set_file.stem
    target_set = best_dir / "sets" / set_file.name
    if target_set.is_file():
        try:
            target_set.unlink()
        except PermissionError:
            pass
    report_dir = best_dir / "reports" / symbol
    if report_dir.is_dir():
        for path in report_dir.iterdir():
            if not path.is_file():
                continue
            # Exact stem token in filename (not substring of a longer pass id).
            name = path.name
            if (
                name == stem
                or name.startswith(f"{stem}.")
                or name.startswith(f"{stem}_")
                or name.startswith(f"{stem}-")
            ):
                try:
                    path.unlink()
                except PermissionError:
                    continue


def delete_report_artifacts(report_base: Path) -> list[Path]:
    """Remove existing tester report files for a stem so a failed run cannot reuse stale XML."""
    deleted: list[Path] = []
    for suffix in REPORT_ARTIFACT_SUFFIXES:
        path = Path(str(report_base) + suffix)
        if not path.is_file():
            continue
        try:
            path.unlink()
        except PermissionError:
            continue
        deleted.append(path)
    return deleted


def run_skip_stress_optimization(
    *,
    terminal: Path,
    install_dir: Path,
    data_dir: Path,
    work_dir: Path,
    expert: str,
    stress_set_name: str,
    symbol: str,
    timeframe: str,
    from_date: str,
    to_date: str,
    deposit: str,
    currency: str,
    leverage: str,
    portable: bool,
    timeout_seconds: float,
) -> Path:
    stem = f"skiprob_{Path(stress_set_name).stem}"
    # Clear both layout targets before build — a prior run may have used the short path.
    delete_report_artifacts(work_dir / "reports" / stem)
    delete_report_artifacts(data_dir / "te_bt" / stem)
    report_base, report_rel = build_tester_report_target(
        data_dir=data_dir,
        work_dir=work_dir,
        stem=stem,
    )
    delete_report_artifacts(report_base)
    configs_dir = work_dir / "configs"
    configs_dir.mkdir(parents=True, exist_ok=True)
    ini_path = configs_dir / f"{stem}.ini"
    cfg = {
        "Expert": expert,
        "ExpertParameters": stress_set_name,
        "Symbol": symbol,
        "Period": timeframe,
        "Model": "4",
        "ExecutionMode": "-1",
        "Optimization": "1",
        "OptimizationCriterion": "0",
        "FromDate": from_date,
        "ToDate": to_date,
        "ForwardMode": "0",
        "Report": report_rel,
        "ReplaceReport": "1",
        "ShutdownTerminal": "1",
        "Deposit": deposit,
        "Currency": currency,
        "Leverage": leverage,
        "UseLocal": "1",
        "UseRemote": "0",
        "UseCloud": "0",
        "Visual": "0",
    }
    write_ini(ini_path, cfg)
    cmd = [str(terminal)]
    if portable:
        cmd.append("/portable")
    cmd.append(f"/config:{ini_path}")

    proc = start_terminal(cmd, cwd=install_dir)
    try:
        proc.wait(timeout=timeout_seconds if timeout_seconds > 0 else None)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise RuntimeError(
            f"Skip robustness opt timed out after {timeout_seconds:.0f}s"
        ) from None

    xml_candidate = Path(str(report_base) + ".xml")
    if xml_candidate.is_file():
        return xml_candidate
    try:
        report = resolve_report_path(report_base)
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Skip robustness report missing (exit={proc.returncode})"
        ) from exc
    if report.suffix.lower() == ".xml":
        return report
    xml_sibling = report.with_suffix(".xml")
    if xml_sibling.is_file():
        return xml_sibling
    raise RuntimeError(f"Skip robustness XML not found next to {report}")


@dataclass(frozen=True)
class SkipRobustnessRunParams:
    set_file: Path
    symbol: str
    timeframe: str
    from_date: str
    to_date: str
    baseline_dd_pct: float
    scaled_risk: float | None
    expert: str
    best_dir: Path
    favorites_dir: Path
    work_dir: Path
    terminal: Path
    install_dir: Path
    data_dir: Path
    deposit: str = DEFAULT_DEPOSIT
    currency: str = DEFAULT_CURRENCY
    leverage: str = DEFAULT_LEVERAGE
    portable: bool = False
    timeout_seconds: float = 0.0
    margin: float = SKIP_ROBUSTNESS_DD_MARGIN


def unfavorite_survivor_set(
    *,
    set_name: str,
    symbol: str,
    best_dir: Path,
    favorites_dir: Path,
) -> None:
    """Unfavorite a set via mt5_favorite_strategy when present under Favorites/sets."""
    script = PACKAGE_ROOT / "mt5_favorite_strategy.py"
    fav = favorites_dir / "sets" / set_name
    if not fav.is_file():
        return
    subprocess.run(
        [
            sys.executable,
            str(script),
            "--set-file",
            str(fav),
            "--symbol",
            symbol,
            "--best-dir",
            str(best_dir),
            "--favorites-dir",
            str(favorites_dir),
            "--unfavorite",
        ],
        cwd=str(PACKAGE_ROOT),
        check=False,
    )


def stress_one_survivor(
    params: SkipRobustnessRunParams,
    *,
    on_fail_unfavorite: Callable[[str, str], None] | None = None,
) -> tuple[SkipRobustnessGateResult, dict[str, Any] | None]:
    if not params.set_file.is_file():
        raise FileNotFoundError(f"Survivor set not found: {params.set_file}")

    tester_dir = params.data_dir / "MQL5" / "Profiles" / "Tester"
    tester_dir.mkdir(parents=True, exist_ok=True)
    stress_name = f"skiprob_{params.set_file.stem}.set"
    stress_path = params.work_dir / "skip_robustness" / stress_name
    write_skip_stress_set(
        source_set=params.set_file,
        dest_set=stress_path,
        scaled_risk=params.scaled_risk,
    )
    shutil.copy2(stress_path, tester_dir / stress_name)

    xml_path = run_skip_stress_optimization(
        terminal=params.terminal,
        install_dir=params.install_dir,
        data_dir=params.data_dir,
        work_dir=params.work_dir,
        expert=params.expert,
        stress_set_name=stress_name,
        symbol=params.symbol,
        timeframe=params.timeframe,
        from_date=params.from_date,
        to_date=params.to_date,
        deposit=params.deposit,
        currency=params.currency,
        leverage=params.leverage,
        portable=params.portable,
        timeout_seconds=params.timeout_seconds,
    )
    dds = load_optimization_equity_dds(xml_path)
    gate = evaluate_skip_robustness_gate(
        combo_equity_dds=dds,
        baseline_dd_pct=params.baseline_dd_pct,
        margin=params.margin,
    )
    if gate.incomplete:
        # Incomplete opt — do not clear keep / survivors / favorites / Best artifacts.
        return gate, None

    updated = update_best_csvs_for_outcome(
        best_dir=params.best_dir,
        set_file=params.set_file,
        gate=gate,
    )
    if not gate.passed:
        if on_fail_unfavorite is not None:
            on_fail_unfavorite(params.set_file.name, params.symbol)
        remove_best_artifacts(
            best_dir=params.best_dir,
            set_file=params.set_file,
            symbol=params.symbol,
        )
    return gate, updated


def stress_auto_top_survivors(
    *,
    rows: list[dict[str, Any]],
    from_date: str,
    to_date: str,
    expert: str,
    best_dir: Path,
    work_dir: Path,
    terminal: Path,
    install_dir: Path,
    data_dir: Path,
    deposit: str,
    currency: str,
    leverage: str,
    portable: bool,
    timeout_seconds: float,
    db_reporter: Any | None = None,
    limit: int = SKIP_ROBUSTNESS_KEEP_LIMIT,
) -> list[SkipRobustnessGateResult]:
    selected = select_auto_stress_survivors(rows, limit=limit)
    results: list[SkipRobustnessGateResult] = []
    for row in selected:
        set_path = Path(str(row.get("set_file") or ""))
        if not set_path.is_file():
            print(f"  Skip robustness: missing set {set_path}", file=sys.stderr)
            continue
        if not set_supports_skip_robustness(set_path):
            print(
                f"  Skip robustness: skipped {set_path.name} "
                f"(missing {SKIP_TRADE_DAY_INPUT}/{SKIP_MONTH_INPUT})",
                file=sys.stderr,
            )
            continue
        baseline = to_float(row.get("realticks_equity_dd_pct"))
        if baseline is None:
            print(
                f"  Skip robustness: missing realticks_equity_dd_pct for {set_path.name}",
                file=sys.stderr,
            )
            continue
        scaled = to_float(row.get("scaled_risk"))
        symbol = str(row.get("symbol") or "")
        timeframe = str(row.get("timeframe") or "")
        print(
            f"  Skip robustness: {symbol} {timeframe} {set_path.name} "
            f"baseline_DD={baseline:.4f}% "
            f"ceiling={baseline + SKIP_ROBUSTNESS_DD_MARGIN:.4f}%"
        )

        def _on_fail_unfavorite(set_name: str, sym: str) -> None:
            unfavorite_survivor_set(
                set_name=set_name,
                symbol=sym,
                best_dir=best_dir,
                favorites_dir=DEFAULT_FAVORITES_DIR,
            )

        gate, updated = stress_one_survivor(
            SkipRobustnessRunParams(
                set_file=set_path,
                symbol=symbol,
                timeframe=timeframe,
                from_date=from_date,
                to_date=to_date,
                baseline_dd_pct=baseline,
                scaled_risk=scaled,
                expert=expert,
                best_dir=best_dir,
                favorites_dir=DEFAULT_FAVORITES_DIR,
                work_dir=work_dir,
                terminal=terminal,
                install_dir=install_dir,
                data_dir=data_dir,
                deposit=deposit,
                currency=currency,
                leverage=leverage,
                portable=portable,
                timeout_seconds=timeout_seconds,
            ),
            on_fail_unfavorite=_on_fail_unfavorite,
        )
        if gate.incomplete:
            status = "INCOMPLETE"
        elif gate.passed:
            status = "PASS"
        else:
            status = "FAIL"
        print(
            f"    {status} combos={gate.combo_count} max_DD={gate.max_combo_dd_pct}"
        )
        # Incomplete = no outcome write (dashboard stays retryable).
        if db_reporter is not None and not gate.incomplete:
            try:
                db_reporter.apply_skip_robustness(
                    row=updated if updated is not None else row,
                    gate=gate,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"    WARNING: API update failed: {exc}", file=sys.stderr)
        results.append(gate)
    return results


def run_skip_robustness_job(
    *,
    set_file: Path,
    symbol: str,
    timeframe: str,
    from_date: str,
    to_date: str,
    baseline_dd_pct: float,
    scaled_risk: float | None,
    expert: str,
    best_dir: Path,
    favorites_dir: Path,
    work_dir: Path,
    terminal: Path,
    install_dir: Path,
    data_dir: Path,
    deposit: str = DEFAULT_DEPOSIT,
    currency: str = DEFAULT_CURRENCY,
    leverage: str = DEFAULT_LEVERAGE,
    portable: bool = False,
    timeout_seconds: float = 0.0,
    result_id: str = "",
    unfavorite_on_fail: bool = True,
) -> SkipRobustnessGateResult:
    """Shared entry for CLI and heartbeat host (no subprocess)."""

    def _on_fail_unfavorite(set_name: str, sym: str) -> None:
        if not unfavorite_on_fail:
            return
        unfavorite_survivor_set(
            set_name=set_name,
            symbol=sym,
            best_dir=best_dir,
            favorites_dir=favorites_dir,
        )

    gate, _updated = stress_one_survivor(
        SkipRobustnessRunParams(
            set_file=set_file,
            symbol=symbol,
            timeframe=timeframe,
            from_date=from_date,
            to_date=to_date,
            baseline_dd_pct=baseline_dd_pct,
            scaled_risk=scaled_risk,
            expert=expert,
            best_dir=best_dir,
            favorites_dir=favorites_dir,
            work_dir=work_dir,
            terminal=terminal,
            install_dir=install_dir,
            data_dir=data_dir,
            deposit=deposit,
            currency=currency,
            leverage=leverage,
            portable=portable,
            timeout_seconds=timeout_seconds,
        ),
        on_fail_unfavorite=_on_fail_unfavorite if unfavorite_on_fail else None,
    )

    # Incomplete = no outcome write (favorites / passed / run flags untouched).
    if result_id.strip() and not gate.incomplete:
        try:
            from mt5_trade_echo_api import TradeEchoOptimizerApi

            api = TradeEchoOptimizerApi.from_env()
            api.apply_skip_robustness(
                result_id=result_id.strip(),
                passed=gate.passed,
                reject_reason=gate.reject_reason or None,
                max_combo_dd_pct=gate.max_combo_dd_pct,
                combo_count=gate.combo_count,
                baseline_dd_pct=gate.baseline_dd_pct,
                ceiling_dd_pct=gate.ceiling_dd_pct,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"WARNING: applySkipRobustness API failed: {exc}", file=sys.stderr)
    elif gate.incomplete:
        print(
            f"WARNING: skip robustness incomplete "
            f"(combos={gate.combo_count}/{EXPECTED_SKIP_COMBINATIONS}); "
            "leaving dashboard outcome unchanged",
            file=sys.stderr,
        )

    return gate


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Run Skip Robustness stress (SKIP_TRADE_DAY × SKIP_MONTH) on a Survivor."
        ),
    )
    p.add_argument("--set-file", required=True, help="Path to Best/sets/*.set")
    p.add_argument("--symbol", required=True)
    p.add_argument("--timeframe", required=True)
    p.add_argument("--from-date", required=True)
    p.add_argument("--to-date", required=True)
    p.add_argument("--baseline-dd", type=float, required=True)
    p.add_argument("--scaled-risk", type=float, default=None)
    p.add_argument("--expert", required=True)
    p.add_argument("--best-dir", type=Path, default=DEFAULT_BEST_DIR)
    p.add_argument("--favorites-dir", type=Path, default=DEFAULT_FAVORITES_DIR)
    p.add_argument("--work-dir", type=Path, default=PACKAGE_ROOT)
    p.add_argument("--terminal", type=Path, default=None, help="Or set MT5_TERMINAL")
    p.add_argument("--mt5-data", type=Path, default=None)
    p.add_argument("--deposit", default=DEFAULT_DEPOSIT)
    p.add_argument("--currency", default=DEFAULT_CURRENCY)
    p.add_argument("--leverage", default=DEFAULT_LEVERAGE)
    p.add_argument("--portable", action="store_true")
    p.add_argument("--timeout-minutes", type=float, default=0.0)
    p.add_argument("--result-id", default="", help="Dashboard result id for API patch")
    return p


def main(argv: list[str] | None = None) -> int:
    load_repo_env()
    args = build_arg_parser().parse_args(argv)
    try:
        terminal = resolve_terminal(explicit=args.terminal)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    assert terminal is not None
    install_dir = terminal.parent
    data_dir = resolve_mt5_data_dir(
        terminal=terminal,
        portable=args.portable,
        mt5_data=str(args.mt5_data) if args.mt5_data else None,
    )
    timeout = 0.0 if args.timeout_minutes <= 0 else args.timeout_minutes * 60.0

    gate = run_skip_robustness_job(
        set_file=Path(args.set_file),
        symbol=args.symbol,
        timeframe=args.timeframe,
        from_date=args.from_date,
        to_date=args.to_date,
        baseline_dd_pct=float(args.baseline_dd),
        scaled_risk=args.scaled_risk,
        expert=args.expert,
        best_dir=args.best_dir,
        favorites_dir=args.favorites_dir,
        work_dir=args.work_dir,
        terminal=terminal,
        install_dir=install_dir,
        data_dir=data_dir,
        deposit=args.deposit,
        currency=args.currency,
        leverage=args.leverage,
        portable=args.portable,
        timeout_seconds=timeout,
        result_id=args.result_id,
        unfavorite_on_fail=True,
    )

    if gate.incomplete:
        print(
            f"Skip robustness INCOMPLETE "
            f"combos={gate.combo_count} max_DD={gate.max_combo_dd_pct} "
            f"ceiling={gate.ceiling_dd_pct}"
        )
        return EXIT_INCOMPLETE
    if gate.passed:
        print(
            f"Skip robustness PASS "
            f"combos={gate.combo_count} max_DD={gate.max_combo_dd_pct} "
            f"ceiling={gate.ceiling_dd_pct}"
        )
        return 0
    print(
        f"Skip robustness FAIL "
        f"combos={gate.combo_count} max_DD={gate.max_combo_dd_pct} "
        f"ceiling={gate.ceiling_dd_pct}"
    )
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
