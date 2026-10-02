"""Versioned Favorites portfolio build manifest (combined MT5 tester input)."""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from mt5_optimization_set_paths import (
    describe_favorite_identity,
    resolve_favorite_source_set_file,
)
from mt5_opt_report import to_float
from mt5_paths import DEFAULT_BEST_DIR, DEFAULT_FAVORITES_DIR
from mt5_portfolio_merge import (
    build_company_portfolio_id,
    resolve_favorite_company_for_portfolio,
    resolve_strategy_report_path,
)
from mt5_workspace import PACKAGE_ROOT

# Bump when the combined-tester input contract changes.
PORTFOLIO_MANIFEST_VERSION = 3
# Real-tick every tick — the only model that can certify portfolio equity DD.
PORTFOLIO_TESTER_MODEL = 4
# Distinct magic ownership per favorite instance (base + index).
PORTFOLIO_MAGIC_BASE = 4_991_701_300
_DEPOSIT_TOLERANCE = 0.01
_PERIOD_RE = re.compile(
    r"\((\d{4}\.\d{2}\.\d{2})\s*-\s*(\d{4}\.\d{2}\.\d{2})\)"
)
_DATE_RE = re.compile(r"^\d{4}\.\d{2}\.\d{2}$")


class PortfolioManifestError(ValueError):
    """Incompatible or missing portfolio build inputs — never invent values."""


@dataclass(frozen=True)
class ManifestStrategy:
    result_id: str
    symbol: str
    timeframe: str
    profile: str
    pass_id: int
    set_file: str
    report_stem: str
    magic: int
    risk_pct: float | None
    parameters: dict[str, str]
    source_from_date: str | None = None
    source_to_date: str | None = None


@dataclass(frozen=True)
class PortfolioManifest:
    version: int
    portfolio_id: str
    company: str
    server: str | None
    from_date: str
    to_date: str
    deposit: float
    currency: str
    leverage: str
    tester_model: int
    strategies: tuple[ManifestStrategy, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["strategies"] = [asdict(s) for s in self.strategies]
        return payload


def _html_metrics(row: dict[str, Any]) -> dict[str, str]:
    report_metrics = row.get("report_metrics")
    if not isinstance(report_metrics, dict) or report_metrics.get("format") != "html":
        return {}
    raw = report_metrics.get("metrics")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, str) and value.strip():
            out[key] = value.strip()
    return out


def _metric(metrics: dict[str, str], *labels: str) -> str | None:
    for label in labels:
        exact = metrics.get(label)
        if exact and exact.strip():
            return exact.strip()
    lower_map = {key.lower(): value for key, value in metrics.items()}
    for label in labels:
        value = lower_map.get(label.lower())
        if value and value.strip():
            return value.strip()
    return None


def _normalize_currency(raw: str) -> str:
    return raw.strip().upper()


def _normalize_leverage(raw: str) -> str:
    text = raw.strip().replace(" ", "")
    if not text:
        raise PortfolioManifestError("Empty leverage")
    if text.startswith("1:"):
        return text
    if text.isdigit():
        return f"1:{text}"
    return text


def _normalize_date(raw: str) -> str:
    text = raw.strip().replace("-", ".")
    if not _DATE_RE.fullmatch(text):
        raise PortfolioManifestError(f"Invalid date {raw!r}")
    return text


def extract_period_dates(
    *,
    metrics: dict[str, str],
    from_date: str | None,
    to_date: str | None,
) -> tuple[str, str]:
    period = _metric(metrics, "Period")
    if period:
        match = _PERIOD_RE.search(period)
        if match:
            return _normalize_date(match.group(1)), _normalize_date(match.group(2))
    if from_date and to_date:
        return _normalize_date(from_date), _normalize_date(to_date)
    raise PortfolioManifestError(
        "Missing test window (report Period or run fromDate/toDate)"
    )


def extract_deposit(
    *,
    metrics: dict[str, str],
    summary: dict[str, Any] | None,
) -> float:
    if isinstance(summary, dict):
        deposit = to_float(summary.get("deposit"))
        if deposit is not None and deposit > 0:
            return float(deposit)
    raw = _metric(metrics, "Initial Deposit", "Initial deposit", "Deposit")
    if raw:
        parsed = to_float(raw.replace(" ", "").replace(",", ""))
        if parsed is not None and parsed > 0:
            return float(parsed)
    raise PortfolioManifestError("Missing deposit")


def extract_currency(*, metrics: dict[str, str], summary: dict[str, Any] | None) -> str:
    if isinstance(summary, dict):
        raw_summary = summary.get("currency")
        if isinstance(raw_summary, str) and raw_summary.strip():
            return _normalize_currency(raw_summary)
    raw = _metric(metrics, "Currency")
    if raw:
        return _normalize_currency(raw)
    raise PortfolioManifestError("Missing currency")


