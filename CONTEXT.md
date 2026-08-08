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

A parameter set that passed full validation and was kept in the final top-K ranking (`keep=true` in `best_survivors.csv`). Produced by the existing optimize/validate path with a **No-Skip Winning Set**. A later **Skip Robustness** step may update **Skip Robustness Status** on a subset of Survivors without changing how Survivors were first created.
_Avoid_: requiring skip robustness inside the main validate path

### Skip Robustness Optimization

A separate, complete optimization run on Every tick based on real ticks that freezes a **Survivor**'s winning parameters and permutates only `SKIP_TRADE_DAY` and `SKIP_MONTH` (60 **Skip Combinations**). Invoked by its own CLI/workflow step **after** `Best/` Survivors already exist. Stress-only — does not choose live skip values. Not Monte Carlo.
_Avoid_: Monte Carlo; baking skip into the winning set; folding this into `mt5_batch_optimize` validate-before-keep

### Skip Combination

One discrete pair from the skip grids `SKIP_TRADE_DAY=0||1||1||5||Y` (days 1–5) × `SKIP_MONTH=0||1||1||12||Y` (months 1–12): 5 × 12 = 60. Baseline no-skip values (`0`) are not part of the 60.

### Skip Robustness Baseline DD

The Survivor's `realticks_equity_dd_pct` from the real-ticks validation backtest (model 4 at that Survivor's **scaled RISK**). Reference DD for the **Skip Robustness Gate**.
_Avoid_: `ohlc_equity_dd_pct`; optimization-pass Equity DD %; the global 17% validation ceiling (unless coincidentally equal); unscaled RISK DD when scaling was applied

### Skip Robustness DD Margin

Fixed **+1.0 percentage point** allowance on top of **Skip Robustness Baseline DD** (absolute equity-DD points, not relative %). Example: baseline 11.4% → allowed ceiling 12.4%.
_Avoid_: 1% relative (×1.01); applying the margin only to some combinations

### Skip Robustness Gate

Final filter in a **separate** script/step: every **Skip Combination**'s maximum equity drawdown must be ≤ **Skip Robustness Baseline DD** + **Skip Robustness DD Margin**. Result is recorded as **Skip Robustness Status** (pass vs `robustness_failed`). Main optimize/validate unchanged; only the selected top-N Survivors are stressed.
_Avoid_: soft-only with no CSV/dashboard update; average DD; best-of-60 DD; inlining into validate-before-keep; backfill

### Skip Robustness Candidate Pool

Two ways to enter skip stress:

1. **Auto after validate:** per job, after Survivors exist, automatically stress the top `min(5, survivor_count)` by `validation_score` (**no backfill**).
2. **Manual:** any **Skip Robustness Pending** Passed row via **Manual Skip Robustness Trigger** (incl. ranks 6–25 and legacy favorites).
   _Avoid_: backfilling ranks 6+ on auto fail; auto-stressing all 25 without operator intent for the rest

### Skip Robustness Keep Limit

How many Survivors per job are selected for the **auto** post-validate stress: **5** (or `min(5, survivor_count)`). Rank key = existing **`validation_score`**. Does not block manual stress of other pending rows.
_Avoid_: a second “return/risk” formula; treating keep-limit as “only 5 rows may ever be stressed”

### Skip Robustness Status

Outcome recorded on a stressed Survivor in CSV and the optimizations dashboard:

- **Pass:** keep `passed=true`; set `skip_robustness_pass=true` (new field/column).
- **Fail:** set `passed=false`, `reject_reason` includes `robustness_failed`; clear `keep` and remove from `best_survivors.csv`; clean up matching `Best/sets` + reports as implemented.
- **Not run** (Survivors ranked below the top-5 stress set): leave as today — still Passed/`keep`, no robustness fields (or explicit `not_run` if stored).
  _Avoid_: staying on Passed after robustness fail; deleting untested ranks 6–25; requiring robustness before first Survivor creation

### Skip Robustness Run Context

