"""MT5 optimization XML parsing, column mapping, forward merge, and candidate filtering."""

from __future__ import annotations

import re
import statistics
from defusedxml import ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

NS = {"ss": "urn:schemas-microsoft-com:office:spreadsheet"}

COLUMN_ALIASES: dict[str, list[str]] = {
    "pass": ["Pass"],
    "custom": ["Custom", "OnTester"],
    "result": ["Result"],
    "back_result": ["Back Result", "Back result", "Back"],
    "forward_result": ["Forward Result", "Forward result", "Forward"],
    "sharpe": ["Sharpe Ratio", "Sharpe ratio", "Sharpe"],
    "recovery": ["Recovery Factor", "Recovery factor", "Recovery"],
    "equity_dd": ["Equity DD %", "Equity DD%", "Drawdown %", "Drawdown %"],
    "trades": ["Trades", "Total trades"],
    "profit": ["Profit", "Net Profit", "Total Net Profit"],
}

KNOWN_METRIC_HEADERS = {
    alias
    for aliases in COLUMN_ALIASES.values()
    for alias in aliases
} | {
    "Profit", "Expected Payoff", "Profit Factor",
}


@dataclass
class ColumnMapping:
    pass_col: str | None = None
    custom: str | None = None
    result: str | None = None
    back_result: str | None = None
    forward_result: str | None = None
    sharpe: str | None = None
    recovery: str | None = None
    equity_dd: str | None = None
    trades: str | None = None
    profit: str | None = None

    def metric_header_names(self) -> set[str]:
        names: set[str] = set()
        for name in (
            self.pass_col, self.custom, self.result,
            self.back_result, self.forward_result,
            self.sharpe, self.recovery, self.equity_dd, self.trades,
            self.profit,
        ):
            if name:
                names.add(name)
        return names

    def as_display_dict(self) -> dict[str, str | None]:
        return {
            "pass": self.pass_col,
            "custom": self.custom,
            "result": self.result,
            "back_result": self.back_result,
            "forward_result": self.forward_result,
            "sharpe": self.sharpe,
            "recovery": self.recovery,
            "equity_dd": self.equity_dd,
            "trades": self.trades,
            "profit": self.profit,
        }


@dataclass
class ColumnOverrides:
    pass_col: str | None = None
    custom: str | None = None
    result: str | None = None
    back_result: str | None = None
    forward_result: str | None = None
    sharpe: str | None = None
    recovery: str | None = None
    equity_dd: str | None = None
    trades: str | None = None
    profit: str | None = None


@dataclass
class ForwardReportInfo:
    back_path: Path
    forward_path: Path | None
    back_rows: int
    forward_rows: int
    forward_joined: int
    candidates_with_forward: int
    has_inline_forward_columns: bool
    forward_file_status: str


DEFAULT_MIN_SHARPE = 1.0

DEFAULT_MIN_BACK_CUSTOM = 6.0
DEFAULT_MIN_BACK_RESULT = DEFAULT_MIN_BACK_CUSTOM
DEFAULT_MIN_FORWARD_RESULT = 3.0


@dataclass
class SelectionThresholds:
    min_back_custom: float = DEFAULT_MIN_BACK_CUSTOM
    min_forward_result: float = DEFAULT_MIN_FORWARD_RESULT
    min_sharpe: float = DEFAULT_MIN_SHARPE


@dataclass
class SelectionStats:
    total_rows: int = 0
    rejected_back_sharpe: int = 0
    rejected_forward_sharpe: int = 0
    rejected_missing_forward_sharpe: int = 0
    rejected_forward_result: int = 0
    rejected_missing_forward_result: int = 0
    scan_stopped_at_custom_floor: bool = False
    accepted: int = 0
    top_candidates: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ParsedRow:
    pass_id: int
    custom: float
    back_result: float | None
    forward_result: float | None
    sharpe: float
    recovery: float
    equity_dd_pct: float
    trades: int
    profit: float | None
    params: dict[str, Any]
    raw: dict[str, Any]
    forward_sharpe: float | None = None
    forward_recovery: float | None = None


@dataclass
class WorksheetData:
    name: str
    headers: list[str]
    records: list[dict[str, Any]]


@dataclass
class WorkbookData:
    title: str
    worksheets: list[WorksheetData]


def _normalize_header(header: str) -> str:
    return header.strip().casefold()


def _find_header(headers: list[str], aliases: list[str]) -> str | None:
    norm_map = {_normalize_header(h): h for h in headers if h}
    for alias in aliases:
        hit = norm_map.get(_normalize_header(alias))
        if hit:
            return hit
    return None


