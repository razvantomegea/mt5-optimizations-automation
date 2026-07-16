#!/usr/bin/env python3
r"""
Batch-run MetaTrader 5 forward optimizations and validate top passes per job.

Pipeline (default)
- Generates one MT5 tester .ini per optimization job.
- Each set file runs `DEFAULT_RUNS_PER_SET_FILE` times (default 2); every run has its own report and validation pass.
- Launches terminal64.exe, waits for optimization to finish.
- After each fresh job: select top passes from the forward report, run OHLC + real-ticks
  backtests, keep survivors in reports/Best/ (repo-relative by default).

Modes
- Default: optimize + validate per job
- --no-validate: optimization only
- --validate-only: re-validate existing reports/ without optimizing

Important MT5 details
- [Tester] ExpertParameters must point to a .set file in MQL5\Profiles\Tester.
- Report paths are relative to the MT5 data directory.
- ShutdownTerminal=1 lets MT5 close when a job finishes.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import os
import re
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Iterable

if sys.version_info < (3, 10):
    raise RuntimeError("Python 3.10 or higher is required")

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from mt5_env import load_repo_env
from mt5_paths import DEFAULT_BEST_DIR, resolve_set_dir
from mt5_opt_report import (
    ColumnMapping,
    ColumnOverrides,
    DEFAULT_MIN_BACK_RESULT,
    DEFAULT_MIN_FORWARD_RESULT,
    DEFAULT_MIN_SHARPE,
    ForwardReportInfo,
    SelectionStats,
    SelectionThresholds,
    merge_forward_reports,
    print_selection_stats,
    print_metric_distribution,
    resolve_back_and_forward_paths,
    resolve_backtest_dates,
    forward_selection_rank_key,
    select_forward_candidates,
    to_float,
    to_int,
    read_report_text,
    worksheet_rows,
)
from mt5_opt_report import extract_symbol_timeframe as extract_symbol_timeframe_from_title
from mt5_equity_metrics import (
    EquityQualityMetrics,
    extract_equity_quality,
    extract_margin_level_pct,
    validation_score as compute_validation_score,
)
from mt5_set_files import (
    choose_base_set,
    discover_chart_tfs,
    discover_set_files,
    discover_strategies,
    filter_paths_by_strategies,
    flatten_set_tester_name,
    parse_set_file,
    resolve_mt5_data_dir,
    sanitize,
    set_path_relative_to_dir,
)

try:
    from mt5_db_report import NoopReporter, create_reporter
except ImportError:  # pragma: no cover - reporting is optional

    class NoopReporter:
        def __getattr__(self, _name):
            return lambda *args, **kwargs: None

    def create_reporter():
        return NoopReporter()

VALID_PERIODS = {
    "M1", "M2", "M3", "M4", "M5", "M6", "M10", "M12", "M15", "M20", "M30",
    "H1", "H2", "H3", "H4", "H6", "H8", "H12", "D1", "W1", "MN1",
}

def sets_for_chart_tf(chart_tf: str, available_names: list[str]) -> list[str]:
    tf = chart_tf.upper()
    return sorted(
        name
        for name in available_names
        if f"_{tf}_" in name.upper() or name.upper().startswith(f"{tf}_")
    )


def default_param_file_paths(set_dir: Path) -> list[str]:
    return [str(path) for path in sorted(discover_set_files(set_dir).values())]

DEFAULT_MIN_VALIDATION_CAGR = 10.0
DEFAULT_TARGET_EQUITY_DD = 15.0
DEFAULT_MAX_EQUITY_DD = 17.0
DEFAULT_MIN_SCALED_RISK = 1.0
DEFAULT_OPTIMIZATION_MODE = "2"
DEFAULT_OPTIMIZATION_MODEL = "1"
COMPLETE_OPTIMIZATION_MODE = "1"
COMPLETE_OPTIMIZATION_MODEL = "4"
DEFAULT_RISK_ROUND_DECIMALS = 1
REPORT_SUFFIXES = (".xml", ".htm", ".html")
DEFAULT_BACKTEST_TIMEOUT_SEC = 300
DEFAULT_VALIDATE_TOP_N_PER_SYMBOL = 25
DEFAULT_VALIDATE_KEEP_TOP_K = 25
DEFAULT_RUNS_PER_SET_FILE = 1


@dataclass
class Job:
    index: int
    symbol: str
    timeframe: str
    param_file: str
    report_stem: str
    ini_path: str
    report_path: str
    status: str = "pending"
    exit_code: int | None = None
    duration_sec: float | None = None
    error: str = ""


@dataclass
class Candidate:
    source_xml: str
    title: str
    symbol: str
    timeframe: str
    profile: str
    row_index: int
    pass_id: int
    forward_result: float | None
    back_result: float | None
    custom: float
    sharpe: float
    recovery: float
    equity_dd_pct: float
    trades: int
    params: dict[str, Any]
    from_date: str
    to_date: str
    forward_data_found: bool = False


@dataclass
class RiskScalingConfig:
    enabled: bool = True
    target_equity_dd_pct: float = DEFAULT_TARGET_EQUITY_DD
    max_scaled_equity_dd_pct: float = DEFAULT_MAX_EQUITY_DD
    min_scaled_risk: float = DEFAULT_MIN_SCALED_RISK
    risk_round_decimals: int = DEFAULT_RISK_ROUND_DECIMALS


@dataclass
class RiskScalingResult:
    passed: bool
    reject_reason: str = ""
    baseline_risk: float | None = None
    baseline_equity_dd_pct: float | None = None
    scaled_risk: float | None = None
    scaled_ohlc_equity_dd_pct: float | None = None
    scaled_ohlc_report: Path | None = None
    error: str = ""


@dataclass
class ValidationThresholds:
    min_sharpe: float = DEFAULT_MIN_SHARPE
    min_cagr_pct: float = DEFAULT_MIN_VALIDATION_CAGR
    max_equity_dd: float = DEFAULT_MAX_EQUITY_DD


@dataclass
class ValidateJobConfig:
    terminal: Path
    install_dir: Path
    data_dir: Path
    work_dir: Path
    expert: str
    set_dir: Path
    set_index: dict[str, Path]
    xml_path: Path
    best_dir: Path
    top_n: int
    keep_top_k: int
    deposit: str
    currency: str
    leverage: str
    portable: bool
    backtest_timeout_seconds: float
    reset_best_dir: bool
    append_summary: bool
    selection_thresholds: SelectionThresholds
    validation_thresholds: ValidationThresholds
    column_overrides: ColumnOverrides
    fallback_from_date: str
    fallback_to_date: str
    fallback_symbol: str
    fallback_timeframe: str
    verbose: bool
    allow_zero_metrics: bool
    risk_scaling: RiskScalingConfig
    db_reporter: Any = field(default_factory=NoopReporter)


def ensure_exists(path: Path, label: str) -> None:
    if not path.exists():
        raise FileNotFoundError(f"{label} not found: {path}")


def format_seconds(seconds: float | None) -> str:
    if seconds is None:
        return "--"
    seconds = max(0, int(round(seconds)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def render_progress(done: int, total: int, width: int = 28) -> str:
    if total <= 0:
        return "[" + ("-" * width) + "]"
    ratio = done / total
    filled = min(width, int(round(ratio * width)))
    return "[" + ("#" * filled) + ("-" * (width - filled)) + "]"


def write_ini(path: Path, cfg: dict[str, Any]) -> None:
    lines = ["[Tester]"]
    ordered_keys = [
        "Expert", "ExpertParameters", "Symbol", "Period", "Login", "Model",
        "ExecutionMode", "Optimization", "OptimizationCriterion", "FromDate", "ToDate",
        "ForwardMode", "ForwardDate", "Report", "ReplaceReport", "ShutdownTerminal",
        "Deposit", "Currency", "Leverage", "UseLocal", "UseRemote", "UseCloud",
        "Visual", "Port",
    ]
    for key in ordered_keys:
        value = cfg.get(key)
        if value not in (None, ""):
            lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def param_files_for_timeframe(timeframe: str, available_names: list[str]) -> list[str]:
    return sets_for_chart_tf(timeframe, available_names)


def build_jobs(
    symbols: Iterable[str],
    timeframes: Iterable[str],
    param_files_by_timeframe: dict[str, list[str]],
    config_dir: Path,
    reports_dir: Path,
    *,
    runs_per_set_file: int = DEFAULT_RUNS_PER_SET_FILE,
) -> list[Job]:
    if runs_per_set_file < 1:
        raise ValueError(f"runs_per_set_file must be >= 1, got {runs_per_set_file}")
    jobs: list[Job] = []
    idx = 1
    for symbol, tf in itertools.product(symbols, timeframes):
        tf_key = tf.upper()
        for param_file in param_files_by_timeframe[tf_key]:
            for _run in range(runs_per_set_file):
                stem = sanitize(f"{idx:03d}_{symbol}_{tf}_{Path(param_file).stem}")
                ini_path = config_dir / f"{stem}.ini"
                report_path = reports_dir / stem
                jobs.append(Job(
                    index=idx,
                    symbol=symbol,
                    timeframe=tf,
                    param_file=Path(param_file).name,
                    report_stem=stem,
                    ini_path=str(ini_path),
                    report_path=str(report_path),
                ))
                idx += 1
    return jobs


def infer_profile_name(path: Path, title: str) -> str:
    name = path.stem.lower() + " " + title.lower()
    if "classic" in name:
        return "Classic"
    if "multi" in name or "mtf" in name:
        return "Multi"
    return "Unknown"


def stage_param_files(
    items: Iterable[str],
    *,
    set_dir: Path,
    tester_profiles_dir: Path,
) -> tuple[list[str], dict[str, Path]]:
    """Copy repo .set files to MT5 Tester using flat names; return names + index."""
    set_index = discover_set_files(set_dir)
    set_dir_resolved = set_dir.expanduser().resolve()
    flat_names: list[str] = []
    for item in items:
        raw = Path(item).expanduser()
        if raw.exists():
            resolved = raw.resolve()
            rel = set_path_relative_to_dir(raw, set_dir)
            flat_name = flatten_set_tester_name(rel)
            if resolved.is_relative_to(set_dir_resolved):
                src = set_dir_resolved / rel
            else:
                src = resolved
        else:
            flat_name = raw.name
            if flat_name not in set_index:
                nested = set_dir / raw
                if nested.exists():
                    rel = set_path_relative_to_dir(nested, set_dir)
                    flat_name = flatten_set_tester_name(rel)
                    src = nested
                else:
                    raise FileNotFoundError(
                        f"Parameter file not found: {item}. "
                        f"Expected under {set_dir} or as flat tester name."
                    )
            else:
                src = set_index[flat_name]
        dst = tester_profiles_dir / flat_name
        if src.resolve() != dst.resolve():
            shutil.copy2(src, dst)
        set_index[flat_name] = src
        flat_names.append(flat_name)
    return flat_names, set_index


def format_set_param_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return ""
    return str(v)


def apply_candidate_params(set_values: dict[str, str], params: dict[str, Any]) -> None:
    for key, value in params.items():
        if value is None:
            continue
        set_values[key] = format_set_param_value(value)


def set_input_value(set_values: dict[str, str], key: str, value: Any) -> None:
    set_values[key] = format_set_param_value(value)


def compute_scaled_risk(
    *,
    baseline_risk: float,
    baseline_dd_pct: float,
    target_dd_pct: float,
    risk_round_decimals: int,
) -> float | None:
    if baseline_dd_pct <= 0:
        return None
    return round(baseline_risk * (target_dd_pct / baseline_dd_pct), risk_round_decimals)


def equity_dd_within_ceiling(actual_dd_pct: float, max_dd_pct: float) -> bool:
    return actual_dd_pct <= max_dd_pct


def write_set_file(path: Path, values: dict[str, Any]) -> None:
    lines = [f"{k}={format_set_param_value(v)}" for k, v in values.items()]
    content = "\r\n".join(lines) + "\r\n"
    path.write_bytes(b"\xff\xfe" + content.encode("utf-16-le"))


def stop_running_terminal() -> None:
    """MT5 ignores [Tester] when another terminal64.exe instance is already running."""
    if sys.platform != "win32":
        return
    subprocess.run(
        ["taskkill", "/IM", "terminal64.exe", "/F"],
        capture_output=True,
        check=False,
    )


def resolve_report_path(report_base: Path) -> Path:
    for suffix in REPORT_SUFFIXES:
        candidate = Path(str(report_base) + suffix)
        if candidate.is_file():
            return candidate
    # MT5 sometimes writes Report= path with no .htm/.html/.xml suffix.
    if report_base.is_file():
        return report_base
    tried = ", ".join(
        [str(report_base) + suffix for suffix in REPORT_SUFFIXES] + [str(report_base)]
    )
    raise FileNotFoundError(f"Backtest report not generated (tried: {tried})")


def optimization_xml_paths(xml_dir: Path) -> list[Path]:
    """One back .xml per optimization stem (skip duplicate .forward.xml entries)."""
    stems: set[str] = set()
    for xml_path in xml_dir.glob("*.xml"):
        if xml_path.name.endswith(".forward.xml"):
            stems.add(xml_path.name[: -len(".forward.xml")])
        else:
            stems.add(xml_path.stem)
    chosen: list[Path] = []
    for stem in sorted(stems):
        back_xml = xml_dir / f"{stem}.xml"
        if back_xml.is_file():
            chosen.append(back_xml)
            continue
        forward_xml = xml_dir / f"{stem}.forward.xml"
        if forward_xml.is_file():
            chosen.append(forward_xml)
    return chosen


def parse_report_stem_symbol_tf(stem: str) -> tuple[str, str] | None:
    """Parse symbol and timeframe from report stem NNN_SYMBOL_TF_..."""
    parts = stem.split("_", 3)
    if len(parts) < 3:
        return None
    return parts[1].upper(), parts[2].upper()


def filter_optimization_xml_paths(
    xml_paths: list[Path],
    *,
    symbols: Iterable[str],
    timeframes: Iterable[str],
) -> list[Path]:
    symbol_set = {s.upper() for s in symbols}
    tf_set = {t.upper() for t in timeframes}
    filtered: list[Path] = []
    for path in xml_paths:
        parsed = parse_report_stem_symbol_tf(path.stem)
        if parsed is None:
            continue
        sym, tf = parsed
        if sym in symbol_set and tf in tf_set:
            filtered.append(path)
    return filtered


def _log_forward_info(info: ForwardReportInfo, *, verbose: bool) -> None:
    print(
        f"  Forward data: file={info.forward_file_status} | "
        f"back_rows={info.back_rows} | forward_rows={info.forward_rows} | "
        f"forward_joined={info.forward_joined} | "
        f"candidates_with_forward={info.candidates_with_forward}"
    )
    if not info.candidates_with_forward and info.forward_file_status == "missing":
        print(
            "  WARNING: no forward metrics found. Manual selection rejects all rows "
            "without forward Sharpe/Recovery/Result. Ensure forward optimization "
            "completed and stem.forward.xml exists."
        )


def optimization_reports_complete(
    report_xml: Path,
    report_forward_xml: Path,
    *,
    forward_mode: str,
) -> bool:
    """True when resume can safely skip re-optimization for this job."""
    if not report_xml.is_file():
        return False
    if forward_mode == "0":
        return True
    if report_forward_xml.is_file():
        return True
    return False


def candidates_from_xml(
    xml_path: Path,
    *,
    selection_thresholds: SelectionThresholds,
    column_overrides: ColumnOverrides,
    fallback_from_date: str = "",
    fallback_to_date: str = "",
    fallback_symbol: str = "",
    fallback_timeframe: str = "",
    verbose: bool = False,
) -> tuple[list[Candidate], SelectionStats, ForwardReportInfo, ColumnMapping]:
    back_path, forward_path = resolve_back_and_forward_paths(xml_path)
    parsed_rows, fwd_info, mapping, title, headers = merge_forward_reports(
        back_path, forward_path, column_overrides
    )
    _log_forward_info(fwd_info, verbose=verbose)

    if verbose:
        print(f"  Column mapping: {mapping.as_display_dict()}")
        print(f"  Report title: {title}")
        print(f"  Worksheets: 1+ | headers ({len(headers)}): {headers[:12]}...")
        if parsed_rows:
            for i, row in enumerate(parsed_rows[:3], start=1):
                print(
                    f"  Raw row {i}: pass={row.pass_id} custom={row.custom:.4f} "
                    f"back={row.back_result} forward={row.forward_result} "
                    f"back_sharpe={row.sharpe:.4f} back_recovery={row.recovery:.4f} "
                    f"fwd_sharpe={row.forward_sharpe} fwd_recovery={row.forward_recovery}"
                )
        print_metric_distribution(parsed_rows, label="pre-filter")

    accepted, selection_stats = select_forward_candidates(
        parsed_rows,
        selection_thresholds,
    )
    print_selection_stats(selection_stats, selection_thresholds)
    if verbose and accepted:
        print_metric_distribution(accepted, label="post-filter")

    symbol, timeframe = extract_symbol_timeframe_from_title(title)
    if symbol == "UNKNOWN" and fallback_symbol:
        symbol = fallback_symbol
    if timeframe == "UNKNOWN" and fallback_timeframe:
        timeframe = fallback_timeframe
    profile = infer_profile_name(back_path, title)
    from_date, to_date = resolve_backtest_dates(
        title,
        fallback_from=fallback_from_date,
        fallback_to=fallback_to_date,
    )

    result: list[Candidate] = []
    for idx, row in enumerate(accepted, start=1):
        result.append(Candidate(
            source_xml=str(back_path),
            title=title,
            symbol=symbol,
            timeframe=timeframe,
            profile=profile,
            row_index=idx,
            pass_id=row.pass_id,
            forward_result=row.forward_result,
            back_result=row.back_result,
            custom=row.custom,
            sharpe=row.sharpe,
            recovery=row.recovery,
            equity_dd_pct=row.equity_dd_pct,
            trades=row.trades,
            params=row.params,
            from_date=from_date,
            to_date=to_date,
            forward_data_found=row.forward_result is not None,
        ))
    return result, selection_stats, fwd_info, mapping


def collect_candidates_from_paths(
    xml_paths: list[Path],
    *,
    selection_thresholds: SelectionThresholds,
    column_overrides: ColumnOverrides,
    fallback_from_date: str = "",
    fallback_to_date: str = "",
    fallback_symbol: str = "",
    fallback_timeframe: str = "",
    verbose: bool = False,
) -> list[Candidate]:
    all_candidates: list[Candidate] = []
    for xml_path in xml_paths:
        cands, _stats, _info, _mapping = candidates_from_xml(
            xml_path,
            selection_thresholds=selection_thresholds,
            column_overrides=column_overrides,
            fallback_from_date=fallback_from_date,
            fallback_to_date=fallback_to_date,
            fallback_symbol=fallback_symbol,
            fallback_timeframe=fallback_timeframe,
            verbose=verbose,
        )
        all_candidates.extend(cands)
    return all_candidates


def collect_candidates(xml_dir: Path, **kwargs: Any) -> list[Candidate]:
    return collect_candidates_from_paths(optimization_xml_paths(xml_dir), **kwargs)


def top_per_symbol(cands: list[Candidate], top_n: int) -> list[Candidate]:
    grouped: dict[str, list[Candidate]] = defaultdict(list)
    for c in cands:
        grouped[c.symbol].append(c)
    chosen: list[Candidate] = []
    for items in grouped.values():
        items.sort(
            key=lambda cand: forward_selection_rank_key(
                cand.custom, cand.forward_result, cand.pass_id
            )
        )
        chosen.extend(items[:top_n])
    return chosen


def run_single_backtest(
    *,
    terminal: Path,
    install_dir: Path,
    data_dir: Path,
    work_dir: Path,
    expert: str,
    set_file_name: str,
    symbol: str,
    timeframe: str,
    from_date: str,
    to_date: str,
    model: int,
    deposit: str,
    currency: str,
    leverage: str,
    portable: bool,
    timeout_seconds: float,
) -> Path:
    reports_dir = work_dir / "reports"
    configs_dir = work_dir / "configs"
    reports_dir.mkdir(parents=True, exist_ok=True)
    configs_dir.mkdir(parents=True, exist_ok=True)
    stem = sanitize(f"{symbol}_{timeframe}_{Path(set_file_name).stem}_model{model}")
    report_base = reports_dir / stem
    ini_path = configs_dir / f"{stem}.ini"
    cfg = {
        "Expert": expert,
        "ExpertParameters": set_file_name,
        "Symbol": symbol,
        "Period": timeframe,
        "Model": str(model),
        "ExecutionMode": "-1",
        "Optimization": "0",
        "FromDate": from_date,
        "ToDate": to_date,
        "ForwardMode": "0",
        "Report": os.path.relpath(report_base, data_dir).replace("/", "\\"),
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
    proc = subprocess.Popen(cmd, cwd=str(install_dir))
    try:
        proc.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise RuntimeError(
            f"MT5 backtest timed out after {timeout_seconds:.0f}s: {symbol} {timeframe} model={model}"
        ) from None
    try:
        return resolve_report_path(report_base)
    except FileNotFoundError:
        # Optimization jobs already accept nonzero exit when a report exists
        # (done_with_nonzero_exit). Match that for validation backtests.
        if proc.returncode != 0:
            raise RuntimeError(
                f"MT5 backtest exited with code {proc.returncode}: "
                f"{symbol} {timeframe} model={model}"
            ) from None
        raise


def extract_backtest_stat(report_path: Path, label: str) -> float:
    if report_path.suffix.lower() == ".xml":
        _title, _headers, records = worksheet_rows(report_path)
        for rec in records:
            for key, val in rec.items():
                if isinstance(key, str) and label in key:
                    parsed = to_float(val)
                    if parsed is not None:
                        return parsed
    text = read_report_text(report_path)
    m = re.search(re.escape(label) + r":.*?<b>([-\d.\s,]+)", text, re.S)
    if m:
        raw = m.group(1).replace(" ", "").replace(",", "")
        return float(raw)
    raise ValueError(f"Could not extract {label} from {report_path}")


def extract_backtest_equity_dd_pct(report_path: Path) -> float:
    if report_path.suffix.lower() == ".xml":
        _title, _headers, records = worksheet_rows(report_path)
        for rec in records:
            for key, val in rec.items():
                if isinstance(key, str) and "Equity Drawdown Relative" in key:
                    parsed = to_float(val)
                    if parsed is not None:
                        return parsed
    text = read_report_text(report_path)
    m = re.search(r"Equity Drawdown Relative:.*?<b>([\d.]+)%", text, re.S)
    if m:
        return float(m.group(1))
    m = re.search(r"Equity Drawdown Relative.*?(\d+(?:\.\d+)?)", text, re.S)
    if m:
        return float(m.group(1))
    raise ValueError(f"Could not extract Equity Drawdown Relative from {report_path}")


def resolve_validation_risk(
    *,
    set_values: dict[str, str],
    generated_set: Path,
    tester_profiles_dir: Path,
    backtest_kwargs: dict[str, Any],
    risk_scaling: RiskScalingConfig,
    verbose: bool,
) -> RiskScalingResult:
    baseline_risk = to_float(set_values.get("RISK"), 1.0) or 1.0

    try:
        baseline_report = run_single_backtest(**backtest_kwargs, model=1)
        baseline_dd = extract_backtest_equity_dd_pct(baseline_report)
    except (RuntimeError, FileNotFoundError, ValueError) as exc:
        if verbose:
            print(f"    risk scaling baseline probe failed: {exc}")
        return RiskScalingResult(
            passed=False,
            reject_reason="risk_scaling_probe_failed",
            baseline_risk=baseline_risk,
            error=str(exc),
        )

    if verbose:
        print(
            f"    risk scaling step 1: RISK={baseline_risk} "
            f"equity_DD={baseline_dd:.4f}%"
        )

    if baseline_dd <= 0:
        if verbose:
            print(f"    reject: baseline DD {baseline_dd:.4f}% <= 0 (non-linear scaling)")
        return RiskScalingResult(
            passed=False,
            reject_reason="risk_scaling_nonlinear",
            baseline_risk=baseline_risk,
            baseline_equity_dd_pct=baseline_dd,
        )

    scaled_risk = compute_scaled_risk(
        baseline_risk=baseline_risk,
        baseline_dd_pct=baseline_dd,
        target_dd_pct=risk_scaling.target_equity_dd_pct,
        risk_round_decimals=risk_scaling.risk_round_decimals,
    )
    if scaled_risk is None:
        return RiskScalingResult(
            passed=False,
            reject_reason="risk_scaling_nonlinear",
            baseline_risk=baseline_risk,
            baseline_equity_dd_pct=baseline_dd,
        )
    if scaled_risk < risk_scaling.min_scaled_risk:
        if verbose:
            print(
                f"    reject: scaled RISK {scaled_risk} < "
                f"{risk_scaling.min_scaled_risk} (min allowed)"
            )
        return RiskScalingResult(
            passed=False,
            reject_reason="risk_scaling_below_min_risk",
            baseline_risk=baseline_risk,
            baseline_equity_dd_pct=baseline_dd,
            scaled_risk=scaled_risk,
        )
    set_input_value(set_values, "RISK", scaled_risk)
    write_set_file(generated_set, set_values)
    shutil.copy2(generated_set, tester_profiles_dir / generated_set.name)

    try:
        scaled_report = run_single_backtest(**backtest_kwargs, model=1)
        scaled_dd = extract_backtest_equity_dd_pct(scaled_report)
    except (RuntimeError, FileNotFoundError, ValueError) as exc:
        if verbose:
            print(f"    risk scaling scaled probe failed: {exc}")
        return RiskScalingResult(
            passed=False,
            reject_reason="risk_scaling_probe_failed",
            baseline_risk=baseline_risk,
            baseline_equity_dd_pct=baseline_dd,
            scaled_risk=scaled_risk,
            error=str(exc),
        )

    max_scaled_dd = risk_scaling.max_scaled_equity_dd_pct
    if verbose:
        print(
            f"    risk scaling step 2: RISK={scaled_risk} "
            f"equity_DD={scaled_dd:.4f}% "
            f"(target {risk_scaling.target_equity_dd_pct}%, max {max_scaled_dd}%)"
        )

    if not equity_dd_within_ceiling(scaled_dd, max_scaled_dd):
        if verbose:
            print(
                f"    reject: scaled OHLC DD {scaled_dd:.4f}% > "
                f"{max_scaled_dd}% (non-linear scaling)"
            )
        return RiskScalingResult(
            passed=False,
            reject_reason="risk_scaling_nonlinear",
            baseline_risk=baseline_risk,
            baseline_equity_dd_pct=baseline_dd,
            scaled_risk=scaled_risk,
            scaled_ohlc_equity_dd_pct=scaled_dd,
            scaled_ohlc_report=scaled_report,
        )

    return RiskScalingResult(
        passed=True,
        baseline_risk=baseline_risk,
        baseline_equity_dd_pct=baseline_dd,
        scaled_risk=scaled_risk,
        scaled_ohlc_equity_dd_pct=scaled_dd,
        scaled_ohlc_report=scaled_report,
    )


def extract_validation_metrics(
    real_report: Path,
    *,
    allow_zero_metrics: bool = False,
) -> tuple[float | None, float | None, EquityQualityMetrics | None, float | None]:
    try:
        recovery = extract_backtest_stat(real_report, "Recovery Factor")
        sharpe = extract_backtest_stat(real_report, "Sharpe Ratio")
        equity = extract_equity_quality(real_report)
        if recovery is None or sharpe is None:
            raise ValueError("missing metrics")
        score = compute_validation_score(recovery, sharpe, equity)
        return recovery, sharpe, equity, score
    except ValueError:
        if allow_zero_metrics:
            equity = EquityQualityMetrics(
                lr_correlation=0.0,
                lr_std_error=0.0,
                cagr_pct=0.0,
                calmar=0.0,
                k_ratio_proxy=0.0,
                ulcer_index=0.0,
                max_stagnation_days=0,
                time_under_water_pct=0.0,
                initial_balance=0.0,
                final_balance=0.0,
                test_years=0.0,
            )
            return 0.0, 0.0, equity, 0.0
        return None, None, None, None


def _format_optional_float(value: Any) -> Any:
    if value is None:
        return ""
    return value


def candidate_summary_row(
    cand: Candidate,
    *,
    keep: bool = False,
    dd_pass: bool = False,
    validation_pass: bool = False,
    set_file: str = "",
    ohlc_dd: Any = "",
    real_dd: Any = "",
    validation_recovery: Any = "",
    validation_sharpe: Any = "",
    validation_score: Any = "",
    validation_lr_correlation: Any = "",
    validation_cagr_pct: Any = "",
    validation_calmar: Any = "",
    validation_k_ratio_proxy: Any = "",
    validation_max_stagnation_days: Any = "",
    validation_ulcer_index: Any = "",
    validation_time_under_water_pct: Any = "",
    margin_level_pct: Any = "",
    baseline_risk: Any = "",
    baseline_equity_dd_pct: Any = "",
    scaled_risk: Any = "",
    scaled_ohlc_equity_dd_pct: Any = "",
    risk_scaling_pass: bool = False,
    reject_reason: str = "",
    error: str = "",
) -> dict[str, Any]:
    return {
        "symbol": cand.symbol,
        "timeframe": cand.timeframe,
        "profile": cand.profile,
        "pass_id": cand.pass_id,
        "custom": cand.custom,
        "optimization_back_result": _format_optional_float(cand.back_result),
        "optimization_forward_result": _format_optional_float(cand.forward_result),
        "forward_data_found": cand.forward_data_found,
        "sharpe": cand.sharpe,
        "recovery": cand.recovery,
        "optimization_equity_dd_pct": cand.equity_dd_pct,
        "ohlc_equity_dd_pct": ohlc_dd,
        "realticks_equity_dd_pct": real_dd,
        "dd_pass": dd_pass,
        "validation_pass": validation_pass,
        "validation_recovery": validation_recovery,
        "validation_sharpe": validation_sharpe,
        "validation_score": validation_score,
        "validation_lr_correlation": validation_lr_correlation,
        "validation_cagr_pct": validation_cagr_pct,
        "validation_calmar": validation_calmar,
        "validation_k_ratio_proxy": validation_k_ratio_proxy,
        "validation_max_stagnation_days": validation_max_stagnation_days,
        "validation_ulcer_index": validation_ulcer_index,
        "validation_time_under_water_pct": validation_time_under_water_pct,
        "margin_level_pct": margin_level_pct,
        "baseline_risk": baseline_risk,
        "baseline_equity_dd_pct": baseline_equity_dd_pct,
        "scaled_risk": scaled_risk,
        "scaled_ohlc_equity_dd_pct": scaled_ohlc_equity_dd_pct,
        "risk_scaling_pass": risk_scaling_pass,
        "reject_reason": reject_reason,
        "keep": keep,
        "set_file": set_file,
        "source_xml": cand.source_xml,
        "error": error,
    }


def write_summary_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                fieldnames.append(key)
                seen.add(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _validation_rank_key(row: dict[str, Any]) -> tuple[float, float, int]:
    return (
        to_float(row.get("validation_score")),
        to_float(row.get("validation_recovery")),
        to_int(row.get("pass_id")),
    )


def _is_validation_pass(row: dict[str, Any]) -> bool:
    value = row.get("validation_pass")
    return value is True or value in ("True", "true", "1", 1)


def _apply_top_k_ranking(rows: list[dict[str, Any]], keep_top_k: int) -> None:
    survivors = [r for r in rows if _is_validation_pass(r)]
    survivors.sort(key=_validation_rank_key, reverse=True)
    keep_ids = {id(r) for r in survivors[:keep_top_k]}
    for row in rows:
        row["keep"] = id(row) in keep_ids


def _row_is_survivor(row: dict[str, Any]) -> bool:
    keep = row.get("keep")
    return keep is True or keep in ("True", "true", "1", 1)


def _prepare_best_dir(best_dir: Path, *, reset: bool) -> None:
    if reset and best_dir.exists():
        shutil.rmtree(best_dir)
    best_dir.mkdir(parents=True, exist_ok=True)
    (best_dir / "sets").mkdir(exist_ok=True)
    (best_dir / "reports").mkdir(exist_ok=True)


def _write_survivors_csv(best_dir: Path, summary_rows: list[dict[str, Any]]) -> None:
    survivors = [r for r in summary_rows if _row_is_survivor(r)]
    survivors_csv = best_dir / "best_survivors.csv"
    if not summary_rows:
        return
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in summary_rows:
        for key in row:
            if key not in seen:
                fieldnames.append(key)
                seen.add(key)
    with survivors_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(survivors)
    if not survivors:
        print(f"  No survivors passed validation. Wrote header-only {survivors_csv}")


def _validation_passes(
    *,
    risk_scaling_pass: bool,
    ohlc_dd_pass: bool,
    real_ticks_dd_pass: bool,
    val_sharpe: float | None,
    val_equity: EquityQualityMetrics | None,
    thresholds: ValidationThresholds,
) -> bool:
    if not risk_scaling_pass or not ohlc_dd_pass or not real_ticks_dd_pass:
        return False
    if val_sharpe is None or val_equity is None:
        return False
    if val_sharpe < thresholds.min_sharpe:
        return False
    return val_equity.cagr_pct >= thresholds.min_cagr_pct


def validate_job(cfg: ValidateJobConfig) -> list[dict[str, Any]]:
    """Select top passes from one optimization XML, run backtests, append survivors to Best/."""
    staging_dir = cfg.work_dir / "validate_staging"
    if cfg.reset_best_dir:
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)
    _prepare_best_dir(cfg.best_dir, reset=cfg.reset_best_dir)

    stop_running_terminal()

    tester_profiles_dir = cfg.data_dir / "MQL5" / "Profiles" / "Tester"
    tester_profiles_dir.mkdir(parents=True, exist_ok=True)

    try:
        cands, selection_stats, fwd_info, _mapping = candidates_from_xml(
            cfg.xml_path,
            selection_thresholds=cfg.selection_thresholds,
            column_overrides=cfg.column_overrides,
            fallback_from_date=cfg.fallback_from_date,
            fallback_to_date=cfg.fallback_to_date,
            fallback_symbol=cfg.fallback_symbol,
            fallback_timeframe=cfg.fallback_timeframe,
            verbose=cfg.verbose,
        )
    except ValueError as exc:
        print(f"ERROR: {cfg.xml_path.name}: {exc}", file=sys.stderr)
        return []

    if not cands:
        print(f"No qualifying candidates for {cfg.xml_path.name}")
        print_selection_stats(selection_stats, cfg.selection_thresholds)
        if (
            selection_stats.accepted == 0
            and fwd_info.forward_file_status == "missing"
            and not fwd_info.has_inline_forward_columns
        ):
            print(
                "  WARNING: forward selection rejected all rows because forward "
                "metrics are missing. Re-run optimization or delete partial "
                "reports before using --resume."
            )
        return []

    selected = top_per_symbol(cands, cfg.top_n)
    print(
        f"Validating {cfg.xml_path.name}: {len(cands)} candidates, "
        f"{len(selected)} selected for backtests"
    )
    if cfg.verbose:
        print(
            f"  Forward selection: Custom/Result>={cfg.selection_thresholds.min_back_custom}, "
            f"Sharpe>={cfg.selection_thresholds.min_sharpe}, "
            f"forward Result>={cfg.selection_thresholds.min_forward_result}; "
            f"rank by Custom + forward Result"
        )
        vt = cfg.validation_thresholds
        print(
            f"  Validation gates: sharpe>={vt.min_sharpe} "
            f"cagr>={vt.min_cagr_pct}% "
            f"equity_dd<={vt.max_equity_dd}% "
            f"(OHLC + real ticks at scaled RISK)"
        )
        if cfg.risk_scaling.enabled:
            print(
                f"  Risk scaling: linear scale RISK toward "
                f"{cfg.risk_scaling.target_equity_dd_pct}% equity DD "
                f"(reject if scaled RISK < {cfg.risk_scaling.min_scaled_risk}, "
                f"or OHLC or real ticks > "
                f"{cfg.risk_scaling.max_scaled_equity_dd_pct}% — non-linear)"
            )
        else:
            print("  Risk scaling: disabled")

    summary_csv = cfg.best_dir / "best_summary.csv"
    summary_rows: list[dict[str, Any]] = []
    if cfg.append_summary and summary_csv.exists():
        with summary_csv.open(newline="", encoding="utf-8") as f:
            summary_rows = list(csv.DictReader(f))

    new_rows: list[dict[str, Any]] = []
    pending_copies: list[tuple[dict[str, Any], str, Path, Path, Path, Candidate]] = []
    batch_start = time.time()
    total = len(selected)
    for idx, cand in enumerate(selected, start=1):
        elapsed = time.time() - batch_start
        avg = elapsed / (idx - 1) if idx > 1 else None
        eta = avg * (total - idx + 1) if avg is not None else None
        fwd_display = (
            f"{cand.forward_result:.4f}"
            if cand.forward_result is not None
            else "n/a"
        )
        print(
            f"  [{idx}/{total}] {cand.symbol} {cand.timeframe} pass={cand.pass_id} "
            f"custom={cand.custom:.4f} forward={fwd_display} ETA={format_seconds(eta)}"
        )

        base_set = choose_base_set(report_stem=Path(cand.source_xml).stem, set_index=cfg.set_index)
        set_values = parse_set_file(base_set)
        apply_candidate_params(set_values, cand.params)

        cand_stem = sanitize(f"{cand.symbol}_{cand.timeframe}_{cand.profile}_pass{cand.pass_id}")
        generated_set = staging_dir / f"{cand_stem}.set"
        write_set_file(generated_set, set_values)
        shutil.copy2(generated_set, tester_profiles_dir / generated_set.name)

        backtest_kwargs = {
            "terminal": cfg.terminal,
            "install_dir": cfg.install_dir,
            "data_dir": cfg.data_dir,
            "work_dir": staging_dir / cand_stem,
            "expert": cfg.expert,
            "set_file_name": generated_set.name,
            "symbol": cand.symbol,
            "timeframe": cand.timeframe,
            "from_date": cand.from_date,
            "to_date": cand.to_date,
            "deposit": cfg.deposit,
            "currency": cfg.currency,
            "leverage": cfg.leverage,
            "portable": cfg.portable,
            "timeout_seconds": cfg.backtest_timeout_seconds,
        }

        try:
            baseline_risk = to_float(set_values.get("RISK"), 1.0) or 1.0
            scaled_risk_value: float | None = baseline_risk
            baseline_equity_dd: float | None = None
            scaled_ohlc_equity_dd: float | None = None
            risk_scaling_pass = True

            if cfg.risk_scaling.enabled:
                scaling = resolve_validation_risk(
                    set_values=set_values,
                    generated_set=generated_set,
                    tester_profiles_dir=tester_profiles_dir,
                    backtest_kwargs=backtest_kwargs,
                    risk_scaling=cfg.risk_scaling,
                    verbose=cfg.verbose,
                )
                baseline_risk = scaling.baseline_risk or baseline_risk
                baseline_equity_dd = scaling.baseline_equity_dd_pct
                scaled_risk_value = (
                    scaling.scaled_risk if scaling.scaled_risk is not None else baseline_risk
                )
                scaled_ohlc_equity_dd = scaling.scaled_ohlc_equity_dd_pct
                risk_scaling_pass = scaling.passed

                if not scaling.passed:
                    if cfg.verbose:
                        print(f"    reject_reason={scaling.reject_reason}")
                    row = candidate_summary_row(
                        cand,
                        set_file=str(generated_set),
                        baseline_risk=_format_optional_float(baseline_risk),
                        baseline_equity_dd_pct=_format_optional_float(baseline_equity_dd),
                        scaled_risk=_format_optional_float(scaled_risk_value),
                        scaled_ohlc_equity_dd_pct=_format_optional_float(scaled_ohlc_equity_dd),
                        risk_scaling_pass=False,
                        reject_reason=scaling.reject_reason,
                        error=scaling.error,
                    )
                    summary_rows.append(row)
                    cfg.db_reporter.validation_result(
                        row=row, parameters=cand.params, real_report_path=None
                    )
                    write_summary_csv(summary_csv, summary_rows + new_rows)
                    continue

                ohlc_report = scaling.scaled_ohlc_report
                ohlc_dd = scaling.scaled_ohlc_equity_dd_pct
                if ohlc_report is None or ohlc_dd is None:
                    raise RuntimeError("risk scaling passed without scaled OHLC report")
            else:
                ohlc_report = run_single_backtest(**backtest_kwargs, model=1)
                ohlc_dd = extract_backtest_equity_dd_pct(ohlc_report)
                baseline_equity_dd = ohlc_dd
                scaled_ohlc_equity_dd = ohlc_dd

            real_report = run_single_backtest(**backtest_kwargs, model=4)
        except (RuntimeError, FileNotFoundError, ValueError) as exc:
            print(f"    FAILED: {exc}", file=sys.stderr)
            if cfg.verbose:
                print(f"    reject_reason=backtest_error: {exc}")
            row = candidate_summary_row(
                cand,
                set_file=str(generated_set),
                reject_reason="backtest_error",
                error=str(exc),
            )
            summary_rows.append(row)
            cfg.db_reporter.validation_result(
                row=row, parameters=cand.params, real_report_path=None
            )
            write_summary_csv(summary_csv, summary_rows + new_rows)
            continue

        real_dd = extract_backtest_equity_dd_pct(real_report)
        margin_level = extract_margin_level_pct(real_report)
        max_equity_dd = cfg.validation_thresholds.max_equity_dd
        ohlc_dd_pass = ohlc_dd is not None and equity_dd_within_ceiling(ohlc_dd, max_equity_dd)
        real_ticks_dd_pass = equity_dd_within_ceiling(real_dd, max_equity_dd)
        dd_pass = ohlc_dd_pass and real_ticks_dd_pass
        scaling_nonlinear = (
            cfg.risk_scaling.enabled
            and (not ohlc_dd_pass or not real_ticks_dd_pass)
        )
        if scaling_nonlinear:
            risk_scaling_pass = False
        val_recovery, val_sharpe, val_equity, val_score = extract_validation_metrics(
            real_report,
            allow_zero_metrics=cfg.allow_zero_metrics,
        )

        reject_reasons: list[str] = []
        if scaling_nonlinear:
            reject_reasons.append("risk_scaling_nonlinear")
            if cfg.verbose:
                print(
                    f"    dropping pass={cand.pass_id}: scaled OHLC DD "
                    f"{ohlc_dd:.4f}% or real DD {real_dd:.4f}% > "
                    f"{max_equity_dd}% (non-linear scaling)"
                )
        elif not real_ticks_dd_pass:
            reject_reasons.append("high_equity_dd")
            if cfg.verbose:
                print(
                    f"    dropping pass={cand.pass_id}: real-ticks equity DD "
                    f"{real_dd:.4f}% > {max_equity_dd}%"
                )
        elif not ohlc_dd_pass:
            reject_reasons.append("dd_fail")
            if cfg.verbose:
                print(
                    f"    dropping pass={cand.pass_id}: OHLC DD "
                    f"{ohlc_dd:.4f}% > {max_equity_dd}%"
                )
        if val_sharpe is None or val_equity is None:
            reject_reasons.append("missing_validation_metrics")
            if cfg.verbose:
                print(f"    missing Sharpe/equity metrics for pass={cand.pass_id}")
        if val_sharpe is not None and val_sharpe < cfg.validation_thresholds.min_sharpe:
            reject_reasons.append("low_validation_sharpe")
            if cfg.verbose:
                print(
                    f"    dropping pass={cand.pass_id}: validation sharpe "
                    f"{val_sharpe:.4f} < {cfg.validation_thresholds.min_sharpe}"
                )
        if val_equity is not None and val_equity.cagr_pct < cfg.validation_thresholds.min_cagr_pct:
            reject_reasons.append("low_cagr")
            if cfg.verbose:
                print(
                    f"    dropping pass={cand.pass_id}: CAGR "
                    f"{val_equity.cagr_pct:.2f}% < "
                    f"{cfg.validation_thresholds.min_cagr_pct}%"
                )

        validation_pass = _validation_passes(
            risk_scaling_pass=risk_scaling_pass if cfg.risk_scaling.enabled else True,
            ohlc_dd_pass=ohlc_dd_pass,
            real_ticks_dd_pass=real_ticks_dd_pass,
            val_sharpe=val_sharpe,
            val_equity=val_equity,
            thresholds=cfg.validation_thresholds,
        )

        row = candidate_summary_row(
            cand,
            dd_pass=dd_pass,
            validation_pass=validation_pass,
            set_file=str(generated_set),
            ohlc_dd=ohlc_dd,
            real_dd=real_dd,
            baseline_risk=_format_optional_float(baseline_risk),
            baseline_equity_dd_pct=_format_optional_float(baseline_equity_dd),
            scaled_risk=_format_optional_float(scaled_risk_value),
            scaled_ohlc_equity_dd_pct=_format_optional_float(scaled_ohlc_equity_dd),
            risk_scaling_pass=risk_scaling_pass,
            margin_level_pct=_format_optional_float(margin_level),
            validation_recovery=_format_optional_float(val_recovery),
            validation_sharpe=_format_optional_float(val_sharpe),
            validation_score=_format_optional_float(val_score),
            validation_lr_correlation=_format_optional_float(
                val_equity.lr_correlation if val_equity else None
            ),
            validation_cagr_pct=_format_optional_float(
                val_equity.cagr_pct if val_equity else None
            ),
            validation_calmar=_format_optional_float(
                val_equity.calmar if val_equity else None
            ),
            validation_k_ratio_proxy=_format_optional_float(
                val_equity.k_ratio_proxy if val_equity else None
            ),
            validation_max_stagnation_days=(
                val_equity.max_stagnation_days if val_equity else ""
            ),
            validation_ulcer_index=_format_optional_float(
                val_equity.ulcer_index if val_equity else None
            ),
            validation_time_under_water_pct=_format_optional_float(
                val_equity.time_under_water_pct if val_equity else None
            ),
            reject_reason=",".join(reject_reasons),
        )
        new_rows.append(row)
        pending_copies.append((row, cand_stem, generated_set, ohlc_report, real_report, cand))
        print(
            f"    baseline_RISK={baseline_risk} | scaled_RISK={scaled_risk_value} | "
            f"OHLC DD={ohlc_dd:.4f}% | RealTicks DD={real_dd:.4f}% | "
            f"margin={margin_level if margin_level is not None else 'n/a'}% | "
            f"risk_scaling_pass={risk_scaling_pass} | dd_pass={dd_pass} | "
            f"validation_pass={validation_pass} | "
            f"validation_score={val_score if val_score is not None else 'n/a'}"
            + (
                f" | LR={val_equity.lr_correlation:.2f} CAGR={val_equity.cagr_pct:.2f}% "
                f"Calmar={val_equity.calmar:.2f} stagnation={val_equity.max_stagnation_days}d"
                if val_equity is not None
                else ""
            )
        )

    _apply_top_k_ranking(new_rows, cfg.keep_top_k)
    for row, cand_stem, generated_set, ohlc_report, real_report, cand in pending_copies:
        cfg.db_reporter.validation_result(
            row=row, parameters=cand.params, real_report_path=real_report
        )
        if row.get("keep"):
            set_file = str(cfg.best_dir / "sets" / f"{cand_stem}.set")
            row["set_file"] = set_file
            shutil.copy2(generated_set, set_file)
            target_report_dir = cfg.best_dir / "reports" / cand.symbol
            target_report_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ohlc_report, target_report_dir / f"{cand_stem}_ohlc{ohlc_report.suffix}")
            shutil.copy2(real_report, target_report_dir / f"{cand_stem}_realticks{real_report.suffix}")
            shutil.copy2(Path(cand.source_xml), target_report_dir / Path(cand.source_xml).name)
            _, fwd_path = resolve_back_and_forward_paths(Path(cand.source_xml))
            if fwd_path and fwd_path.is_file():
                shutil.copy2(fwd_path, target_report_dir / fwd_path.name)

    kept = sum(1 for r in new_rows if r.get("keep"))
    if new_rows:
        print(
            f"  Final ranking: {kept}/{len(new_rows)} kept "
            f"(top {cfg.keep_top_k} by validation score; "
            f"risk scaling + sharpe/CAGR/DD gates required)"
        )
    elif cfg.verbose:
        print(f"  Forward info: {fwd_info.forward_file_status} joined={fwd_info.forward_joined}")

    summary_rows.extend(new_rows)
    write_summary_csv(summary_csv, summary_rows)
    _write_survivors_csv(cfg.best_dir, summary_rows)
    return new_rows


def resolve_optimization_xml(report_path: str) -> Path:
    """Return back optimization .xml (forward metrics merged at parse time)."""
    plain_xml = Path(report_path + ".xml")
    if plain_xml.is_file():
        return plain_xml
    forward_xml = Path(report_path + ".forward.xml")
    if forward_xml.is_file():
        return forward_xml
    raise FileNotFoundError(
        f"No optimization XML found for {report_path} (tried .xml and .forward.xml)"
    )


def _selection_thresholds_from_args(args: argparse.Namespace) -> SelectionThresholds:
    min_sharpe = float(args.min_sharpe)
    return SelectionThresholds(
        min_back_custom=float(args.min_back_result),
        min_forward_result=float(args.min_forward_result),
        min_sharpe=min_sharpe,
    )


def _validation_thresholds_from_args(args: argparse.Namespace) -> ValidationThresholds:
    return ValidationThresholds(
        min_sharpe=float(args.min_sharpe),
        min_cagr_pct=float(args.min_validation_cagr),
        max_equity_dd=float(args.max_equity_dd),
    )


def _require_positive(name: str, value: float) -> float:
    if value <= 0:
        raise ValueError(f"{name} must be > 0, got {value}")
    return value


def _risk_scaling_config_from_args(args: argparse.Namespace) -> RiskScalingConfig:
    target_equity_dd = _require_positive(
        "--target-equity-dd",
        float(args.target_equity_dd),
    )
    max_equity_dd = _require_positive(
        "--max-equity-dd",
        float(args.max_equity_dd),
    )
    min_scaled_risk = _require_positive(
        "--min-scaled-risk",
        float(args.min_scaled_risk),
    )
    return RiskScalingConfig(
        enabled=not args.no_risk_scaling,
        target_equity_dd_pct=target_equity_dd,
        max_scaled_equity_dd_pct=max_equity_dd,
        min_scaled_risk=min_scaled_risk,
        risk_round_decimals=DEFAULT_RISK_ROUND_DECIMALS,
    )


def _column_overrides_from_args(args: argparse.Namespace) -> ColumnOverrides:
    return ColumnOverrides(
        pass_col=args.col_pass or None,
        custom=args.col_custom or None,
        result=args.col_result or None,
        back_result=args.col_back_result or None,
        forward_result=args.col_forward_result or None,
        sharpe=args.col_sharpe or None,
        recovery=args.col_recovery or None,
        equity_dd=args.col_dd or None,
        trades=args.col_trades or None,
        profit=args.col_profit or None,
    )


def _validate_job_config_from_args(
    args: argparse.Namespace,
    *,
    terminal: Path,
    install_dir: Path,
    data_dir: Path,
    work_dir: Path,
    set_dir: Path,
    set_index: dict[str, Path],
    xml_path: Path,
    best_dir: Path,
    reset_best_dir: bool,
    append_summary: bool,
    fallback_symbol: str = "",
    fallback_timeframe: str = "",
    db_reporter: Any = None,
) -> ValidateJobConfig:
    return ValidateJobConfig(
        terminal=terminal,
        install_dir=install_dir,
        data_dir=data_dir,
        work_dir=work_dir,
        expert=args.expert,
        set_dir=set_dir,
        set_index=set_index,
        xml_path=xml_path,
        best_dir=best_dir,
        top_n=args.validate_top_n_per_symbol,
        keep_top_k=args.validate_keep_top_k,
        deposit=args.deposit,
        currency=args.currency,
        leverage=args.leverage,
        portable=args.portable,
        backtest_timeout_seconds=args.backtest_timeout_seconds,
        reset_best_dir=reset_best_dir,
        append_summary=append_summary,
        selection_thresholds=_selection_thresholds_from_args(args),
        validation_thresholds=_validation_thresholds_from_args(args),
        column_overrides=_column_overrides_from_args(args),
        fallback_from_date=args.from_date or "",
        fallback_to_date=args.to_date or "",
        fallback_symbol=fallback_symbol,
        fallback_timeframe=fallback_timeframe,
        verbose=args.verbose,
        allow_zero_metrics=args.allow_zero_metrics,
        risk_scaling=_risk_scaling_config_from_args(args),
        db_reporter=db_reporter if db_reporter is not None else NoopReporter(),
    )


def run_validate_only(args: argparse.Namespace) -> int:
    terminal = Path(args.terminal).expanduser().resolve()
    ensure_exists(terminal, "MT5 terminal")
    install_dir = terminal.parent
    data_dir = resolve_mt5_data_dir(
        terminal=terminal,
        portable=args.portable,
        mt5_data=args.mt5_data or None,
    )
    work_dir = Path(args.work_dir).expanduser().resolve()
    reports_dir = (work_dir / args.reports_dir).resolve()
    if not reports_dir.is_dir():
        raise FileNotFoundError(f"Reports directory not found: {reports_dir}")

    set_dir = (
        Path(args.validate_set_dir).expanduser().resolve()
        if args.validate_set_dir.strip()
        else resolve_set_dir(required=True)
    )
    if not set_dir.is_dir():
        raise FileNotFoundError(f"Set directory not found: {set_dir}")

    set_index = discover_set_files(set_dir)
    if not set_index:
        raise FileNotFoundError(f"No .set files found under {set_dir}")

    best_dir = (
        Path(args.best_dir).expanduser().resolve()
        if args.best_dir
        else DEFAULT_BEST_DIR
    )

    all_xml_paths = optimization_xml_paths(reports_dir)
    if not all_xml_paths:
        raise ValueError(f"No optimization XML reports found in {reports_dir}")

    xml_paths = filter_optimization_xml_paths(
        all_xml_paths,
        symbols=args.symbols,
        timeframes=args.timeframes,
    )
    if not xml_paths:
        raise ValueError(
            f"No optimization XML reports matched --symbols {args.symbols} "
            f"and --timeframes {args.timeframes} in {reports_dir} "
            f"({len(all_xml_paths)} total report(s))"
        )

    if len(xml_paths) < len(all_xml_paths):
        print(
            f"Validate-only: {len(xml_paths)}/{len(all_xml_paths)} report(s) "
            f"for symbols={args.symbols} timeframes={args.timeframes} in {reports_dir}"
        )
    else:
        print(f"Validate-only: {len(xml_paths)} report(s) in {reports_dir}")
    best_dir_initialized = (best_dir / "best_summary.csv").is_file()
    total_survivors = 0

    for xml_path in xml_paths:
        rows = validate_job(_validate_job_config_from_args(
            args,
            terminal=terminal,
            install_dir=install_dir,
            data_dir=data_dir,
            work_dir=work_dir,
            set_dir=set_dir,
            set_index=set_index,
            xml_path=xml_path,
            best_dir=best_dir,
            reset_best_dir=not best_dir_initialized,
            append_summary=best_dir_initialized,
        ))
        survivors = sum(1 for r in rows if r.get("keep"))
        total_survivors += survivors
        print(f"Validated {xml_path.stem}: selected={len(rows)} survivors={survivors}")
        best_dir_initialized = True

    print(f"Finished validate-only. Total survivors={total_survivors} Best folder: {best_dir}")
    return 0


def add_common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--terminal",
        default=r"C:\Program Files\MetaTrader 5\terminal64.exe",
        help="Full path to terminal64.exe",
    )
    p.add_argument(
        "--expert",
        default=os.environ.get("MT5_EXPERT", "").strip(),
        help=r"EA filename inside MQL5\Experts (e.g. MyEA.ex5). Or set MT5_EXPERT.",
    )
    p.add_argument("--deposit", default="100000")
    p.add_argument("--currency", default="USD")
    p.add_argument("--leverage", default="1:33")
    p.add_argument("--portable", action="store_true")
    p.add_argument(
        "--mt5-data",
        default="",
        help="MT5 data directory (auto-detected when omitted)",
    )
    p.add_argument("--reports-dir", default="reports")
    p.add_argument("--work-dir", default=".", help="Folder for generated files/logs")
    p.add_argument("--best-dir", default="", help=f"Defaults to {DEFAULT_BEST_DIR}")
    p.add_argument(
        "--validate-set-dir",
        default="",
        help="Directory of MT5 .set parameter grids (or set MT5_SET_DIR)",
    )
    p.add_argument(
        "--validate-top-n-per-symbol",
        type=int,
        default=DEFAULT_VALIDATE_TOP_N_PER_SYMBOL,
        help="Top optimization passes per symbol to backtest (default: 25)",
    )
    p.add_argument(
        "--validate-keep-top-k",
        type=int,
        default=DEFAULT_VALIDATE_KEEP_TOP_K,
        help="Max survivors per job after validation ranking (default: 25)",
    )
    p.add_argument(
        "--backtest-timeout-seconds",
        type=float,
        default=DEFAULT_BACKTEST_TIMEOUT_SEC,
        help="Max seconds per validation backtest (default: 300)",
    )
    p.add_argument(
        "--min-sharpe",
        default=str(DEFAULT_MIN_SHARPE),
        help=f"Min Sharpe (>=) for back, forward, and real-ticks validation (default: {DEFAULT_MIN_SHARPE})",
    )
    p.add_argument(
        "--min-forward-result",
        default=str(DEFAULT_MIN_FORWARD_RESULT),
        help="Min forward Custom/Result (>=; default: 3)",
    )
    p.add_argument(
        "--min-back-result",
        default=str(DEFAULT_MIN_BACK_RESULT),
        help="Min optimization Custom/Result (>=; default: 6)",
    )
    p.add_argument(
        "--min-validation-cagr",
        default=str(DEFAULT_MIN_VALIDATION_CAGR),
        help="Min CAGR %% on real ticks (default: 10)",
    )
    p.add_argument(
        "--max-equity-dd",
        type=float,
        default=DEFAULT_MAX_EQUITY_DD,
        help="Max equity DD %% after scaling; OHLC or real ticks above this = non-linear (default: 17)",
    )
    p.add_argument(
        "--no-risk-scaling",
        action="store_true",
        help="Disable post-validation RISK scaling to target equity DD",
    )
    p.add_argument(
        "--target-equity-dd",
        type=float,
        default=DEFAULT_TARGET_EQUITY_DD,
        help="Linear RISK scaling target equity DD %% (default: 15; scaled_RISK = RISK * target / baseline_DD)",
    )
    p.add_argument(
        "--min-scaled-risk",
        type=float,
        default=DEFAULT_MIN_SCALED_RISK,
        help="Reject when linear scaling yields scaled RISK below this (default: 1.0)",
    )
    p.add_argument("--col-pass", default="", help="Override Pass column name")
    p.add_argument("--col-custom", default="", help="Override Custom column name")
    p.add_argument("--col-result", default="", help="Override Result column name")
    p.add_argument("--col-back-result", default="", help="Override Back Result column name")
    p.add_argument("--col-forward-result", default="", help="Override Forward Result column name")
    p.add_argument("--col-sharpe", default="", help="Override Sharpe column name")
    p.add_argument("--col-recovery", default="", help="Override Recovery column name")
    p.add_argument("--col-dd", default="", help="Override Equity DD column name")
    p.add_argument("--col-trades", default="", help="Override Trades column name")
    p.add_argument("--col-profit", default="", help="Override Profit column name")
    p.add_argument("--verbose", action="store_true", help="Print mapping, samples, filter stats")
    p.add_argument(
        "--allow-zero-metrics",
        action="store_true",
        help="Treat missing validation metrics as 0 instead of empty",
    )


def _apply_derived_args(args: argparse.Namespace) -> None:
    if getattr(args, "complete_opt", False):
        args.optimization = COMPLETE_OPTIMIZATION_MODE
        args.model = COMPLETE_OPTIMIZATION_MODEL


def main() -> int:
    load_repo_env()
    p = argparse.ArgumentParser(description="Batch-run MT5 optimizations with per-job validation")
    add_common_args(p)

    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--validate-only",
        action="store_true",
        help="Skip optimization; validate reports in reports/ (honors --symbols and --timeframes)",
    )
    mode.add_argument(
        "--no-validate",
        action="store_true",
        help="Run optimizations only, without per-job validation",
    )

    default_symbols = [
        "EURUSD", "GBPUSD", "USDCAD", "AUDUSD", "NZDUSD", "USDJPY", "USDCHF",
        "EURGBP", "EURCAD", "EURAUD", "EURNZD", "EURJPY", "EURCHF",
        "GBPCAD", "GBPAUD", "GBPNZD", "GBPJPY", "GBPCHF",
        "AUDCAD", "NZDCAD", "CADJPY", "CADCHF",
        "AUDNZD", "AUDJPY", "AUDCHF", "NZDJPY", "NZDCHF", "CHFJPY",
        "BTCUSD", "XAUUSD", "US500", "US500.cash", "SP500",
    ]
    default_timeframes = ["M15", "H1", "H4"]
    p.add_argument("--symbols", nargs="+", default=default_symbols)
    p.add_argument("--timeframes", nargs="+", default=default_timeframes)
    p.add_argument("--param-files", nargs="+", default=None)
    p.add_argument(
        "--strategies",
        nargs="+",
        default=None,
        help="Restrict to strategy folder names under --validate-set-dir (default: all discovered)",
    )
    p.add_argument("--from-date", help="YYYY.MM.DD (required unless --validate-only)")
    p.add_argument("--to-date", help="YYYY.MM.DD (required unless --validate-only)")
    p.add_argument("--optimization", default=DEFAULT_OPTIMIZATION_MODE, choices=["0", "1", "2", "3"])
    p.add_argument("--criterion", default="6")
    p.add_argument("--model", default=DEFAULT_OPTIMIZATION_MODEL, choices=["0", "1", "2", "3", "4"])
    p.add_argument(
        "--complete-opt",
        action="store_true",
        help="Use complete optimization on every tick real ticks (Optimization=1, Model=4)",
    )
    p.add_argument("--execution-mode", default="-1")
    p.add_argument("--forward-mode", default="2", choices=["0", "1", "2", "3", "4"])
    p.add_argument("--forward-date", default="")
    p.add_argument("--use-local", default="1", choices=["0", "1"])
    p.add_argument("--use-remote", default="0", choices=["0", "1"])
    p.add_argument("--use-cloud", default="0", choices=["0", "1"])
    p.add_argument("--replace-report", default="1", choices=["0", "1"])
    p.add_argument("--shutdown-terminal", default="1", choices=["0", "1"])
    p.add_argument("--visual", default="0", choices=["0", "1"])
    p.add_argument("--login", default="")
    p.add_argument("--port", default="")
    p.add_argument("--config-dir", default="generated_configs")
    p.add_argument("--resume", action="store_true")
    p.add_argument("--delay-seconds", type=float, default=2.0)
    p.add_argument("--timeout-minutes", type=float, default=0.0)
    args = p.parse_args()
    _apply_derived_args(args)

    if not args.expert.strip():
        raise ValueError("--expert or MT5_EXPERT is required")

    if args.validate_only:
        return run_validate_only(args)

    if not args.from_date or not args.to_date:
        raise ValueError("--from-date and --to-date are required unless --validate-only")
    if args.forward_mode == "4" and not args.forward_date.strip():
        raise ValueError("--forward-date is required when --forward-mode=4")

    auto_validate = not args.no_validate
    terminal = Path(args.terminal).expanduser().resolve()
    ensure_exists(terminal, "MT5 terminal")

    work_dir = Path(args.work_dir).expanduser().resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    config_dir = work_dir / args.config_dir
    reports_dir = work_dir / args.reports_dir
    config_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    install_dir = terminal.parent
    data_dir = resolve_mt5_data_dir(
        terminal=terminal,
        portable=args.portable,
        mt5_data=args.mt5_data or None,
    )
    tester_profiles_dir = data_dir / "MQL5" / "Profiles" / "Tester"
    tester_profiles_dir.mkdir(parents=True, exist_ok=True)

    set_dir = (
        Path(args.validate_set_dir).expanduser().resolve()
        if args.validate_set_dir.strip()
        else resolve_set_dir(required=True)
    )
    if auto_validate and not set_dir.is_dir():
        raise FileNotFoundError(f"Set directory not found: {set_dir}")

    if not args.param_files:
        args.param_files = default_param_file_paths(set_dir)
    if not args.param_files:
        raise FileNotFoundError(f"No .set files found under {set_dir}")

    if args.strategies:
        allowed = {s.strip() for s in args.strategies}
        known = set(discover_strategies(set_dir))
        unknown = allowed - known
        if unknown:
            raise ValueError(
                f"Unknown strategies: {sorted(unknown)}. "
                f"Discovered under {set_dir}: {sorted(known)}"
            )

    best_dir = (
        Path(args.best_dir).expanduser().resolve()
        if args.best_dir
        else DEFAULT_BEST_DIR
    )

    normalized_param_files, set_index = stage_param_files(
        filter_paths_by_strategies(args.param_files, args.strategies, set_dir=set_dir)
        if args.strategies
        else args.param_files,
        set_dir=set_dir,
        tester_profiles_dir=tester_profiles_dir,
    )
    if not set_index:
        set_index = discover_set_files(set_dir)

    chart_tfs = discover_chart_tfs(set_dir)
    for tf in args.timeframes:
        tf_key = tf.upper()
        if tf_key not in VALID_PERIODS:
            raise ValueError(f"Unsupported timeframe: {tf}. Allowed: {sorted(VALID_PERIODS)}")
        if chart_tfs and tf_key not in chart_tfs:
            raise ValueError(
            f"No .set files for chart timeframe {tf_key}. "
                f"Available under {set_dir}: {sorted(chart_tfs)}"
            )

    param_files_by_timeframe = {
        tf.upper(): param_files_for_timeframe(tf, normalized_param_files)
        for tf in args.timeframes
    }
    missing_tf_sets = [
        tf for tf, files in param_files_by_timeframe.items() if not files
    ]
    if missing_tf_sets:
        raise ValueError(
            f"No staged set files for timeframe(s) {missing_tf_sets}. "
            f"Check --param-files against --timeframes "
            f"(nested layout uses <Strategy>_<TF>_*.set flat names)."
        )
    jobs = build_jobs(
        args.symbols,
        args.timeframes,
        param_files_by_timeframe,
        config_dir,
        reports_dir,
        runs_per_set_file=DEFAULT_RUNS_PER_SET_FILE,
    )
    if not jobs:
        raise ValueError(
            "No optimization jobs generated. Check --symbols, --timeframes, and --param-files."
        )
    if DEFAULT_RUNS_PER_SET_FILE > 1:
        print(
            f"Scheduled {len(jobs)} jobs "
            f"({DEFAULT_RUNS_PER_SET_FILE} runs per set file; each validated independently)"
        )
    run_log_path = work_dir / "mt5_batch_runs.csv"

    if not run_log_path.exists():
        with run_log_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(asdict(jobs[0]).keys()))
            writer.writeheader()

    batch_started = time.time()
    completed_count = 0
    # Prior validation output means append/keep Best; do not wipe on first post-resume validate.
    best_dir_initialized = (best_dir / "best_summary.csv").is_file()

    db_reporter = create_reporter()
    db_reporter.run_started(
        total_jobs=len(jobs),
        from_date=args.from_date,
        to_date=args.to_date,
        symbols=list(args.symbols),
        timeframes=[tf.upper() for tf in args.timeframes],
        resume=bool(args.resume),
    )

    try:
        exit_code = _run_batch_jobs(
            args=args,
            jobs=jobs,
            terminal=terminal,
            install_dir=install_dir,
            data_dir=data_dir,
            work_dir=work_dir,
            set_dir=set_dir,
            set_index=set_index,
            best_dir=best_dir,
            auto_validate=auto_validate,
            run_log_path=run_log_path,
            batch_started=batch_started,
            best_dir_initialized=best_dir_initialized,
            db_reporter=db_reporter,
        )
    except Exception as exc:
        db_reporter.run_failed(str(exc))
        raise
    else:
        db_reporter.run_completed(status="completed" if exit_code == 0 else "failed")
    finally:
        db_reporter.close()

    return exit_code


def _run_batch_jobs(
    *,
    args: argparse.Namespace,
    jobs: list[Job],
    terminal: Path,
    install_dir: Path,
    data_dir: Path,
    work_dir: Path,
    set_dir: Path,
    set_index: dict[str, Path],
    best_dir: Path,
    auto_validate: bool,
    run_log_path: Path,
    batch_started: float,
    best_dir_initialized: bool,
    db_reporter: Any | None,
) -> int:
    completed_count = 0
    best_initialized = best_dir_initialized

    for job in jobs:
        report_xml = Path(job.report_path + ".xml")
        report_htm = Path(job.report_path + ".htm")
        report_forward_xml = Path(job.report_path + ".forward.xml")
        report_forward_htm = Path(job.report_path + ".forward.htm")
        if args.resume and optimization_reports_complete(
            report_xml,
            report_forward_xml,
            forward_mode=args.forward_mode,
        ):
            job.status = "skipped_existing"
            completed_count += 1
            elapsed_batch = time.time() - batch_started
            avg_job_sec = elapsed_batch / completed_count if completed_count else None
            remaining_jobs = len(jobs) - completed_count
            eta_sec = (avg_job_sec * remaining_jobs) if avg_job_sec is not None else None
            pct = (completed_count / len(jobs)) * 100 if jobs else 0.0
            progress = render_progress(completed_count, len(jobs))
            with run_log_path.open("a", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=list(asdict(job).keys())).writerow(asdict(job))
            print(f"[{job.index}/{len(jobs)}] skip {job.symbol} {job.timeframe} {job.param_file} (existing report)")
            print(f"    {progress} {pct:5.1f}% | completed {completed_count}/{len(jobs)} | avg/job {format_seconds(avg_job_sec)} | ETA {format_seconds(eta_sec)}")
            db_reporter.job_completed(
                job_index=job.index,
                total_jobs=len(jobs),
                symbol=job.symbol,
                timeframe=job.timeframe,
                param_file=job.param_file,
                report_stem=job.report_stem,
                status=job.status,
            )
            continue

        ini_cfg = {
            "Expert": args.expert,
            "ExpertParameters": job.param_file,
            "Symbol": job.symbol,
            "Period": job.timeframe,
            "Login": args.login,
            "Model": args.model,
            "ExecutionMode": args.execution_mode,
            "Optimization": args.optimization,
            "OptimizationCriterion": args.criterion,
            "FromDate": args.from_date,
            "ToDate": args.to_date,
            "ForwardMode": args.forward_mode,
            "ForwardDate": args.forward_date,
            "Report": os.path.relpath(job.report_path, data_dir).replace("/", "\\"),
            "ReplaceReport": args.replace_report,
            "ShutdownTerminal": args.shutdown_terminal,
            "Deposit": args.deposit,
            "Currency": args.currency,
            "Leverage": args.leverage,
            "UseLocal": args.use_local,
            "UseRemote": args.use_remote,
            "UseCloud": args.use_cloud,
            "Visual": args.visual,
            "Port": args.port,
        }
        ini_path = Path(job.ini_path)
        write_ini(ini_path, ini_cfg)

        cmd = [str(terminal)]
        if args.portable:
            cmd.append("/portable")
        cmd.append(f"/config:{ini_path}")

        elapsed_batch = time.time() - batch_started
        avg_job_sec = (elapsed_batch / completed_count) if completed_count else None
        remaining_jobs = len(jobs) - completed_count
        eta_sec = (avg_job_sec * remaining_jobs) if avg_job_sec is not None else None
        pct = (completed_count / len(jobs)) * 100 if jobs else 0.0
        progress = render_progress(completed_count, len(jobs))
        print(f"[{job.index}/{len(jobs)}] start {job.symbol} {job.timeframe} {job.param_file}")
        print(f"    {progress} {pct:5.1f}% | completed {completed_count}/{len(jobs)} | avg/job {format_seconds(avg_job_sec)} | ETA {format_seconds(eta_sec)}")
        db_reporter.job_started(
            job_index=job.index,
            total_jobs=len(jobs),
            symbol=job.symbol,
            timeframe=job.timeframe,
            param_file=job.param_file,
            report_stem=job.report_stem,
        )
        stop_running_terminal()
        started = time.time()
        proc = subprocess.Popen(cmd, cwd=str(install_dir))
        timeout = None if args.timeout_minutes <= 0 else args.timeout_minutes * 60.0
        try:
            proc.wait(timeout=timeout)
            job.exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            proc.kill()
            job.exit_code = -9
            job.status = "timeout"
            job.error = f"Timed out after {args.timeout_minutes} minutes"
        finally:
            job.duration_sec = round(time.time() - started, 2)

        if job.status != "timeout":
            report_exists = any(p.exists() for p in [report_xml, report_htm, report_forward_xml, report_forward_htm])
            if proc.returncode == 0 and report_exists:
                job.status = "done"
            elif report_exists:
                job.status = "done_with_nonzero_exit"
                job.error = f"MT5 exit code {proc.returncode}"
            else:
                job.status = "failed"
                job.error = f"No report generated; MT5 exit code {proc.returncode}"

        with run_log_path.open("a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=list(asdict(job).keys())).writerow(asdict(job))

        completed_count += 1
        elapsed_batch = time.time() - batch_started
        avg_job_sec = elapsed_batch / completed_count if completed_count else None
        remaining_jobs = len(jobs) - completed_count
        eta_sec = (avg_job_sec * remaining_jobs) if avg_job_sec is not None else None
        pct = (completed_count / len(jobs)) * 100 if jobs else 0.0
        progress = render_progress(completed_count, len(jobs))
        print(f"[{job.index}/{len(jobs)}] {job.status} in {job.duration_sec:.2f}s")
        print(f"    {progress} {pct:5.1f}% | completed {completed_count}/{len(jobs)} | avg/job {format_seconds(avg_job_sec)} | remaining {remaining_jobs} | ETA {format_seconds(eta_sec)}")
        db_reporter.job_completed(
            job_index=job.index,
            total_jobs=len(jobs),
            symbol=job.symbol,
            timeframe=job.timeframe,
            param_file=job.param_file,
            report_stem=job.report_stem,
            status=job.status,
            error=job.error or "",
        )

        if auto_validate and job.status in ("done", "done_with_nonzero_exit"):
            try:
                opt_xml = resolve_optimization_xml(job.report_path)
            except FileNotFoundError as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                return 1
            try:
                rows = validate_job(_validate_job_config_from_args(
                    args,
                    terminal=terminal,
                    install_dir=install_dir,
                    data_dir=data_dir,
                    work_dir=work_dir,
                    set_dir=set_dir,
                    set_index=set_index,
                    xml_path=opt_xml,
                    best_dir=best_dir,
                    reset_best_dir=not best_initialized,
                    append_summary=best_initialized,
                    fallback_symbol=job.symbol,
                    fallback_timeframe=job.timeframe,
                    db_reporter=db_reporter,
                ))
            except Exception as exc:
                print(f"ERROR: validation failed for {job.report_stem}: {exc}", file=sys.stderr)
                return 1
            survivors = sum(1 for r in rows if r.get("keep"))
            print(f"Validated {job.report_stem}: selected={len(rows)} survivors={survivors}")
            best_initialized = True

        time.sleep(args.delay_seconds)

    total_elapsed = time.time() - batch_started
    print(f"Finished {len(jobs)} jobs in {format_seconds(total_elapsed)}. Log: {run_log_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