**Skip Robustness Optimization** uses the same **scaled RISK** and the same `--from-date` / `--to-date` window as that Survivor’s real-ticks validation backtest. Tester: `Optimization=1` (complete), `Model=4` (Every tick based on real ticks), `ForwardMode=0` (full period, no forward split).
_Avoid_: unscaled/baseline RISK; a different date window; OHLC model; genetic optimization for skip stress; forward mode on the skip run

### Skip Robustness Favorite Sync

If a result is already favorited and later gets **Skip Robustness Status** fail, automatically unfavorite it (DB + Favorites file move/cleanup) and rebuild the all-favorites portfolio when that path applies. Favoriting before robustness has run remains allowed.
_Avoid_: leaving orphan favorites on `robustness_failed` rows; requiring `skip_robustness_pass=true` before any favorite (rejected alternative)

### Skip Robustness Pending

A Passed result (including current favorites) that has **not** been skip-stressed yet. Dashboard shows this as a distinct pending/yellow state: passed, no robustness. Remains favorite-eligible. Ranks 6–25 and legacy rows start here until a stress run completes.
_Avoid_: treating pending as failed; hiding pending from Passed

### Manual Skip Robustness Trigger

Dashboard control labeled **Stress test**, shown **only** when the result is Passed **and** robustness has **not** been run (**Skip Robustness Pending**). Same interaction family as Favorite (enqueue worker → one **Skip Robustness Optimization**). If robustness already ran (pass or fail), **hide** the button — no re-run control ever.
_Avoid_: "Re-run" label or control; showing Stress test after `skip_robustness_pass` or `robustness_failed`; requiring only a batch CLI with no per-row action

### No-Skip Winning Set

A **Survivor**'s winning `.set` always keeps `SKIP_TRADE_DAY=0` and `SKIP_MONTH=0` through main optimize/validate and after skip stress. Calendar skips exist only inside the stress grid.
_Avoid_: writing the lowest-DD skip pair into Best/Favorites; enabling SKIP\_\* as `Y` on committed base sets for the main opt path

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

One discrete value in a permutated parameter's grid: `START, START+STEP, …, STOP`. Example: `LOOKBACK=20||20||20||100||Y` has steps `{20, 40, 60, 80, 100}`.

### Winning set

The `.set` file written for a Survivor (in `Best/sets/`) containing the single chosen value per parameter (no `||` grid fields). Source of the value a Survivor actually used for each Permutated parameter.

### Step usage

For a given Permutated parameter, the count of Survivors whose chosen value equals each Step (exact match — Survivor values are always grid values). Least-used Steps are candidates for removal to shrink the grid (less overfitting, faster optimization).

### Survivor matrix

A table with one row per Survivor and one column per Permutated parameter; each cell holds the value that Survivor chose. Carries identifying columns (base set, profile, symbol, timeframe, pass id) plus validation metrics.

### Value-frequency summary

Per Permutated parameter, the count (and distinct-symbol count and percentage) of Survivors that chose each Step, including zero-count Steps, ordered least-used first.

## Relationships

