"""Generate one tester EA from the canonical TrendReversal source and a manifest.

The generated file is a build artifact. It switches all strategy-local globals
between instances before invoking the unchanged TrendReversal bar pipeline.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path

from mt5_portfolio_manifest import PortfolioManifest
from mt5_workspace import PACKAGE_ROOT

EA_ROOT = PACKAGE_ROOT.parent.parent / "EAs"
_INPUT_RE = re.compile(
    r"^input\s+(int|double|bool|ENUM_TIMEFRAMES|ENUM_DAY_OF_WEEK)\s+"
    r"([A-Z][A-Z_0-9]*)\s*=\s*([^;]+);$",
    re.MULTILINE,
)
_LOCAL_INCLUDE_RE = re.compile(r"^#include <(TrendReversal\w+|OnTesterEquityMetrics)\.mqh>$", re.MULTILINE)

# All mutable globals in the canonical EA except CTrade, which is rebound by
# magic on each context switch. A missing entry here is a portfolio isolation bug.
_SCALARS = {
    "int": [
        "gGroupIdCounter", "rsiHandle", "maHandle", "adxHandle", "atrHandle",
        "currentDigits",
    ],
    "double": [
        "currentHigh", "currentLow", "currentClose", "currentOpen",
        "minVolume", "maxVolume", "volumeStep", "point", "cachedMinTP",
        "lastLongSignalTp", "lastShortSignalTp", "gPeakEquity", "gPeakBalance",
    ],
    "datetime": ["lastBarTime", "lastLongSignalBar", "lastShortSignalBar"],
    "bool": ["useTimeframe", "gTradingHalted"],
    "ISwing": ["swing"],
    "ENUM_TIMEFRAMES": ["TIMEFRAME"],
}
_ARRAYS = {
    "TradeGroup": ["longGroups", "shortGroups"],
    "double": ["rsi", "ma", "adx", "plus_di", "minus_di", "atr"],
}
_SYMBOL_RE = re.compile(r"^[A-Za-z0-9._#-]{1,64}$")
_TIMEFRAMES = {"M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"}


def canonical_ea_revision() -> str:
    """Stable hash of TrendReversal.mq5 plus its inlined local includes.

    Combined portfolio builds and verified summaries include this revision so a
    behavior change such as MAX_GROUP_POSITIONS cannot keep an older verified
    snapshot displayable.
    """
    source = (EA_ROOT / "TrendReversal.mq5").read_text(encoding="utf-8-sig")
    digest = hashlib.sha256()
    digest.update(source.encode("utf-8"))
    for name in _LOCAL_INCLUDE_RE.findall(source):
        include = (EA_ROOT / "include" / f"{name}.mqh").read_text(encoding="utf-8-sig")
        digest.update(include.encode("utf-8"))
    return digest.hexdigest()


def _read_canonical_ea() -> str:
    source = (EA_ROOT / "TrendReversal.mq5").read_text(encoding="utf-8-sig")

    def inline_include(match: re.Match[str]) -> str:
        name = match.group(1)
        return (EA_ROOT / "include" / f"{name}.mqh").read_text(encoding="utf-8-sig")

    source = _LOCAL_INCLUDE_RE.sub(inline_include, source)
    source = re.sub(r'^input group .*$', '', source, flags=re.MULTILINE)
    source = _INPUT_RE.sub(lambda m: f"{m.group(1)} {m.group(2)} = {m.group(3)};", source)
    # Portfolio magic values are unique 64-bit numbers, beyond MQL5 int range.
    source = source.replace("int EXPERT_MAGIC =", "long EXPERT_MAGIC =", 1)
    for event in ("OnInit", "OnDeinit", "OnTick", "OnTester", "OnTradeTransaction"):
        source = re.sub(rf"\b{event}\s*\(", f"Strategy{event}(", source)
    source = re.sub(r"\b_Symbol\b", "gStrategySymbol", source)
    source = re.sub(r"\bPeriod\(\)", "(int)gStrategyPeriod", source)
    source = source.replace(
        "#include <Trade/Trade.mqh>",
        "#include <Trade/Trade.mqh>\nstring gStrategySymbol;\nENUM_TIMEFRAMES gStrategyPeriod;",
        1,
    )
    return source


def _input_definitions(source: str) -> dict[str, tuple[str, str]]:
    result = {name: (kind, default.strip()) for kind, name, default in _INPUT_RE.findall(
        (EA_ROOT / "TrendReversal.mq5").read_text(encoding="utf-8-sig")
    )}
    if not result or "EXPERT_MAGIC" not in result:
        raise ValueError("Canonical TrendReversal inputs could not be parsed")
    return result


def _mql_literal(kind: str, value: str) -> str:
    clean = value.split("||", 1)[0].strip()
    if kind == "bool":
        if clean.lower() not in {"true", "false"}:
            raise ValueError(f"Invalid bool input {value!r}")
        return clean.lower()
    if kind == "double":
        number = float(clean)
        if not (-1e100 < number < 1e100):
            raise ValueError(f"Invalid double input {value!r}")
        return repr(number)
    if kind in {"int", "ENUM_TIMEFRAMES", "ENUM_DAY_OF_WEEK"}:
        if re.fullmatch(r"-?\d+", clean):
            number = int(clean)
            return f"({kind}){number}" if kind.startswith("ENUM_") else str(number)
        if kind == "ENUM_TIMEFRAMES" and re.fullmatch(r"PERIOD_[A-Z0-9]+", clean):
            return clean
        if kind == "ENUM_DAY_OF_WEEK" and clean in {
            "SUNDAY", "MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY"
        }:
            return clean
    raise ValueError(f"Invalid {kind} input {value!r}")


def _read_set_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    raw = path.read_bytes()
    text = raw.decode("utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig")
    for line in text.splitlines():
        if "=" not in line or line.lstrip().startswith((";", "#")):
            continue
        name, value = line.split("=", 1)
        values[name.strip()] = value.strip()
    return values


def generate_portfolio_ea(
    manifest: PortfolioManifest,
    *,
    sets_dir: Path,
    export_file: str = "portfolio_combined_equity.json",
) -> str:
    """Return compilable MQL5 source with no fixed strategy count."""
    if not manifest.strategies:
        raise ValueError("Portfolio must contain at least one strategy")
    if manifest.tester_model != 4:
        raise ValueError("Portfolio requires real-tick tester model 4")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+\.json", export_file):
        raise ValueError("Portfolio equity export filename is invalid")
    source = _read_canonical_ea()
    inputs = _input_definitions(source)
    config_lines = ["void ApplyPortfolioConfig(const int index)", "  {", "   switch(index)", "     {"]
    for index, strategy in enumerate(manifest.strategies):
        if not _SYMBOL_RE.fullmatch(strategy.symbol):
            raise ValueError(f"Invalid symbol {strategy.symbol!r}")
        if strategy.timeframe not in _TIMEFRAMES:
            raise ValueError(f"Unsupported timeframe {strategy.timeframe!r}")
        start = strategy.source_from_date or manifest.from_date
        end = strategy.source_to_date or manifest.to_date
        try:
            start_date = datetime.strptime(start, "%Y.%m.%d")
            end_date = datetime.strptime(end, "%Y.%m.%d")
        except ValueError as exc:
            raise ValueError(f"Invalid source window for {strategy.result_id}") from exc
        if not (manifest.from_date <= start < end <= manifest.to_date):
            raise ValueError(f"Source window is outside portfolio span for {strategy.result_id}")
        set_path = sets_dir / strategy.set_file
        values = _read_set_values(set_path)
        unknown = set(values) - set(inputs)
        if unknown:
            raise ValueError(f"{set_path.name}: unknown inputs {sorted(unknown)}")
        config_lines += [f"      case {index}:", "        {"]
        for name, (kind, default) in inputs.items():
            raw = values.get(name, default)
            if name == "EXPERT_MAGIC":
                raw = str(strategy.magic)
            config_lines.append(f"         {name} = {_mql_literal(kind, raw)};")
        config_lines += [
            f'         gStrategySymbol = "{strategy.symbol}";',
            f"         gStrategyPeriod = PERIOD_{strategy.timeframe};",
            f'         gPortfolioStartTimes[index] = StringToTime("{start_date:%Y.%m.%d} 00:00:00");',
            f'         gPortfolioEndTimes[index] = StringToTime("{end_date:%Y.%m.%d} 00:00:00");',
            "         TIMEFRAME = gStrategyPeriod;",
            "         break;",
            "        }",
        ]
    config_lines += ["     }", "  }"]

    input_fields = [("long" if name == "EXPERT_MAGIC" else kind, name) for name, (kind, _) in inputs.items()]
    fields = input_fields + [(kind, name) for kind, names in _SCALARS.items() for name in names]
    struct_lines = ["struct PortfolioStrategyContext", "  {", "   string strategySymbol;", "   ENUM_TIMEFRAMES strategyPeriod;"]
    struct_lines += [f"   {kind} {name};" for kind, name in fields]
    struct_lines += [f"   {kind} {name}[];" for kind, names in _ARRAYS.items() for name in names]
    struct_lines += ["  };", "PortfolioStrategyContext gPortfolioContexts[];"]

    save_lines = ["void SavePortfolioContext(const int index)", "  {",
                  "   gPortfolioContexts[index].strategySymbol = gStrategySymbol;",
                  "   gPortfolioContexts[index].strategyPeriod = gStrategyPeriod;"]
    load_lines = ["void LoadPortfolioContext(const int index)", "  {",
                  "   gStrategySymbol = gPortfolioContexts[index].strategySymbol;",
                  "   gStrategyPeriod = gPortfolioContexts[index].strategyPeriod;"]
    for _, name in fields:
        save_lines.append(f"   gPortfolioContexts[index].{name} = {name};")
        load_lines.append(f"   {name} = gPortfolioContexts[index].{name};")
    for names in _ARRAYS.values():
        for name in names:
            save_lines.append(
                f"   ArrayResize(gPortfolioContexts[index].{name}, ArraySize({name}));"
            )
            save_lines.append(
                f"   if(ArraySize({name}) > 0) "
                f"ArrayCopy(gPortfolioContexts[index].{name}, {name});"
            )
            load_lines.append(
                f"   ArrayResize({name}, ArraySize(gPortfolioContexts[index].{name}));"
            )
            load_lines.append(
                f"   if(ArraySize(gPortfolioContexts[index].{name}) > 0) "
                f"ArrayCopy({name}, gPortfolioContexts[index].{name});"
            )
    save_lines.append("  }")
    load_lines += [
        "   ArraySetAsSeries(rsi, true); ArraySetAsSeries(ma, true);",
        "   ArraySetAsSeries(adx, true); ArraySetAsSeries(plus_di, true);",
        "   ArraySetAsSeries(minus_di, true); ArraySetAsSeries(atr, true);",
        "   trade.SetExpertMagicNumber((ulong)EXPERT_MAGIC);", "  }",
    ]

    equity_driver = [
        "struct PortfolioEquityPoint { datetime time; double balance; double equity; };",
        "struct PortfolioEquityDay",
        "  {",
        "   int day;",
        "   PortfolioEquityPoint first; PortfolioEquityPoint last;",
        "   PortfolioEquityPoint minEquity; PortfolioEquityPoint maxEquity;",
        "   PortfolioEquityPoint minBalance; PortfolioEquityPoint maxBalance;",
        "  };",
        "PortfolioEquityDay gPortfolioDays[];",
        "int gPortfolioTradeCounts[];",
        "datetime gPortfolioStartTimes[];",
        "datetime gPortfolioEndTimes[];",
        "bool gPortfolioStarted[];",
        "bool gPortfolioFinished[];",
        "bool gPortfolioMissedBars = false;",
        "bool gPortfolioWindowCloseFailed = false;",
        "bool PortfolioHasOwnedPositions()",
        "  {",
        "   for(int p = PositionsTotal()-1; p >= 0; p--)",
        "     {",
        "      ulong ticket = PositionGetTicket(p);",
        "      if(ticket == 0 || !PositionSelectByTicket(ticket)) continue;",
        "      if(PositionGetString(POSITION_SYMBOL) == gStrategySymbol &&",
        "         (ulong)PositionGetInteger(POSITION_MAGIC) == (ulong)EXPERT_MAGIC) return true;",
        "     }",
        "   return false;",
        "  }",
        "void CapturePortfolioEquity()",
        "  {",
        "   PortfolioEquityPoint p;",
        "   p.time = TimeCurrent();",
        "   p.balance = AccountInfoDouble(ACCOUNT_BALANCE);",
        "   p.equity = AccountInfoDouble(ACCOUNT_EQUITY);",
        "   int day = (int)((long)p.time / 86400);",
        "   int count = ArraySize(gPortfolioDays);",
        "   if(count == 0 || gPortfolioDays[count-1].day != day)",
        "     {",
        "      ArrayResize(gPortfolioDays, count+1);",
        "      gPortfolioDays[count].day = day;",
        "      gPortfolioDays[count].first = p; gPortfolioDays[count].last = p;",
        "      gPortfolioDays[count].minEquity = p; gPortfolioDays[count].maxEquity = p;",
        "      gPortfolioDays[count].minBalance = p; gPortfolioDays[count].maxBalance = p;",
        "      return;",
        "     }",
        "   gPortfolioDays[count-1].last = p;",
        "   if(p.equity < gPortfolioDays[count-1].minEquity.equity) gPortfolioDays[count-1].minEquity = p;",
        "   if(p.equity > gPortfolioDays[count-1].maxEquity.equity) gPortfolioDays[count-1].maxEquity = p;",
        "   if(p.balance < gPortfolioDays[count-1].minBalance.balance) gPortfolioDays[count-1].minBalance = p;",
        "   if(p.balance > gPortfolioDays[count-1].maxBalance.balance) gPortfolioDays[count-1].maxBalance = p;",
        "  }",
        "bool ExportPortfolioEquity()",
        "  {",
        f'   int file = FileOpen("{export_file}", FILE_WRITE|FILE_TXT|FILE_COMMON|FILE_ANSI);',
        "   if(file == INVALID_HANDLE) return false;",
        '   FileWriteString(file, "{\\\"source\\\":\\\"mt5_combined_tester\\\",\\\"equity_curve\\\":[");',
        "   bool first = true;",
        "   for(int d = 0; d < ArraySize(gPortfolioDays); d++)",
        "     {",
        "      PortfolioEquityPoint points[6];",
        "      points[0] = gPortfolioDays[d].first; points[1] = gPortfolioDays[d].minEquity;",
        "      points[2] = gPortfolioDays[d].maxEquity; points[3] = gPortfolioDays[d].minBalance;",
        "      points[4] = gPortfolioDays[d].maxBalance; points[5] = gPortfolioDays[d].last;",
        "      for(int i = 0; i < 6; i++)",
        "         for(int j = i+1; j < 6; j++)",
        "            if(points[j].time < points[i].time)",
        "              { PortfolioEquityPoint tmp = points[i]; points[i] = points[j]; points[j] = tmp; }",
        "      for(int i = 0; i < 6; i++)",
        "        {",
        "         if(i > 0 && points[i].time == points[i-1].time &&",
        "            points[i].balance == points[i-1].balance && points[i].equity == points[i-1].equity) continue;",
        '         if(!first) FileWriteString(file, ",");',
        "         first = false;",
        '         string stamp = TimeToString(points[i].time, TIME_DATE|TIME_SECONDS);',
        '         FileWriteString(file, StringFormat("{\\\"time\\\":\\\"%s\\\",\\\"balance\\\":%.8f,\\\"equity\\\":%.8f}",',
        "                                        stamp, points[i].balance, points[i].equity));",
        "        }",
        "     }",
        '   FileWriteString(file, StringFormat("],\\\"initial_deposit\\\":%.8f,\\\"final_balance\\\":%.8f,\\\"final_equity\\\":%.8f,",',
        "                                  TesterStatistics(STAT_INITIAL_DEPOSIT),",
        "                                  AccountInfoDouble(ACCOUNT_BALANCE), AccountInfoDouble(ACCOUNT_EQUITY)));",
        '   FileWriteString(file, "\\\"strategy_trade_counts\\\":[");',
        "   for(int i = 0; i < PORTFOLIO_STRATEGY_COUNT; i++)",
        '     { if(i > 0) FileWriteString(file, ","); FileWriteString(file, IntegerToString(gPortfolioTradeCounts[i])); }',
        '   FileWriteString(file, gPortfolioMissedBars ? "],\\\"missed_bars\\\":true," : "],\\\"missed_bars\\\":false,");',
        '   FileWriteString(file, gPortfolioWindowCloseFailed ? "\\\"window_close_failed\\\":true," : "\\\"window_close_failed\\\":false,");',
        '   FileWriteString(file, StringFormat("\\\"net_profit\\\":%.8f,\\\"trades\\\":%.0f,\\\"balance_dd_pct\\\":%.8f,\\\"equity_dd_pct\\\":%.8f}",',
        "                                  TesterStatistics(STAT_PROFIT), TesterStatistics(STAT_TRADES),",
        "                                  TesterStatistics(STAT_BALANCE_DDREL_PERCENT),",
        "                                  TesterStatistics(STAT_EQUITY_DDREL_PERCENT)));",
        "   FileClose(file);",
        "   return true;",
        "  }",
    ]

    driver = [
        f"const int PORTFOLIO_STRATEGY_COUNT = {len(manifest.strategies)};",
        *struct_lines, *config_lines, *save_lines, *load_lines, *equity_driver,
        "int OnInit()",
        "  {",
        "   if(!MQLInfoInteger(MQL_TESTER)) return INIT_FAILED;",
        "   ArrayResize(gPortfolioContexts, PORTFOLIO_STRATEGY_COUNT);",
        "   ArrayResize(gPortfolioTradeCounts, PORTFOLIO_STRATEGY_COUNT);",
        "   ArrayResize(gPortfolioStartTimes, PORTFOLIO_STRATEGY_COUNT);",
        "   ArrayResize(gPortfolioEndTimes, PORTFOLIO_STRATEGY_COUNT);",
        "   ArrayResize(gPortfolioStarted, PORTFOLIO_STRATEGY_COUNT);",
        "   ArrayResize(gPortfolioFinished, PORTFOLIO_STRATEGY_COUNT);",
        "   for(int i = 0; i < PORTFOLIO_STRATEGY_COUNT; i++)",
        "     {",
        "      ArrayResize(longGroups, 0); ArrayResize(shortGroups, 0);",
        "      ArrayResize(rsi, 0); ArrayResize(ma, 0); ArrayResize(adx, 0);",
        "      ArrayResize(plus_di, 0); ArrayResize(minus_di, 0); ArrayResize(atr, 0);",
        "      rsiHandle = INVALID_HANDLE; maHandle = INVALID_HANDLE;",
        "      adxHandle = INVALID_HANDLE; atrHandle = INVALID_HANDLE;",
        "      gGroupIdCounter = 0; lastBarTime = 0;",
        "      lastLongSignalBar = 0; lastShortSignalBar = 0;",
        "      lastLongSignalTp = 0; lastShortSignalTp = 0;",
        "      swing.calculatedAt = 0; cachedMinTP = 0; useTimeframe = false;",
        "      ApplyPortfolioConfig(i);",
        "      if(!SymbolSelect(gStrategySymbol, true)) return INIT_FAILED;",
        "      if(StrategyOnInit() != INIT_SUCCEEDED) return INIT_FAILED;",
        "      SavePortfolioContext(i);",
        "     }",
        "   return INIT_SUCCEEDED;",
        "  }",
        "void RunPortfolioStrategies()",
        "  {",
        "   CapturePortfolioEquity();",
        "   for(int i = 0; i < PORTFOLIO_STRATEGY_COUNT; i++)",
        "     {",
        "      if(TimeCurrent() < gPortfolioStartTimes[i] || gPortfolioFinished[i]) continue;",
        "      if(TimeCurrent() < gPortfolioEndTimes[i] &&",
        "         (gPortfolioContexts[i].MAX_DRAWDOWN == 0 || gPortfolioContexts[i].MAX_DRAWDOWN == 100))",
        "        {",
        "         datetime bar = iTime(gPortfolioContexts[i].strategySymbol,",
        "                              gPortfolioContexts[i].TIMEFRAME, 0);",
        "         if(bar == 0 || bar == gPortfolioContexts[i].lastBarTime) continue;",
        "        }",
        "      LoadPortfolioContext(i);",
        "      if(TimeCurrent() >= gPortfolioEndTimes[i])",
        "        {",
        "         CloseAllEATrades();",
        "         if(PortfolioHasOwnedPositions()) gPortfolioWindowCloseFailed = true;",
        "         else gPortfolioFinished[i] = true;",
        "         SavePortfolioContext(i);",
        "         continue;",
        "        }",
        "      if(!gPortfolioStarted[i])",
        "        {",
        "         gPeakEquity = AccountInfoDouble(ACCOUNT_EQUITY);",
        "         gPeakBalance = AccountInfoDouble(ACCOUNT_BALANCE);",
        "         gPortfolioStarted[i] = true;",
        "        }",
        "      if(lastBarTime != 0)",
        "        {",
        "         // Exact-match miss means the prior bar vanished from history.",
        "         // priorShift > 1 is expected for non-chart symbols under a single",
        "         // chart OnTick driver and is not treated as a fatal gap.",
        "         int priorShift = iBarShift(gStrategySymbol, TIMEFRAME, lastBarTime, true);",
        "         if(priorShift < 0) gPortfolioMissedBars = true;",
        "        }",
        "      StrategyOnTick();",
        "      SavePortfolioContext(i);",
        "     }",
        "   CapturePortfolioEquity();",
        "  }",
        "void OnTick() { RunPortfolioStrategies(); }",
        "void OnTradeTransaction(const MqlTradeTransaction &trans,",
        "                        const MqlTradeRequest &request,",
        "                        const MqlTradeResult &result)",
        "  {",
        "   if(trans.type != TRADE_TRANSACTION_DEAL_ADD || !HistoryDealSelect(trans.deal)) return;",
        "   long entry = HistoryDealGetInteger(trans.deal, DEAL_ENTRY);",
        "   if(entry != DEAL_ENTRY_OUT && entry != DEAL_ENTRY_INOUT) return;",
        "   long magic = HistoryDealGetInteger(trans.deal, DEAL_MAGIC);",
        "   for(int i = 0; i < PORTFOLIO_STRATEGY_COUNT; i++)",
        "      if(gPortfolioContexts[i].EXPERT_MAGIC == magic)",
        "        { gPortfolioTradeCounts[i]++; break; }",
        "   CapturePortfolioEquity();",
        "  }",
        "void OnDeinit(const int reason)",
        "  {",
        "   for(int i = 0; i < PORTFOLIO_STRATEGY_COUNT; i++)",
        "     {",
        "      LoadPortfolioContext(i);",
        "      StrategyOnDeinit(reason);",
        "     }",
        "  }",
        "double OnTester() { CapturePortfolioEquity(); return ExportPortfolioEquity() ? 1.0 : 0.0; }",
    ]
    return source + "\n\n// Generated portfolio context and tester driver.\n" + "\n".join(driver) + "\n"
