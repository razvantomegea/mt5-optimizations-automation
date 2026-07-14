"""Equity-curve quality metrics from MT5 backtest HTML reports."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from mt5_opt_report import read_report_text, to_float

SECONDS_PER_YEAR = 365.25 * 86400.0


@dataclass
class EquityQualityMetrics:
    lr_correlation: float
    lr_std_error: float
    cagr_pct: float
    calmar: float
    k_ratio_proxy: float
    ulcer_index: float
    max_stagnation_days: int
    time_under_water_pct: float
    initial_balance: float
    final_balance: float
    test_years: float


def _read_report_text(report_path: Path) -> str:
    return read_report_text(report_path)


def _extract_html_stat(text: str, label: str) -> float | None:
    pattern = re.escape(label) + r":.*?<b>([-\d.\s]+)"
    match = re.search(pattern, text, re.S)
    if not match:
        return None
    raw = match.group(1).replace(" ", "").replace(",", "")
    return to_float(raw)


def _parse_mt5_datetime(value: str) -> datetime | None:
    value = value.strip()
    for fmt in ("%Y.%m.%d %H:%M:%S", "%Y.%m.%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _parse_balance_rows(html: str) -> tuple[list[datetime], list[float]]:
    times: list[datetime] = []
    balances: list[float] = []

    section_html = _deals_section_html(html)
    if not section_html:
        return times, balances

    row_pattern = re.compile(r"<tr[^>]*>.*?</tr>", re.S | re.I)
    cell_pattern = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)
    tag_pattern = re.compile(r"<[^>]+>")

    for row_match in row_pattern.finditer(section_html):
        row_html = row_match.group(0)
        if "<b>Balance</b>" in row_html or "<b>Time</b>" in row_html:
            continue
        cells = cell_pattern.findall(row_html)
        if len(cells) < 13:
            continue
        time_raw = tag_pattern.sub("", cells[0]).strip()
        balance_raw = tag_pattern.sub("", cells[11]).strip().replace(" ", "").replace(",", "")
        parsed_time = _parse_mt5_datetime(time_raw)
        parsed_balance = to_float(balance_raw)
        if parsed_time is None or parsed_balance is None:
            continue
        times.append(parsed_time)
        balances.append(parsed_balance)

    return times, balances


def parse_balance_rows(report_path: Path) -> tuple[list[datetime], list[float]]:
    """Public: parse (time, balance) series from an MT5 HTML backtest report."""
    return _parse_balance_rows(_read_report_text(report_path))


EXIT_DEAL_DIRECTIONS = frozenset({"out", "in/out"})
TRADE_DEAL_DIRECTIONS = frozenset({"in", "out", "in/out"})
_DEALS_SECTION_END_MARKERS = (
    re.compile(r"<b>Workings</b>", re.I),
    re.compile(r"<b>Results</b>", re.I),
)


@dataclass(frozen=True)
class DealEvent:
    time: datetime
    direction: str
    balance_delta: float
    equity_before: float
    balance_after: float
    is_closed_trade: bool


@dataclass(frozen=True)
class _ParsedDealRow:
    time: datetime
    symbol: str
    deal_type: str
    direction: str
    volume: float
    price: float
    profit: float
    balance: float


@dataclass
class _OpenLot:
    volume: float
    entry_price: float
    is_long: bool


DEFAULT_FOREX_CONTRACT_SIZE = 100_000.0
MIN_FOREX_CONTRACT_SIZE = 50_000.0
MAX_FOREX_CONTRACT_SIZE = 200_000.0
MIN_LOT_VOLUME = 1e-8


def _clean_cell_text(raw: str) -> str:
    return re.sub(r"<[^>]+>", "", raw).strip()


def _infer_contract_size(*, profit: float, volume: float, entry: float, exit_price: float) -> float:
    price_diff = abs(exit_price - entry)
    if price_diff <= 0 or volume <= 0 or profit == 0:
        return DEFAULT_FOREX_CONTRACT_SIZE
    inferred = abs(profit) / (price_diff * volume)
    if not (MIN_FOREX_CONTRACT_SIZE <= inferred <= MAX_FOREX_CONTRACT_SIZE):
        return DEFAULT_FOREX_CONTRACT_SIZE
    return inferred


def _parse_deal_trade_rows(html: str) -> list[_ParsedDealRow]:
    section_html = _deals_section_html(html)
    if not section_html:
        return []

    rows: list[_ParsedDealRow] = []
    row_pattern = re.compile(r"<tr[^>]*>.*?</tr>", re.S | re.I)
    cell_pattern = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)

    for row_match in row_pattern.finditer(section_html):
        row_html = row_match.group(0)
        if "<b>Balance</b>" in row_html or "<b>Time</b>" in row_html:
            continue
        cells = cell_pattern.findall(row_html)
        if len(cells) < 13:
            continue
        direction = _clean_cell_text(cells[4]).casefold()
        if direction not in TRADE_DEAL_DIRECTIONS:
            continue
        deal_type = _clean_cell_text(cells[3]).casefold()
        if deal_type not in {"buy", "sell"}:
            continue
        symbol = _clean_cell_text(cells[2])
        if not symbol:
            continue
        time_raw = _clean_cell_text(cells[0])
        parsed_time = _parse_mt5_datetime(time_raw)
        volume = to_float(_clean_cell_text(cells[5]).replace(" ", ""))
        price = to_float(_clean_cell_text(cells[6]).replace(" ", ""))
        profit = to_float(_clean_cell_text(cells[10]).replace(" ", "").replace(",", ""))
        balance = to_float(_clean_cell_text(cells[11]).replace(" ", "").replace(",", ""))
        if parsed_time is None or volume is None or price is None or balance is None:
            continue
        rows.append(
            _ParsedDealRow(
                time=parsed_time,
                symbol=symbol,
                deal_type=deal_type,
                direction=direction,
                volume=volume,
                price=price,
                profit=profit or 0.0,
                balance=balance,
            )
        )
    return rows


def _floating_pnl(*, lots: list[_OpenLot], mark_price: float, contract_size: float) -> float:
    total = 0.0
    for lot in lots:
        if lot.is_long:
            total += (mark_price - lot.entry_price) * lot.volume * contract_size
        else:
            total += (lot.entry_price - mark_price) * lot.volume * contract_size
    return total


def _close_lots_fifo(
    *,
    lots: list[_OpenLot],
    close_long: bool,
    volume: float,
    exit_price: float,
    contract_sizes: dict[str, float],
    symbol: str,
    realized_profit: float,
) -> None:
    remaining = volume
    kept: list[_OpenLot] = []
    closed_volume = 0.0
    weighted_entry = 0.0

    for lot in lots:
        if remaining <= 0:
            kept.append(lot)
            continue
        if lot.is_long != close_long:
            kept.append(lot)
            continue
        closed = min(lot.volume, remaining)
        weighted_entry += lot.entry_price * closed
        closed_volume += closed
        if closed < lot.volume:
            kept.append(
                _OpenLot(
                    volume=lot.volume - closed,
                    entry_price=lot.entry_price,
                    is_long=lot.is_long,
                )
            )
        remaining -= closed

    lots[:] = [lot for lot in kept if lot.volume >= MIN_LOT_VOLUME]
    if closed_volume > 0 and realized_profit != 0:
        contract_sizes[symbol] = _infer_contract_size(
            profit=realized_profit,
            volume=closed_volume,
            entry=weighted_entry / closed_volume,
            exit_price=exit_price,
        )


def _apply_deal_row(
    *,
    row: _ParsedDealRow,
    open_lots: dict[str, list[_OpenLot]],
    contract_sizes: dict[str, float],
) -> None:
    symbol_lots = open_lots.setdefault(row.symbol, [])
    is_long_open = row.deal_type == "buy"
    if row.direction == "in":
        symbol_lots.append(_OpenLot(volume=row.volume, entry_price=row.price, is_long=is_long_open))
        return
    if row.direction == "out":
        _close_lots_fifo(
            lots=symbol_lots,
            close_long=not is_long_open,
            volume=row.volume,
            exit_price=row.price,
            contract_sizes=contract_sizes,
            symbol=row.symbol,
            realized_profit=row.profit,
        )
        return
    _close_lots_fifo(
        lots=symbol_lots,
        close_long=is_long_open,
        volume=row.volume,
        exit_price=row.price,
        contract_sizes=contract_sizes,
        symbol=row.symbol,
        realized_profit=row.profit,
    )
    symbol_lots.append(_OpenLot(volume=row.volume, entry_price=row.price, is_long=is_long_open))


def reconstruct_deal_equity_series(
    html: str,
    *,
    initial_deposit: float,
) -> list[tuple[datetime, float]]:
    """Estimate account equity after each deal from MT5 HTML deal rows."""
    rows = _parse_deal_trade_rows(html)
    if not rows:
        return []

    open_lots: dict[str, list[_OpenLot]] = {}
    contract_sizes: dict[str, float] = {}
    last_mark: dict[str, float] = {}
    points: list[tuple[datetime, float]] = []

    for row in rows:
        _apply_deal_row(row=row, open_lots=open_lots, contract_sizes=contract_sizes)
        last_mark[row.symbol] = row.price
        floating = 0.0
        for symbol, lots in open_lots.items():
            if not lots:
                continue
            mark_price = last_mark.get(symbol, row.price)
            contract_size = contract_sizes.get(symbol, DEFAULT_FOREX_CONTRACT_SIZE)
            floating += _floating_pnl(lots=lots, mark_price=mark_price, contract_size=contract_size)
        points.append((row.time, row.balance + floating))

    return points


def reconstruct_deal_equity_by_time(
    html: str,
    *,
    initial_deposit: float,
) -> dict[datetime, float]:
    """Map timestamp -> equity; duplicate timestamps keep the last value only."""
    return {
        point_time: equity
        for point_time, equity in reconstruct_deal_equity_series(
            html,
            initial_deposit=initial_deposit,
        )
    }


def attach_equity_to_deal_events(
    events: list[DealEvent],
    equity_series: list[tuple[datetime, float]],
) -> list[tuple[DealEvent, float | None]]:
    """Attach equity snapshots to deal events by index, or FIFO-by-timestamp when lengths differ."""
    if len(equity_series) == len(events):
        return [
            (event, equity_series[index][1])
            for index, event in enumerate(events)
        ]

    from collections import defaultdict, deque

    by_time: dict[datetime, deque[float]] = defaultdict(deque)
    for point_time, equity in equity_series:
        by_time[point_time].append(equity)

    attached: list[tuple[DealEvent, float | None]] = []
    for event in events:
        queue = by_time.get(event.time)
        if queue:
            attached.append((event, queue.popleft()))
        else:
            attached.append((event, None))
    return attached


def _deals_section_html(html: str) -> str:
    deals_match = re.search(r"<b>Deals</b>", html, re.I)
    if not deals_match:
        return ""
    section_end = len(html)
    for marker in _DEALS_SECTION_END_MARKERS:
        end_match = marker.search(html, deals_match.end())
        if end_match:
            section_end = min(section_end, end_match.start())
    return html[deals_match.start():section_end]


def _parse_deal_events(
    html: str,
    *,
    initial_deposit: float,
) -> list[DealEvent]:
    section_html = _deals_section_html(html)
    if not section_html:
        return []

    events: list[DealEvent] = []
    running_balance = initial_deposit

    row_pattern = re.compile(r"<tr[^>]*>.*?</tr>", re.S | re.I)
    cell_pattern = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)
    tag_pattern = re.compile(r"<[^>]+>")

    for row_match in row_pattern.finditer(section_html):
        row_html = row_match.group(0)
        if "<b>Balance</b>" in row_html or "<b>Time</b>" in row_html:
            continue
        cells = cell_pattern.findall(row_html)
        if len(cells) < 13:
            continue
        direction = tag_pattern.sub("", cells[4]).strip().casefold()
        if direction not in TRADE_DEAL_DIRECTIONS:
            continue
        time_raw = tag_pattern.sub("", cells[0]).strip()
        balance_raw = tag_pattern.sub("", cells[11]).strip().replace(" ", "").replace(",", "")
        parsed_time = _parse_mt5_datetime(time_raw)
        parsed_balance = to_float(balance_raw)
        if parsed_time is None or parsed_balance is None:
            continue
        equity_before = running_balance
        balance_delta = parsed_balance - equity_before
        if balance_delta == 0:
            running_balance = parsed_balance
            continue
        events.append(
            DealEvent(
                time=parsed_time,
                direction=direction,
                balance_delta=balance_delta,
                equity_before=equity_before,
                balance_after=parsed_balance,
                is_closed_trade=direction in EXIT_DEAL_DIRECTIONS,
            )
        )
        running_balance = parsed_balance

    return events


def parse_deal_events(
    report_path: Path,
    *,
    initial_deposit: float,
) -> list[DealEvent]:
    """Public: parse every trade deal (in + out) from an MT5 HTML report."""
    return _parse_deal_events(_read_report_text(report_path), initial_deposit=initial_deposit)


def _parse_exit_deal_rows(html: str) -> tuple[list[datetime], list[float]]:
    """Parse balance after each closed trade (exit / in-out deals only)."""
    deals_match = re.search(r"<b>Deals</b>", html, re.I)
    if not deals_match:
        return [], []

    section_end = len(html)
    for marker in _DEALS_SECTION_END_MARKERS:
        end_match = marker.search(html, deals_match.end())
        if end_match:
            section_end = min(section_end, end_match.start())

    section_html = html[deals_match.start():section_end]
    times: list[datetime] = []
    balances: list[float] = []

    row_pattern = re.compile(r"<tr[^>]*>.*?</tr>", re.S | re.I)
    cell_pattern = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)
    tag_pattern = re.compile(r"<[^>]+>")

    for row_match in row_pattern.finditer(section_html):
        row_html = row_match.group(0)
        if "<b>Balance</b>" in row_html or "<b>Time</b>" in row_html:
            continue
        cells = cell_pattern.findall(row_html)
        if len(cells) < 13:
            continue
        direction = tag_pattern.sub("", cells[4]).strip().casefold()
        if direction not in EXIT_DEAL_DIRECTIONS:
            continue
        time_raw = tag_pattern.sub("", cells[0]).strip()
        balance_raw = tag_pattern.sub("", cells[11]).strip().replace(" ", "").replace(",", "")
        parsed_time = _parse_mt5_datetime(time_raw)
        parsed_balance = to_float(balance_raw)
        if parsed_time is None or parsed_balance is None:
            continue
        times.append(parsed_time)
        balances.append(parsed_balance)

    return times, balances


def parse_exit_deal_rows(report_path: Path) -> tuple[list[datetime], list[float]]:
    """Public: parse (time, balance) at each closed trade from an MT5 HTML report."""
    return _parse_exit_deal_rows(_read_report_text(report_path))


def _years_between(t0: datetime, t1: datetime) -> float:
    if t1 <= t0:
        return 0.0
    return (t1 - t0).total_seconds() / SECONDS_PER_YEAR


def _lr_correlation(balance: list[float]) -> float:
    n = len(balance)
    if n < 2:
        return 0.0
    sum_x = sum(range(n))
    sum_y = sum(balance)
    sum_xy = sum(i * balance[i] for i in range(n))
    sum_x2 = sum(i * i for i in range(n))
    sum_y2 = sum(y * y for y in balance)
    denom = math.sqrt((n * sum_x2 - sum_x * sum_x) * (n * sum_y2 - sum_y * sum_y))
    if denom <= 0:
        return 0.0
    r = (n * sum_xy - sum_x * sum_y) / denom
    return max(-1.0, min(1.0, r))


def _lr_slope_std_error(values: list[float]) -> float:
    n = len(values)
    if n < 3:
        return 0.0
    sum_x = sum(range(n))
    sum_y = sum(values)
    sum_xy = sum(i * values[i] for i in range(n))
    sum_x2 = sum(i * i for i in range(n))
    denom = n * sum_x2 - sum_x * sum_x
    if abs(denom) < 1e-12:
        return 0.0
    slope = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n
    sse = sum((values[i] - (intercept + slope * i)) ** 2 for i in range(n))
    mse = sse / (n - 2)
    return math.sqrt(mse * n / denom)


def compute_k_ratio_proxy(balance: list[float]) -> float:
    log_values = [math.log(v) for v in balance if v > 0]
    if len(log_values) < 3:
        return 0.0
    se = _lr_slope_std_error(log_values)
    if se <= 1e-12:
        return 0.0
    n = len(log_values)
    sum_x = sum(range(n))
    sum_y = sum(log_values)
    sum_xy = sum(i * log_values[i] for i in range(n))
    sum_x2 = sum(i * i for i in range(n))
    denom = n * sum_x2 - sum_x * sum_x
    if abs(denom) < 1e-12:
        return 0.0
    slope = (n * sum_xy - sum_x * sum_y) / denom
    return slope / se


def compute_ulcer_index(balance: list[float]) -> float:
    if len(balance) < 2:
        return 0.0
    max_value = balance[0]
    sum_sq = 0.0
    for value in balance:
        if value > max_value:
            max_value = value
        if max_value <= 0:
            continue
        dd_pct = 100.0 * (value / max_value - 1.0)
        sum_sq += dd_pct * dd_pct
    return math.sqrt(sum_sq / len(balance))


def compute_max_stagnation_days(times: list[datetime], balance: list[float]) -> int:
    if len(times) < 2 or len(balance) != len(times):
        return 0
    max_value = balance[0]
    last_high_time = times[0]
    max_gap_days = 0.0
    for i in range(1, len(balance)):
        if balance[i] > max_value:
            max_value = balance[i]
            last_high_time = times[i]
            continue
        gap_days = (times[i] - last_high_time).total_seconds() / 86400.0
        max_gap_days = max(max_gap_days, gap_days)
    tail_gap = (times[-1] - last_high_time).total_seconds() / 86400.0
    return int(max(max_gap_days, tail_gap))


def compute_time_under_water_pct(times: list[datetime], balance: list[float]) -> float:
    if len(times) < 2 or len(balance) != len(times):
        return 0.0
    total_sec = (times[-1] - times[0]).total_seconds()
    if total_sec <= 0:
        return 0.0
    max_value = balance[0]
    under_water_sec = 0.0
    for i in range(1, len(balance)):
        span = (times[i] - times[i - 1]).total_seconds()
        if balance[i - 1] < max_value:
            under_water_sec += span
        if balance[i] > max_value:
            max_value = balance[i]
    return under_water_sec / total_sec


def compute_cagr_pct(initial: float, final: float, years: float) -> float:
    if initial <= 0 or final <= 0 or years <= 0:
        return 0.0
    return (math.pow(final / initial, 1.0 / years) - 1.0) * 100.0


def compute_calmar(profit_pct: float, years: float, max_dd_pct: float) -> float:
    safe_years = max(years, 0.01)
    safe_dd = max(max_dd_pct, 0.01)
    return (profit_pct / safe_years) / safe_dd


def compute_equity_quality_from_series(
    times: list[datetime],
    balance: list[float],
    *,
    lr_correlation: float | None = None,
    lr_std_error: float | None = None,
    equity_dd_pct: float | None = None,
) -> EquityQualityMetrics:
    if len(balance) < 2 or len(times) != len(balance):
        raise ValueError("Need at least two balance points with matching times")

    initial = balance[0]
    final = balance[-1]
    years = _years_between(times[0], times[-1])
    profit_pct = ((final - initial) / initial * 100.0) if initial > 0 else 0.0
    dd_pct = equity_dd_pct if equity_dd_pct is not None else 0.01

    lr = lr_correlation if lr_correlation is not None else _lr_correlation(balance)
    lr_se = lr_std_error if lr_std_error is not None else _lr_slope_std_error(balance)

    return EquityQualityMetrics(
        lr_correlation=lr,
        lr_std_error=lr_se,
        cagr_pct=compute_cagr_pct(initial, final, years),
        calmar=compute_calmar(profit_pct, years, dd_pct),
        k_ratio_proxy=compute_k_ratio_proxy(balance),
        ulcer_index=compute_ulcer_index(balance),
        max_stagnation_days=compute_max_stagnation_days(times, balance),
        time_under_water_pct=compute_time_under_water_pct(times, balance),
        initial_balance=initial,
        final_balance=final,
        test_years=years,
    )


def extract_margin_level_pct(report_path: Path) -> float | None:
    if report_path.suffix.lower() == ".xml":
        return None

    text = _read_report_text(report_path)
    match = re.search(r"Margin Level:.*?<b>([-\d.\s,]+)\s*%", text, re.S)
    if not match:
        return None
    raw = match.group(1).replace(" ", "").replace(",", "")
    return to_float(raw)


def extract_equity_quality(report_path: Path) -> EquityQualityMetrics:
    if report_path.suffix.lower() == ".xml":
        raise ValueError(f"Equity quality metrics require HTML report: {report_path}")

    text = _read_report_text(report_path)
    times, balances = _parse_balance_rows(text)
    if len(balances) < 2:
        raise ValueError(f"Could not parse balance series from {report_path}")

    lr = _extract_html_stat(text, "LR Correlation")
    lr_se = _extract_html_stat(text, "LR Standard Error")
    equity_dd = _extract_html_stat(text, "Equity Drawdown Relative")

    return compute_equity_quality_from_series(
        times,
        balances,
        lr_correlation=lr,
        lr_std_error=lr_se,
        equity_dd_pct=equity_dd,
    )


def validation_score(
    recovery: float,
    sharpe: float,
    equity: EquityQualityMetrics,
) -> float:
    if recovery < 0 or sharpe < 0:
        return 0.0
    return (
        recovery
        * sharpe
        * equity.lr_correlation
        * equity.calmar
        * max(equity.k_ratio_proxy, 0.0)
    )