def resolve_column_mapping(
    headers: list[str],
    overrides: ColumnOverrides | None = None,
) -> ColumnMapping:
    overrides = overrides or ColumnOverrides()
    mapping = ColumnMapping()

    def pick(field_name: str, aliases: list[str]) -> str | None:
        override = getattr(overrides, field_name, None)
        if override:
            if override in headers:
                return override
            raise ValueError(
                f"Column override --col-{field_name.replace('_', '-')}={override!r} "
                f"not found in report headers: {headers[:20]}"
            )
        return _find_header(headers, aliases)

    mapping.pass_col = pick("pass_col", COLUMN_ALIASES["pass"])
    mapping.custom = pick("custom", COLUMN_ALIASES["custom"])
    mapping.result = pick("result", COLUMN_ALIASES["result"])
    mapping.back_result = pick("back_result", COLUMN_ALIASES["back_result"])
    mapping.forward_result = pick("forward_result", COLUMN_ALIASES["forward_result"])
    mapping.sharpe = pick("sharpe", COLUMN_ALIASES["sharpe"])
    mapping.recovery = pick("recovery", COLUMN_ALIASES["recovery"])
    mapping.equity_dd = pick("equity_dd", COLUMN_ALIASES["equity_dd"])
    mapping.trades = pick("trades", COLUMN_ALIASES["trades"])
    mapping.profit = pick("profit", COLUMN_ALIASES["profit"])

    if not mapping.pass_col:
        raise ValueError(f"Required column Pass not found in headers: {headers[:20]}")
    if not mapping.sharpe:
        raise ValueError(f"Required column Sharpe Ratio not found in headers: {headers[:20]}")
    if not mapping.recovery:
        raise ValueError(f"Required column Recovery Factor not found in headers: {headers[:20]}")
    if not mapping.custom and not mapping.result:
        raise ValueError(
            f"Required column Custom or Result not found in headers: {headers[:20]}"
        )
    return mapping


def parse_cell_value(cell: ET.Element) -> Any:
    data = cell.find("ss:Data", NS)
    if data is None:
        return None
    text = data.text
    if text is None:
        return None
    t = data.attrib.get(f'{{{NS["ss"]}}}Type', "String")
    if t == "Number":
        try:
            if any(ch in text for ch in ".eE"):
                return float(text)
            return int(text)
        except Exception:
            try:
                return float(text)
            except Exception:
                return text
    if t == "Boolean":
        return text in ("1", "true", "True")
    return text


def _row_cell_values(row: ET.Element) -> list[Any]:
    """Parse ss:Row cells honoring optional ss:Index (1-based column positions)."""
    values: list[Any] = []
    next_col = 1
    index_key = "{urn:schemas-microsoft-com:office:spreadsheet}Index"
    for cell in row.findall("ss:Cell", NS):
        idx_attr = cell.attrib.get(index_key)
        if idx_attr is not None:
            next_col = int(idx_attr)
        while len(values) < next_col - 1:
            values.append(None)
        value = parse_cell_value(cell)
        if len(values) == next_col - 1:
            values.append(value)
        else:
            values[next_col - 1] = value
        next_col += 1
    return values


def _worksheet_records(ws: ET.Element) -> WorksheetData:
    name = ws.attrib.get("{urn:schemas-microsoft-com:office:spreadsheet}Name", "")
    table = ws.find("ss:Table", NS)
    rows = table.findall("ss:Row", NS) if table is not None else []
    if not rows:
        return WorksheetData(name=name, headers=[], records=[])
    headers = [str(v) for v in _row_cell_values(rows[0])]
    records: list[dict[str, Any]] = []
    for row in rows[1:]:
        values = _row_cell_values(row)
        if len(values) < len(headers):
            values += [None] * (len(headers) - len(values))
        records.append({headers[i]: values[i] for i in range(len(headers))})
    return WorksheetData(name=name, headers=headers, records=records)


def parse_optimization_workbook(xml_path: Path) -> WorkbookData:
    root = ET.parse(xml_path).getroot()
    title_el = root.find(".//{urn:schemas-microsoft-com:office:office}Title")
    title = title_el.text if title_el is not None else xml_path.stem
    worksheets = [
        _worksheet_records(ws)
        for ws in root.findall(".//ss:Worksheet", NS)
    ]
    return WorkbookData(title=title, worksheets=worksheets)