def extract_leverage(*, metrics: dict[str, str], summary: dict[str, Any] | None) -> str:
    if isinstance(summary, dict):
        raw_summary = summary.get("leverage")
        if isinstance(raw_summary, str) and raw_summary.strip():
            return _normalize_leverage(raw_summary)
    raw = _metric(metrics, "Leverage")
    if raw:
        return _normalize_leverage(raw)
    raise PortfolioManifestError("Missing leverage")


def extract_server(*, metrics: dict[str, str]) -> str | None:
    raw = _metric(metrics, "Server", "Trade Server")
    if raw:
        return raw.strip()
    return None


def assign_strategy_magic(result_id: str, index: int) -> int:
    """Deterministic distinct magic per favorite (stable across rebuilds)."""
    digest = hashlib.sha1(result_id.encode("utf-8")).hexdigest()
    offset = int(digest[:6], 16) % 90_000
    return PORTFOLIO_MAGIC_BASE + (index * 100_000) + offset


def _stringify_parameters(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items()}


def _require_identity(
    row: dict[str, Any],
) -> tuple[str, str, str, str, int, str]:
    result_id = str(row.get("id") or "").strip()
    symbol = str(row.get("symbol") or "").strip().upper()
    timeframe = str(row.get("timeframe") or "").strip()
    profile = str(row.get("profile") or "").strip()
    pass_raw = row.get("pass_id", row.get("passId"))
    if not result_id:
        raise PortfolioManifestError("Favorite missing result id")
    if not symbol or not timeframe or not profile:
        raise PortfolioManifestError(
            f"{result_id}: missing symbol/timeframe/profile"
        )
    try:
        pass_id = int(pass_raw)
    except (TypeError, ValueError) as exc:
        raise PortfolioManifestError(f"{result_id}: missing pass id") from exc
    report_stem = str(row.get("report_stem") or row.get("reportStem") or "").strip()
    if not report_stem:
        report_stem = f"{symbol}_{timeframe}_{profile}_pass{pass_id}"
    return result_id, symbol, timeframe, profile, pass_id, report_stem


def _resolve_set_path(
    row: dict[str, Any],
    *,
    best_dir: Path,
    favorites_dir: Path,
    repo_root: Path,
) -> Path:
    identity = {
        "symbol": row.get("symbol"),
        "timeframe": row.get("timeframe"),
        "profile": row.get("profile"),
        "pass_id": row.get("pass_id", row.get("passId")),
        "passId": row.get("passId", row.get("pass_id")),
    }
    param_file = row.get("param_file") or row.get("paramFile")
    summary = row.get("summary") if isinstance(row.get("summary"), dict) else None
    found = resolve_favorite_source_set_file(
        best_dir=best_dir,
        favorites_dir=favorites_dir,
        repo_root=repo_root,
        param_file=str(param_file) if param_file else None,
        summary=summary,
        identity=identity,
    )
    if found is None or not found.is_file():
        label = describe_favorite_identity(identity)
        raise PortfolioManifestError(f"{label}: missing set file")
    return found


def _require_realticks_report(
    row: dict[str, Any],
    *,
    best_dir: Path,
    favorites_dir: Path,
) -> Path:
    report_path = resolve_strategy_report_path(
        symbol=str(row.get("symbol") or ""),
        timeframe=str(row.get("timeframe") or ""),
        profile=str(row.get("profile") or "") or None,
        pass_id=row.get("pass_id", row.get("passId")),
        report_stem=str(row.get("report_stem") or row.get("reportStem") or "") or None,
        best_dir=best_dir,
        favorites_dir=favorites_dir,
    )
    if report_path is None:
        identity = {
            "symbol": row.get("symbol"),
            "timeframe": row.get("timeframe"),
            "profile": row.get("profile"),
            "pass_id": row.get("pass_id", row.get("passId")),
        }
        raise PortfolioManifestError(
            f"{describe_favorite_identity(identity)}: missing realticks report"
        )
    return report_path


def _risk_pct(row: dict[str, Any]) -> float | None:
    summary = row.get("summary")
    if isinstance(summary, dict):
        for key in ("scaled_risk", "baseline_risk", "risk"):
            value = to_float(summary.get(key))
            if value is not None:
                return float(value)
    parameters = row.get("parameters")
    if isinstance(parameters, dict):
        for key in ("RISK", "Risk", "risk"):
            value = to_float(parameters.get(key))
            if value is not None:
                return float(value)
    return None


