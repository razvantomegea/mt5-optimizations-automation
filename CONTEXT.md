# EA Optimization Glossary

Canonical vocabulary for the MT5 batch-optimization + step-usage analysis domain. Glossary only — no implementation details.

## Language

**Chart Timeframe**:
The MT5 period an optimization job runs on; folder name under `SetFiles/<Strategy>/` (e.g. M5, M15, H1).
_Avoid_: higher timeframe, period alone when chart vs trend TF matters

**Higher Timeframe**:
The trend/HTF period baked into a **Base set** variant name (`TrendM15`, `HTFH4`, …), which may differ from the **Chart Timeframe**.
_Avoid_: chart timeframe; calling every `.set` a higher TF when `TrendCurrent` means chart period

**M5 Chart Support**:
**Chart Timeframe** `M5` is first-class for Classic, Multi, and SwingHA (same strategy coverage as M15/H1/H4).
_Avoid_: SwingHA-only like D1/W1; treating M5 as Higher Timeframe-only

**M5 Base-set Matrix**:
Canonical **Base set** variants for **Chart Timeframe** M5: Classic = `TrendCurrent`, `TrendM5`, `TrendM15`, `TrendH1`, `TrendH4`, `TrendD1`; Multi = `TrendCurrent`, `HTFM15`, `HTFH1`, `HTFH4`, `HTFD1`; SwingHA = `TrendCurrent`.
_Avoid_: exact M15 copy without M15-as-HTF; TrendCurrent-only as the lasting matrix

**M5 Default Selection**:
**Chart Timeframe** M5 is pre-selected with M15/H1/H4 in dashboard defaults and CLI `--timeframes` default (not opt-in like D1/W1). Allowlist/default order: `M5, M15, H1, H4` (+ `D1, W1` allow-only).
_Avoid_: allowed-only M5; dropping H4 from defaults to make room; appending M5 after H4 in defaults

**M5 Grid Provenance**:
New M5 **Base sets** clone the matching M15 sibling grids; only **Higher Timeframe** enum fields and comments change (`TrendM5`→5, `TrendM15`/`HTFM15`→15). Brand-new variant files clone the nearest sibling then set the enum.
_Avoid_: inventing new Y/N grids for v1; stub/empty M5 folders

**M5 Higher-Timeframe Labels**:
Detail UI derives **Higher Timeframe** from report stem: `TrendM5`→`M5`, `HTFM15`→`M15` (plus existing mappings). Longer suffixes win over shorter (`TrendM15` before `TrendM5`).
_Avoid_: blank Higher Timeframe for new M5 variants; labeling `TrendM5` as M15

**M5 Delivery Scope**:
Ship **M5 Chart Support** end-to-end for optimize → favorite → portfolio: SetFiles matrix, dashboard/CLI defaults + allowlist, Higher Timeframe labels, docs/validation copy. Favorite/portfolio paths treat timeframe as opaque identity — no separate M5 branches. Out: Market bake, EA strategy rewrites. Tests that pin TF lists/labels may be updated. Shared default date range stays unchanged (~12y) for M5. Canonical committed grids: `EAs/SetFiles/**` only (package `SetFiles/` stays local/gitignored).
_Avoid_: Market Favorite Product work; inventing portfolio TF filters; EA OnInit changes for PERIOD_M5; shipping without test updates for new label/suffix contracts; M5-only shorter default fromDate; committing package-local SetFiles duplicates

## Terms

### Survivor

A parameter set that passed full validation and was kept in the final top-K ranking (`keep=true` in `best_survivors.csv`). Not merely a `validation_pass=true` row, and not any optimization pass. The step-usage report counts only Survivors.

### Base set

A `.set` file under your `MT5_SET_DIR` (or `--validate-set-dir`) defines the optimization **grid**. Each optimizable line has the form `VALUE||START||STEP||STOP||ENABLED`.

Nested layout example:

```
SetFiles/
  Classic/M5/TrendCurrent.set
  Classic/M15/TrendCurrent.set
  Multi/H1/HTFH4.set
  SwingHA/M15/TrendCurrent.set
  SwingHA/D1/TrendCurrent.set
  SwingHA/W1/TrendCurrent.set
```

### Permutated parameter

A base-set input whose line ends in `Y` (optimization enabled). Only these are permutated during optimization and are the subject of the step-usage report. Lines ending in `N` are fixed and ignored.

### Step

One discrete value in a permutated parameter's grid: `START, START+STEP, …, STOP`. Example: `RSI_LOOKBACK=50||50||25||100||Y` has steps `{50, 75, 100}`.

### Winning set

The `.set` file written for a Survivor (in `Best/sets/`) containing the single chosen value per parameter (no `||` grid fields). Source of the value a Survivor actually used for each Permutated parameter.

### Step usage

For a given Permutated parameter, the count of Survivors whose chosen value equals each Step (exact match — Survivor values are always grid values). Least-used Steps are candidates for removal to shrink the grid (less overfitting, faster optimization).

### Survivor matrix

A table with one row per Survivor and one column per Permutated parameter; each cell holds the value that Survivor chose. Carries identifying columns (base set, profile, symbol, timeframe, pass id) plus validation metrics.

### Value-frequency summary

Per Permutated parameter, the count (and distinct-symbol count and percentage) of Survivors that chose each Step, including zero-count Steps, ordered least-used first.

## Relationships

- A **Base set** lives under one strategy folder and one **Chart Timeframe** folder.
- A **Base set** variant may encode a **Higher Timeframe** (`Trend*`, `HTF*`) or use chart period (`TrendCurrent`).
- **M5 Chart Support** applies to Classic, Multi, and SwingHA — not SwingHA-only like D1/W1.
- **M5 Base-set Matrix** defines which Higher Timeframe variants exist under each strategy’s M5 folder.
- **M5 Default Selection** puts M5 in the same default job matrix as M15/H1/H4.
- **M5 Grid Provenance** means M5 grids start as copies of M15 grids with TF enums adjusted.
- **M5 Higher-Timeframe Labels** keep detail-page Higher Timeframe display in sync with new stem suffixes.
- **M5 Delivery Scope** covers optimize → favorite → portfolio via existing opaque timeframe identity once M5 jobs can run; tests for TF contracts may change; date default stays shared with other TFs; grids commit only under `EAs/SetFiles/`; docs/validation copy update in same change.

## Example dialogue

> **Dev:** "Is M5 like D1/W1 — SwingHA-only and opt-in?"
> **Domain expert:** "No. **M5 Chart Support** is Classic/Multi/SwingHA and in **M5 Default Selection** with M15/H1/H4. D1/W1 stay SwingHA opt-in."

> **Dev:** "Do favorites or portfolio need an M5 code path?"
> **Domain expert:** "No. Under **M5 Delivery Scope**, timeframe is opaque identity. Once M5 Survivors exist, favorite + portfolio already work."

> **Dev:** "Where do we commit the new grids?"
> **Domain expert:** "`EAs/SetFiles/` only, per **M5 Base-set Matrix** and **M5 Grid Provenance**. Package SetFiles stay local."

## Flagged ambiguities

- M5 means **Chart Timeframe** M5 (job period). Same-TF Classic variant is `TrendM5`; Multi does not use same-TF `HTFM5` — M15-as-HTF is `TrendM15` / `HTFM15`.
- Docs in same change: root README + package README + validation string + constants comment (not code-only).
- Grill closed 2026-08-02: shared understanding complete; ready for implementation plan / execute on request.