def primary_worksheet(workbook: WorkbookData) -> WorksheetData:
    for ws in workbook.worksheets:
        if ws.records:
            return ws
    if workbook.worksheets:
        return workbook.worksheets[0]
    return WorksheetData(name="", headers=[], records=[])


def worksheet_rows(xml_path: Path) -> tuple[str, list[str], list[dict[str, Any]]]:
    """Compatibility helper: first non-empty worksheet."""
    wb = parse_optimization_workbook(xml_path)
    ws = primary_worksheet(wb)
    return wb.title, ws.headers, ws.records


def read_report_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16")
    return raw.decode("utf-8", errors="ignore")


def to_float(v: Any, default: float | None = None) -> float | None:
    if v is None or v == "":
        return default
    try:
        return float(v)
    except Exception:
        return default


def to_int(v: Any, default: int = 0) -> int:
    try:
        return int(float(v))
    except Exception:
        return default


def _criterion_value(rec: dict[str, Any], mapping: ColumnMapping) -> float:
    if mapping.custom and rec.get(mapping.custom) is not None:
        return to_float(rec.get(mapping.custom), 0.0) or 0.0
    if mapping.result and rec.get(mapping.result) is not None:
        return to_float(rec.get(mapping.result), 0.0) or 0.0
    return 0.0


def _param_columns(headers: list[str], mapping: ColumnMapping) -> list[str]:
    metric_names = mapping.metric_header_names() | KNOWN_METRIC_HEADERS
    return [h for h in headers if h and h not in metric_names]


def _parse_rows_from_records(
    records: list[dict[str, Any]],
    headers: list[str],
    mapping: ColumnMapping,
) -> list[ParsedRow]:
    param_cols = _param_columns(headers, mapping)
    parsed: list[ParsedRow] = []
    for rec in records:
        pass_id = to_int(rec.get(mapping.pass_col or "Pass"))
        custom = _criterion_value(rec, mapping)
        sharpe = to_float(rec.get(mapping.sharpe), 0.0) or 0.0
        recovery = to_float(rec.get(mapping.recovery), 0.0) or 0.0
        equity_dd = to_float(rec.get(mapping.equity_dd), 0.0) or 0.0 if mapping.equity_dd else 0.0
        trades = to_int(rec.get(mapping.trades), 0) if mapping.trades else 0
        profit = to_float(rec.get(mapping.profit)) if mapping.profit else None

        back_result: float | None = None
        forward_result: float | None = None
        if mapping.back_result and rec.get(mapping.back_result) is not None:
            back_result = to_float(rec.get(mapping.back_result))
        if mapping.forward_result and rec.get(mapping.forward_result) is not None:
            forward_result = to_float(rec.get(mapping.forward_result))

        params = {k: rec.get(k) for k in param_cols if k and rec.get(k) is not None}
        parsed.append(ParsedRow(
            pass_id=pass_id,
            custom=custom,
            back_result=back_result,
            forward_result=forward_result,
            sharpe=sharpe,
            recovery=recovery,
            equity_dd_pct=equity_dd,
            trades=trades,
            profit=profit,
            params=params,
            raw=rec,
        ))
    return parsed


def resolve_back_and_forward_paths(xml_path: Path) -> tuple[Path, Path | None]:
    """Return (back_xml, forward_xml_or_none) for a report stem or file."""
    if xml_path.name.endswith(".forward.xml"):
        stem = xml_path.name[: -len(".forward.xml")]
        back = xml_path.parent / f"{stem}.xml"
        return (back if back.is_file() else xml_path, xml_path)

    back = xml_path
    forward = xml_path.parent / f"{xml_path.stem}.forward.xml"
    return back, forward if forward.is_file() else None


def _normalize_inline_forward_rows(rows: list[ParsedRow]) -> None:
    """Inline Forward/Back export: row Sharpe/Recovery are forward-period; Custom may be forward too."""
    for row in rows:
        if row.forward_sharpe is None:
            row.forward_sharpe = row.sharpe
        if row.forward_recovery is None:
            row.forward_recovery = row.recovery
        if row.back_result is not None:
            row.custom = row.back_result


