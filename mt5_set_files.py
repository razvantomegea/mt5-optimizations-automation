"""Shared MT5 set-file discovery, parsing, and optimization-grid helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Literal

SetLayout = Literal["nested", "flat"]


@dataclass(frozen=True)
class ParamGrid:
    name: str
    steps: list[str]


def sanitize(name: str) -> str:
    bad = '<>:"/\\|?* '
    s = "".join("_" if c in bad else c for c in name)
    while "__" in s:
        s = s.replace("__", "_")
    return s.strip("._") or "job"


def flatten_set_tester_name(rel: Path) -> str:
    """Classic/M15/TrendH4.set -> Classic_M15_TrendH4.set; flat names stay as-is."""
    if len(rel.parts) == 1:
        return rel.name
    return "_".join(rel.with_suffix("").parts) + ".set"


def set_path_relative_to_dir(path: Path, set_dir: Path) -> Path:
    resolved = path.expanduser().resolve()
    base = set_dir.expanduser().resolve()
    if resolved.is_relative_to(base):
        return resolved.relative_to(base)
    return Path(path.name)


def discover_layout(set_dir: Path) -> SetLayout:
    """Detect nested ``<strategy>/<chart_tf>/*.set`` vs flat ``*.set`` layout."""
    if not set_dir.is_dir():
        return "flat"

    for child in set_dir.iterdir():
        if not child.is_dir():
            continue
        for sub in child.iterdir():
            if sub.is_dir() and any(sub.glob("*.set")):
                return "nested"

    return "flat"


def discover_strategies(set_dir: Path) -> tuple[str, ...]:
    """Top-level strategy folder names, or ``Default`` for a flat .set directory."""
    if not set_dir.is_dir():
        return ()

    layout = discover_layout(set_dir)
    if layout == "flat":
        return ("Default",) if any(set_dir.glob("*.set")) else ()

    strategies: list[str] = []
    for child in sorted(set_dir.iterdir()):
        if child.is_dir() and any(child.rglob("*.set")):
            strategies.append(child.name)
    return tuple(strategies)


def discover_chart_tfs(set_dir: Path) -> frozenset[str]:
    """Chart timeframe folder names (nested layout only)."""
    tfs: set[str] = set()
    if discover_layout(set_dir) != "nested":
        return frozenset()

    for strategy in discover_strategies(set_dir):
        root = set_dir / strategy
        if not root.is_dir():
            continue
        for child in root.iterdir():
            if child.is_dir() and any(child.glob("*.set")):
                tfs.add(child.name.upper())
    return frozenset(tfs)


def discover_set_files(set_dir: Path) -> dict[str, Path]:
    """Map flat MT5 tester filename -> path under ``set_dir``."""
    index: dict[str, Path] = {}
    if not set_dir.is_dir():
        return index

    layout = discover_layout(set_dir)
    if layout == "flat":
        for path in sorted(set_dir.glob("*.set")):
            index[path.name] = path
        return index

    for strategy in discover_strategies(set_dir):
        for chart_tf in sorted(discover_chart_tfs(set_dir)):
            sub = set_dir / strategy / chart_tf
            if not sub.is_dir():
                continue
            for path in sorted(sub.glob("*.set")):
                rel = path.relative_to(set_dir)
                index[flatten_set_tester_name(rel)] = path
    return index


def filter_paths_by_strategies(
    paths: Iterable[str | Path],
    strategies: Iterable[str],
    *,
    set_dir: Path,
) -> list[str]:
    """Keep .set paths whose strategy folder (or flat name prefix) is selected."""
    allowed = {s.strip() for s in strategies if s.strip()}
    known = set(discover_strategies(set_dir))
    unknown = allowed - known
    if unknown:
        raise ValueError(
            f"Unknown strategies: {sorted(unknown)}. "
            f"Discovered under {set_dir}: {sorted(known)}"
        )
    if not allowed:
        raise ValueError("At least one strategy is required")

    layout = discover_layout(set_dir)
    set_dir_resolved = set_dir.expanduser().resolve()
    filtered: list[str] = []

    for item in paths:
        path = Path(item).expanduser()
        resolved = path.resolve() if path.exists() else path
        if resolved.is_file() and resolved.is_relative_to(set_dir_resolved):
            rel = resolved.relative_to(set_dir_resolved)
            if layout == "nested" and rel.parts and rel.parts[0] in allowed:
                filtered.append(str(resolved))
            elif layout == "flat" and "Default" in allowed:
                filtered.append(str(resolved))
            continue

        flat_name = path.name
        if layout == "nested":
            for strategy in allowed:
                if flat_name.startswith(f"{strategy}_"):
                    filtered.append(str(path))
                    break
        elif layout == "flat" and "Default" in allowed:
            filtered.append(str(path))

    return filtered


def choose_base_set(*, report_stem: str, set_index: dict[str, Path]) -> Path:
    stem = report_stem.lower()
    for flat_name, path in sorted(
        set_index.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        flat_stem = flat_name.removesuffix(".set").lower()
        if stem.endswith(flat_stem):
            return path
    raise FileNotFoundError(
        f"No base .set file matched report stem={report_stem!r}. "
        f"Known flat names: {sorted(set_index)}"
    )


def _decode_set_file_bytes(raw: bytes) -> str:
    if not raw:
        return ""
    if raw.startswith(b"\xff\xfe"):
        text = raw.decode("utf-16-le", errors="ignore")
    elif raw.startswith(b"\xfe\xff"):
        text = raw.decode("utf-16-be", errors="ignore")
    elif len(raw) >= 2 and raw[1::2].count(0) > len(raw[1::2]) // 2:
        text = raw.decode("utf-16-le", errors="ignore")
    elif len(raw) >= 2 and raw[0::2].count(0) > len(raw[0::2]) // 2:
        text = raw.decode("utf-16-be", errors="ignore")
    else:
        text = raw.decode("utf-8", errors="ignore")
    return text.lstrip("\ufeff")


def read_set_file_text(path: Path) -> str:
    return _decode_set_file_bytes(path.read_bytes())


def set_file_effective_value(raw: str) -> str:
    """First value from MT5 tester .set line (before || optimization fields)."""
    return raw.split("||")[0].strip()


def parse_set_file(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    for line in read_set_file_text(path).splitlines():
        line = line.strip()
        if not line or line.startswith(";") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = set_file_effective_value(value.strip())
    return data


def canonical_set_value(raw: Any) -> str:
    s = str(raw).strip()
    low = s.lower()
    if low in {"true", "false"}:
        return low
    try:
        value = Decimal(s)
    except (InvalidOperation, ValueError):
        return s
    normalized = value.normalize()
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _decimal_places(raw: str) -> int:
    s = raw.strip()
    if "." not in s:
        return 0
    return len(s.split(".", 1)[1].rstrip("0"))


def enumerate_grid_steps(start: str, step: str, stop: str) -> list[str]:
    lower_bounds = {start.strip().lower(), stop.strip().lower()}
    if lower_bounds <= {"false", "true"}:
        return ["false", "true"]

    try:
        current = Decimal(start.strip())
        increment = Decimal(step.strip())
        limit = Decimal(stop.strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Unsupported grid bounds: {start!r}, {step!r}, {stop!r}") from exc

    if increment == 0:
        values = [current] if current == limit else [current, limit]
    else:
        direction = 1 if increment > 0 else -1
        if (limit - current) * direction < 0:
            raise ValueError(f"Unsupported grid bounds: {start!r}, {step!r}, {stop!r}")
        values: list[Decimal] = []
        while (direction > 0 and current <= limit) or (direction < 0 and current >= limit):
            values.append(current)
            current += increment

    places = max(_decimal_places(start), _decimal_places(step), _decimal_places(stop))
    quant = Decimal(1).scaleb(-places)
    return [canonical_set_value(value.quantize(quant) if places else value) for value in values]


def parse_set_grid(path: Path) -> dict[str, ParamGrid]:
    grids: dict[str, ParamGrid] = {}
    for line in read_set_file_text(path).splitlines():
        line = line.strip()
        if not line or line.startswith(";") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        parts = raw_value.split("||")
        if len(parts) < 5 or parts[-1].strip().upper() != "Y":
            continue
        name = key.strip()
        grids[name] = ParamGrid(
            name=name,
            steps=enumerate_grid_steps(parts[1], parts[2], parts[3]),
        )
    return grids


def _ensure_exists(path: Path, label: str) -> None:
    if not path.exists():
        raise FileNotFoundError(f"{label} not found: {path}")


def resolve_mt5_data_dir(*, terminal: Path, portable: bool, mt5_data: str | None) -> Path:
    install_dir = terminal.parent
    if portable:
        return install_dir
    if mt5_data:
        data = Path(mt5_data).expanduser().resolve()
        _ensure_exists(data, "MT5 data directory")
        return data

    appdata = Path(os.environ.get("APPDATA", "")) / "MetaQuotes" / "Terminal"
    if appdata.is_dir():
        install_dir_resolved = install_dir.resolve()
        for entry in appdata.iterdir():
            if not entry.is_dir():
                continue
            origin = entry / "origin.txt"
            if not origin.is_file():
                continue
            try:
                origin_path = origin.read_text(encoding="utf-16-le").strip().strip("\ufeff\x00")
            except UnicodeError:
                origin_path = origin.read_text(encoding="utf-8", errors="ignore").strip()
            if Path(origin_path).expanduser().resolve() == install_dir_resolved:
                return entry.resolve()

    raise FileNotFoundError(
        "Could not determine MT5 data directory. Pass --mt5-data "
        "(e.g. %APPDATA%\\MetaQuotes\\Terminal\\<id>) or use --portable."
    )