- Main optimize/validate produces **Survivors** with **No-Skip Winning Sets**; SKIP\_\* stay fixed at 0 / not permutated on that path.
- A **Skip Robustness Optimization** is a separate CLI/final step on existing **Survivors** (winning params frozen; only skip params permutated).
- Per job, **auto** skip stress runs after that job’s validate on the top `min(5, survivor_count)` by `validation_score` (**no backfill**); **Manual Skip Robustness Trigger** covers any remaining **Skip Robustness Pending** row.
- A **Skip Robustness Optimization** uses that Survivor's **Skip Robustness Run Context** and enumerates 60 **Skip Combinations**.
- A **Skip Robustness Gate** requires each combination's equity DD ≤ **Skip Robustness Baseline DD** + **Skip Robustness DD Margin** (+1.0 pp); outcome is **Skip Robustness Status** (F1 fail / P1 pass / U1 not-run for ranks 6–25).
- **Skip Robustness Pending** rows (yellow) are Passed without stress; **Manual Skip Robustness Trigger** can stress one row via dashboard→worker.
- **Skip Robustness Favorite Sync** auto-unfavorites on robustness fail; pre-robustness favorites are allowed.
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
>
> **Dev:** "Do favorites or portfolio need an M5 code path?"
> **Domain expert:** "No. Under **M5 Delivery Scope**, timeframe is opaque identity. Once M5 Survivors exist, favorite + portfolio already work."
>
> **Dev:** "Where do we commit the new grids?"
> **Domain expert:** "`EAs/SetFiles/` only, per **M5 Base-set Matrix** and **M5 Grid Provenance**. Package SetFiles stay local."
>
> **Dev:** "Is skip robustness part of normal validate?"
> **Domain expert:** "No. Main opt/validate still builds **Survivors** with **No-Skip Winning Sets**. **Skip Robustness Optimization** is a final step: auto top-5 after each job, plus **Manual Skip Robustness Trigger** for **Skip Robustness Pending** (yellow) rows."
>
> **Dev:** "Can I favorite before stress?"
> **Domain expert:** "Yes. Pending stays favorite-eligible. If stress later fails, **Skip Robustness Favorite Sync** unfavorites. Pass sets `skip_robustness_pass`; fail sets `passed=false` / `validation_pass=false`, records `robustness_failed`, and does not retain Passed status."
>
> **Dev:** "When is the Stress test button visible?"
> **Domain expert:** "Only on Passed rows that never ran robustness (**Skip Robustness Pending**, yellow). After any robustness run, hide it — no re-run."

## Flagged ambiguities

- M5 means **Chart Timeframe** M5 (job period). Same-TF Classic variant is `TrendM5`; Multi does not use same-TF `HTFM5` — M15-as-HTF is `TrendM15` / `HTFM15`.
- Docs in same change: root README + package README + validation string + constants comment (not code-only).
- Grill closed 2026-08-02: shared understanding complete; ready for implementation plan / execute on request.
- "Monte Carlo" in the request meant **Skip Robustness Optimization** / **Skip Robustness Gate** — not a stochastic simulation.
- Baseline equity DD = **Skip Robustness Baseline DD** (`realticks_equity_dd_pct`) — resolved.
- Margin = **Skip Robustness DD Margin** (+1.0 pp absolute) — resolved.
- Winning `.set` = **No-Skip Winning Set** (`SKIP_*=0`); skip run is stress-only; main path does not permute SKIP — resolved.
- Candidate selection = top `min(5, N)` Survivors by `validation_score` per job; **no backfill** — resolved (supersedes earlier backfill-C).
- **Skip Robustness Run Context** = scaled RISK + same from/to dates + real ticks — resolved.
- Invoke = separate CLI/final step; status written to CSV + optimizations dashboard — resolved.
- On fail: `passed=false` + `robustness_failed`, clear keep / `best_survivors` + Best artifact cleanup (F1); on pass: `skip_robustness_pass=true` (P1); untested ranks 6–25 unchanged (U1) — resolved.
- Skip opt tester = complete + real ticks + no forward (Optimization=1, Model=4, ForwardMode=0) — resolved.
- Favorites on fail = auto-unfavorite + portfolio rebuild (**Skip Robustness Favorite Sync**, option A) — resolved.
- Untested / legacy Passed = **Skip Robustness Pending** (yellow); favorite-eligible; per-row **Manual Skip Robustness Trigger** — resolved.
- Invoke = **both**: auto top-5 after **each job’s** validate (option C+A chain) **and** manual button — resolved.
- Re-run = never; **Stress test** button only while Passed + robustness not run; hide once robustness has run (pass or fail) — resolved.
- Code note: validation today has no coded OHLC↔realticks 1% slack; +1.0 pp is skip-gate only.
- Grill closed 2026-08-05: Skip Robustness shared understanding complete; ready for implementation plan / execute on request.