def build_portfolio_manifest(
    rows: list[dict[str, Any]],
    *,
    company: str,
    best_dir: Path = DEFAULT_BEST_DIR,
    favorites_dir: Path = DEFAULT_FAVORITES_DIR,
    repo_root: Path | None = None,
) -> PortfolioManifest:
    """Assemble a versioned combined-tester manifest or raise PortfolioManifestError."""
    if not rows:
        raise PortfolioManifestError("No favorite strategies for portfolio manifest")

    resolved_company = resolve_favorite_company_for_portfolio(company)
    portfolio_id = build_company_portfolio_id(resolved_company)
    root = repo_root or PACKAGE_ROOT.parent.parent

    strategies: list[ManifestStrategy] = []
    deposits: list[float] = []
    currencies: list[str] = []
    leverages: list[str] = []
    windows: list[tuple[str, str]] = []
    servers: list[str | None] = []
    companies: list[str] = []

    for index, row in enumerate(rows):
        (
            result_id,
            symbol,
            timeframe,
            profile,
            pass_id,
            report_stem,
        ) = _require_identity(row)
        metrics = _html_metrics(row)
        raw_company = row.get("company")
        if not isinstance(raw_company, str) or not raw_company.strip():
            raw_company = _metric(metrics, "Company", "Broker", "Broker company")
        if not isinstance(raw_company, str) or not raw_company.strip():
            raise PortfolioManifestError(f"{result_id}: missing company")
        row_company = raw_company.strip()
        companies.append(row_company)
        if row_company.casefold() != resolved_company.casefold():
            raise PortfolioManifestError(
                f"{result_id}: company {row_company!r} != portfolio {resolved_company!r}"
            )

        summary = row.get("summary") if isinstance(row.get("summary"), dict) else None
        report_path = _require_realticks_report(
            row,
            best_dir=best_dir,
            favorites_dir=favorites_dir,
        )
        # Older API exports may omit Period and account settings. The exact
        # real-tick report is required anyway, so use it to fill those gaps.
        from mt5_db_report import extract_full_report_metrics

        report_metrics = extract_full_report_metrics(report_path)
        if report_metrics.get("format") != "html":
            raise PortfolioManifestError(f"{result_id}: realticks report is not HTML")
        metrics = {**(report_metrics.get("metrics") or {}), **metrics}
        from_date, to_date = extract_period_dates(
            metrics=metrics,
            from_date=str(row.get("from_date") or row.get("fromDate") or "") or None,
            to_date=str(row.get("to_date") or row.get("toDate") or "") or None,
        )
        deposit = extract_deposit(metrics=metrics, summary=summary)
        currency = extract_currency(metrics=metrics, summary=summary)
        leverage = extract_leverage(metrics=metrics, summary=summary)
        server = extract_server(metrics=metrics)
        set_path = _resolve_set_path(
            row,
            best_dir=best_dir,
            favorites_dir=favorites_dir,
            repo_root=root,
        )
        deposits.append(deposit)
        currencies.append(currency)
        leverages.append(leverage)
        windows.append((from_date, to_date))
        servers.append(server)
        strategies.append(
            ManifestStrategy(
                result_id=result_id,
                symbol=symbol,
                timeframe=timeframe,
                profile=profile,
                pass_id=pass_id,
                set_file=set_path.name,
                report_stem=report_stem,
                magic=assign_strategy_magic(result_id, index),
                risk_pct=_risk_pct(row),
                parameters=_stringify_parameters(row.get("parameters")),
                source_from_date=from_date,
                source_to_date=to_date,
            )
        )

    if len({c.casefold() for c in companies}) != 1:
        raise PortfolioManifestError("Favorites span multiple companies")

    if max(deposits) - min(deposits) > _DEPOSIT_TOLERANCE:
        raise PortfolioManifestError(
            f"Incompatible deposits {sorted(set(deposits))}; refusing to pick max/min"
        )
    if len(set(currencies)) != 1:
        raise PortfolioManifestError(f"Incompatible currencies {sorted(set(currencies))}")
    if len(set(leverages)) != 1:
        raise PortfolioManifestError(f"Incompatible leverages {sorted(set(leverages))}")
    # The tester runs across the union. The generated EA activates each
    # strategy only within its own source window, carrying account balance
    # forward when the windows do not overlap.
    from_date = min(start for start, _ in windows)
    to_date = max(end for _, end in windows)

    present_servers = {s for s in servers if s}
    if len(present_servers) > 1:
        raise PortfolioManifestError(
            f"Incompatible servers {sorted(present_servers)}"
        )
    # Partial server presence is incomplete — do not invent the missing ones.
    if present_servers and any(s is None for s in servers):
        raise PortfolioManifestError(
            "Incomplete server metadata across favorites"
        )

    return PortfolioManifest(
        version=PORTFOLIO_MANIFEST_VERSION,
        portfolio_id=portfolio_id,
        company=resolved_company,
        server=next(iter(present_servers), None),
        from_date=from_date,
        to_date=to_date,
        deposit=deposits[0],
        currency=currencies[0],
        leverage=leverages[0],
        tester_model=PORTFOLIO_TESTER_MODEL,
        strategies=tuple(strategies),
    )