def merge_forward_reports(
    back_path: Path,
    forward_path: Path | None,
    overrides: ColumnOverrides | None = None,
) -> tuple[list[ParsedRow], ForwardReportInfo, ColumnMapping, str, list[str]]:
    back_wb = parse_optimization_workbook(back_path)
    back_ws = primary_worksheet(back_wb)
    mapping = resolve_column_mapping(back_ws.headers, overrides)

    has_inline = (
        mapping.back_result is not None
        and mapping.forward_result is not None
        and any(
            r.back_result is not None or r.forward_result is not None
            for r in _parse_rows_from_records(back_ws.records, back_ws.headers, mapping)
        )
    )

    if has_inline:
        rows = _parse_rows_from_records(back_ws.records, back_ws.headers, mapping)
        _normalize_inline_forward_rows(rows)
        with_fwd = sum(1 for r in rows if r.forward_result is not None)
        info = ForwardReportInfo(
            back_path=back_path,
            forward_path=None,
            back_rows=len(rows),
            forward_rows=0,
            forward_joined=with_fwd,
            candidates_with_forward=with_fwd,
            has_inline_forward_columns=True,
            forward_file_status="inline_columns",
        )
        return rows, info, mapping, back_wb.title, back_ws.headers

    back_rows = _parse_rows_from_records(back_ws.records, back_ws.headers, mapping)
    for row in back_rows:
        if row.back_result is None:
            row.back_result = row.custom

    forward_rows_count = 0
    forward_joined = 0
    fwd_status = "missing"

    if forward_path and forward_path.is_file():
        fwd_status = forward_path.name
        fwd_wb = parse_optimization_workbook(forward_path)
        fwd_ws = primary_worksheet(fwd_wb)
        fwd_mapping = resolve_column_mapping(fwd_ws.headers, overrides)
        fwd_parsed = _parse_rows_from_records(fwd_ws.records, fwd_ws.headers, fwd_mapping)
        forward_rows_count = len(fwd_parsed)
        fwd_by_pass = {r.pass_id: r for r in fwd_parsed}
        for row in back_rows:
            fwd = fwd_by_pass.get(row.pass_id)
            if fwd is None:
                continue
            if fwd_mapping.forward_result is not None:
                row.forward_result = fwd.forward_result
            else:
                row.forward_result = fwd.custom
            row.forward_sharpe = fwd.sharpe
            row.forward_recovery = fwd.recovery
            if fwd.back_result is not None:
                row.back_result = fwd.back_result
            forward_joined += 1
    else:
        if mapping.forward_result:
            fwd_status = "column_present_no_data"
        else:
            fwd_status = "missing"

    with_fwd = sum(1 for r in back_rows if r.forward_result is not None)
    info = ForwardReportInfo(
        back_path=back_path,
        forward_path=forward_path,
        back_rows=len(back_rows),
        forward_rows=forward_rows_count,
        forward_joined=forward_joined,
        candidates_with_forward=with_fwd,
        has_inline_forward_columns=False,
        forward_file_status=fwd_status,
    )
    return back_rows, info, mapping, back_wb.title, back_ws.headers


def forward_selection_rank_key(
    custom: float,
    forward_result: float | None,
    pass_id: int,
) -> tuple[float, float, int]:
    fwd = forward_result or 0.0
    return (-(custom + fwd), -custom, -pass_id)


def selection_score(row: ParsedRow) -> float:
    fwd = row.forward_result or 0.0
    return row.custom + fwd


def selection_rank_key(row: ParsedRow) -> tuple[float, float, int]:
    return forward_selection_rank_key(row.custom, row.forward_result, row.pass_id)


def select_forward_candidates(
    rows: list[ParsedRow],
    thresholds: SelectionThresholds | None = None,
    *,
    top_n: int | None = None,
) -> tuple[list[ParsedRow], SelectionStats]:
    """Forward selection: Custom/Result, forward Result, and Sharpe gates only."""
    thresholds = thresholds or SelectionThresholds()
    stats = SelectionStats(total_rows=len(rows))
    pool: list[ParsedRow] = []

    rows_sorted = sorted(rows, key=lambda row: (-row.custom, -row.pass_id))
    for row in rows_sorted:
        if row.custom < thresholds.min_back_custom:
            stats.scan_stopped_at_custom_floor = True
            break
        if row.sharpe < thresholds.min_sharpe:
            stats.rejected_back_sharpe += 1
            continue

        if row.forward_result is None:
            stats.rejected_missing_forward_result += 1
            continue
        if row.forward_result < thresholds.min_forward_result:
            stats.rejected_forward_result += 1
            continue

        if row.forward_sharpe is None:
            stats.rejected_missing_forward_sharpe += 1
            continue
        if row.forward_sharpe < thresholds.min_sharpe:
            stats.rejected_forward_sharpe += 1
            continue

        pool.append(row)

    pool.sort(key=selection_rank_key)
    stats.accepted = len(pool)
    for row in pool[:3]:
        stats.top_candidates.append({
            "pass_id": row.pass_id,
            "custom": row.custom,
            "forward_result": row.forward_result,
            "combined_score": selection_score(row),
            "back_sharpe": row.sharpe,
            "forward_sharpe": row.forward_sharpe,
        })

    if top_n is not None and top_n > 0:
        return pool[:top_n], stats
    return pool, stats


def print_selection_stats(
    stats: SelectionStats,
    thresholds: SelectionThresholds | None = None,
) -> None:
    thresholds = thresholds or SelectionThresholds()
    floor_note = (
        ", scan stopped at Custom floor"
        if stats.scan_stopped_at_custom_floor
        else ""
    )
    print(
        f"  Forward selection: {stats.accepted}/{stats.total_rows} passed{floor_note} | "
        f"rejected: back_sharpe<{thresholds.min_sharpe}="
        f"{stats.rejected_back_sharpe}, "
        f"forward_sharpe<{thresholds.min_sharpe}="
        f"{stats.rejected_forward_sharpe}, "
        f"missing_forward_sharpe={stats.rejected_missing_forward_sharpe}, "
        f"forward_result<{thresholds.min_forward_result}="
        f"{stats.rejected_forward_result}, "
        f"missing_forward_result={stats.rejected_missing_forward_result}"
    )
    if stats.top_candidates:
        print("  Top ranked candidates (Custom + forward Result):")
        for item in stats.top_candidates:
            print(
                f"    pass={item['pass_id']} combined={item['combined_score']:.4f} "
                f"custom={item['custom']:.4f} forward={item['forward_result']} "
                f"back_sharpe={item['back_sharpe']:.4f} "
                f"fwd_sharpe={item['forward_sharpe']:.4f}"
            )


def metric_distribution(rows: list[ParsedRow]) -> dict[str, tuple[float, float, float] | None]:
    def dist(values: list[float]) -> tuple[float, float, float] | None:
        if not values:
            return None
        return min(values), statistics.median(values), max(values)

    sharpe = [r.sharpe for r in rows]
    recovery = [r.recovery for r in rows]
    custom = [r.custom for r in rows]
    forward = [r.forward_result for r in rows if r.forward_result is not None]
    forward_sharpe = [r.forward_sharpe for r in rows if r.forward_sharpe is not None]
    forward_recovery = [r.forward_recovery for r in rows if r.forward_recovery is not None]
    return {
        "sharpe": dist(sharpe),
        "recovery": dist(recovery),
        "custom": dist(custom),
        "forward_result": dist(forward),
        "forward_sharpe": dist(forward_sharpe),
        "forward_recovery": dist(forward_recovery),
    }


def print_metric_distribution(rows: list[ParsedRow], label: str = "") -> None:
    dist = metric_distribution(rows)
    prefix = f"  Distribution {label}: " if label else "  Distribution: "
    parts: list[str] = []
    for key, vals in dist.items():
        if vals is None:
            parts.append(f"{key}=n/a")
        else:
            parts.append(f"{key} min/med/max={vals[0]:.4f}/{vals[1]:.4f}/{vals[2]:.4f}")
    print(prefix + " | ".join(parts))


def extract_dates_from_title(title: str) -> tuple[str, str]:
    m = re.search(r"(\d{4}\.\d{2}\.\d{2})-(\d{4}\.\d{2}\.\d{2})", title)
    if m:
        return m.group(1), m.group(2)
    return "", ""


def extract_symbol_timeframe(title: str) -> tuple[str, str]:
    m = re.search(r"\s([A-Z]{6,}),\s*([A-Z0-9]+)\s", title)
    if m:
        return m.group(1), m.group(2)
    m = re.search(r"([A-Z]{6,}),\s*([A-Z0-9]+)", title)
    if m:
        return m.group(1), m.group(2)
    return "UNKNOWN", "UNKNOWN"


def resolve_backtest_dates(
    title: str,
    *,
    fallback_from: str = "",
    fallback_to: str = "",
) -> tuple[str, str]:
    from_date, to_date = extract_dates_from_title(title)
    if not from_date:
        from_date = fallback_from
    if not to_date:
        to_date = fallback_to
    if not from_date or not to_date:
        raise ValueError(
            f"Could not determine backtest FromDate/ToDate from title {title!r}. "
            "Pass --from-date and --to-date."
        )
    return from_date, to_date
