# MT5 optimizations automation

Open-source Python tooling for MetaTrader 5 batch forward optimization, pass validation, and portfolio merging. Used by [TradeEcho](https://trade-echo.com) Ultimate subscribers with the dashboard at `/dashboard/optimizations`.

## What is included

| Script                            | Purpose                                                                                               |
| --------------------------------- | ----------------------------------------------------------------------------------------------------- |
| `mt5_heartbeat.py`                | Poll TradeEcho API; run dashboard Start/Stop/Clean/Resume                                             |
| `start_mt5_heartbeat.bat`         | Launch `mt5_heartbeat.py` from this folder (visible console)                                          |
| `install_heartbeat_startup.bat`   | Install Windows Startup shortcut for the heartbeat worker                                             |
| `uninstall_heartbeat_startup.bat` | Remove the Windows Startup heartbeat shortcut                                                         |
| `mt5_stop.py`                     | Stop `terminal64.exe`, leftover `metatester64.exe` agents, batch Python, and free localhost:3000–3015 |
| `mt5_clean_cache.py`              | Clear MT5 tester cache and local batch artifacts                                                      |
| `mt5_sync_favorites.py`           | Copy dashboard favorites from `Best/` to `Favorites/`                                                 |
| `mt5_batch_optimize.py`           | Batch forward optimization + per-job validation                                                       |
| `mt5_opt_report.py`               | Optimization XML parsing and candidate filters                                                        |
| `mt5_equity_metrics.py`           | Equity-curve metrics from backtest HTML                                                               |
| `mt5_db_report.py`                | Push run status and validation rows to Postgres                                                       |
| `mt5_portfolio_favorites.py`      | Merge dashboard favorites into per-company portfolio snapshots                                        |
| `mt5_portfolio_merge.py`          | Trade-by-trade portfolio merge helpers                                                                |
| `mt5_favorite_strategy.py`        | Copy a survivor's `.set` + reports from `Best/` to `Favorites/`                                       |
| `mt5_skip_robustness.py`          | Skip Robustness stress: skip-day×skip-month combos on real ticks                                      |
| `mt5_tester_runtime.py`           | Shared tester ini / report-path / terminal helpers                                                    |
| `mt5_ea_inputs.py`                | Env-overridable EA input names and skip grids                                                         |
| `mt5_step_usage.py`               | Excel workbook: which grid steps survivors used                                                       |
| `mt5_set_files.py`                | Generic `.set` discovery (nested or flat layouts)                                                     |

## What is **not** included (private)

- **`.set` parameter grids** — EA-specific. Place yours in `SetFiles/` (gitignored; see layouts below).
- MQ5 Expert Advisors — distributed separately on MQL5 Market.
- Database access — handled by TradeEcho API; scripts never connect to Postgres directly.

## Requirements

- Windows with **MetaTrader 5** (`terminal64.exe`)
- Python **3.10+** — `pip install -r requirements.txt` (`defusedxml` for report XML parsing)
- Compiled EA (`.ex5`) in your MT5 `MQL5\Experts` folder
- Active [**TradeEcho Ultimate**](https://trade-echo.com/pricing) subscription (`TRADEECHO_USER_ID` + API check)

## Setup

1. **Clone this repo** and open a terminal in the folder.

2. **Install Python dependencies:**

   ```powershell
   pip install -r requirements.txt
   ```

3. **Copy [`.env.example`](.env.example) to `.env`** and set at minimum:
   - `TRADEECHO_USER_ID` — your User ID from [TradeEcho](https://trade-echo.com/dashboard) dashboard → Setup
   - `MT5_EXPERT` — compiled EA filename (e.g. `MyEA.ex5`)

4. **Add `.set` grids** under `SetFiles/` (see layouts below). These are not shipped in the repo.

5. **Install your EA** in MetaTrader 5 (`File → Open Data Folder → MQL5\Experts`).

6. **Optional — TradeEcho dashboard:** set `TRADEECHO_USER_ID` in `.env` and run the optimizer heartbeat worker so Start/Stop in the web UI controls your PC (see [Dashboard integration](#tradeecho-dashboard-integration)).

Scripts load `.env` and `.env.local` from this folder.

## Operator commands

From **this folder**, run Python directly:

| Workflow                         | Command                                                                                                                          |
| -------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| Dashboard worker                 | `python mt5_heartbeat.py`                                                                                                        |
| Dashboard worker (bat)           | `.\start_mt5_heartbeat.bat`                                                                                                      |
| Install heartbeat on startup     | `.\install_heartbeat_startup.bat`                                                                                                |
| Uninstall heartbeat startup      | `.\uninstall_heartbeat_startup.bat`                                                                                              |
| Full batch optimize + validate   | `python mt5_batch_optimize.py --expert TrendReversalCluster.ex5 --from-date 2014.07.02 --to-date 2026.07.02`                     |
| Batch optimize only              | add `--no-validate` to the optimize command                                                                                      |
| Re-validate `reports/`           | `python mt5_batch_optimize.py --validate-only`                                                                                   |
| Stop MT5 + tester agents + batch | `python mt5_stop.py`                                                                                                             |
| Clean cache + artifacts          | `python mt5_clean_cache.py` (local only; dashboard **Clean** also clears optimization DB rows for your user and keeps favorites) |
| Preview clean                    | `python mt5_clean_cache.py --dry-run`                                                                                            |
| Cache only                       | `python mt5_clean_cache.py --cache-only`                                                                                         |
| Artifacts only                   | `python mt5_clean_cache.py --artifacts-only`                                                                                     |
| Sync favorites                   | `python mt5_sync_favorites.py`                                                                                                   |
| Build portfolio                  | `python mt5_portfolio_favorites.py`                                                                                              |
| Step-usage report                | `python mt5_step_usage.py`                                                                                                       |
| Skip robustness (one Survivor)   | `python mt5_skip_robustness.py --set-file … --symbol … --timeframe … --from-date … --to-date … --baseline-dd … --expert …`       |
| Unit tests                       | `python -m pytest -q`                                                                                                            |

Scripts auto-detect one of two layouts under `SetFiles/` (or `MT5_SET_DIR` / `--validate-set-dir`). If package `SetFiles/` is empty, grids fall back to `../../EAs/SetFiles` (Classic / Multi / SwingHA).

### Nested (strategy + chart timeframe)

```
SetFiles/
  Classic/
    M5/
      TrendCurrent.set
    M15/
      TrendCurrent.set
    H1/
      TrendH4.set
  Multi/
    M5/
      HTFM15.set
    H1/
      HTFH4.set
  SwingHA/
    M5/
      TrendCurrent.set
    M15/
      TrendCurrent.set
    D1/
      TrendCurrent.set
    W1/
      TrendCurrent.set
```

Staged for MT5 as flat names like `Classic_M15_TrendCurrent.set`. Chart TFs beyond the CLI default (M5/M15/H1/H4) are valid when matching folders exist under a strategy (e.g. SwingHA `D1`/`W1`).

### Flat

```
SetFiles/
  EURUSD_M15_grid.set
  GBPUSD_H1_grid.set
```

Restrict runs with `--strategies Classic Multi SwingHA` (nested) or `--strategies Default` (flat).

## Environment variables

| Variable                      | Required | Description                                                                              |
| ----------------------------- | -------- | ---------------------------------------------------------------------------------------- |
| `MT5_SET_DIR`                 | No\*     | Folder with `.set` grids (default: `./SetFiles` if populated, else `../../EAs/SetFiles`) |
| `MT5_EXPERT`                  | Yes\*\*  | Compiled EA in `MQL5\Experts` (e.g. `MyEA.ex5`)                                          |
| `MT5_TERMINAL`                | No       | Path to `terminal64.exe` (else first existing of FTMO / MetaTrader 5 install)            |
| `MT5_RISK_INPUT`              | No       | EA risk input name (default: `RISK`)                                                     |
| `MT5_SKIP_DAY_INPUT`          | No       | Skip-day input name (default: `SKIP_TRADE_DAY`)                                          |
| `MT5_SKIP_MONTH_INPUT`        | No       | Skip-month input name (default: `SKIP_MONTH`)                                            |
| `MT5_SKIP_DAY_GRID`           | No       | Skip-day optimize grid (default: `0\|\|1\|\|1\|\|5\|\|Y`)                                |
| `MT5_SKIP_MONTH_GRID`         | No       | Skip-month optimize grid (default: `0\|\|1\|\|1\|\|12\|\|Y`)                             |
| `TRADEECHO_USER_ID`           | Yes      | Your TradeEcho User ID (Ultimate plan)                                                   |
| `TRADEECHO_API_BASE_URL`      | No       | API host (default: `https://trade-echo.com`)                                             |
| `TRADEECHO_SKIP_ACCESS_CHECK` | No       | `1` to skip subscription check (local dev only)                                          |

\*Required when `SetFiles/` is empty and you do not pass `--validate-set-dir`.

\*\*Or pass `--expert` on every optimization run (not needed for `--validate-only`).

## How the pipeline works

Default mode (**optimize + validate**) runs this sequence for each job (symbol × timeframe × `.set` file):

1. Build one Strategy Tester `.ini` in `generated_configs/`.
2. Copy the `.set` file into `<mt5-data>/MQL5/Profiles/Tester` and launch `terminal64.exe /config:…`.
3. Wait for optimization to finish (`ShutdownTerminal=1` closes MT5).
4. Parse the forward optimization XML report, select top passes, and run OHLC + real-ticks backtests. Real-tick probes (`Model=4`) start the tester minimized — that mode freezes the MT5 UI thread until ticks finish.
5. Copy surviving parameter sets and reports into `reports/Best/`.

When linked to the TradeEcho dashboard (heartbeat worker running), `mt5_db_report.py` pushes job progress and validation rows through the TradeEcho API.

MT5 ignores `[Tester]` config when another `terminal64.exe` is already running. Local tester agents bind **`127.0.0.1:3000`**, the same default as Next.js (`pnpm dev`). Preflight kills foreign listeners on **:3000** by PID (e.g. Next.js) before launching the tester; if the port is still occupied afterward, the run fails rather than producing empty stub reports / `tester agent authorization error`. `python mt5_stop.py` (and each finished/timeout optimization job) kills leftover `metatester64.exe` agents and frees **3000–3015** of MT5 listeners only — it does not stop a restarted `pnpm dev`. Do not run a second MT5 terminal in parallel.

Set `MT5_DATA_DIR` or pass `--mt5-data` when auto-detection fails (e.g. `%APPDATA%\MetaQuotes\Terminal\<id>`).

## Example runs

All examples assume you are in this folder and `.env` is configured.

### Full batch — optimize + validate (default)

Runs every discovered `.set` × symbol × timeframe, validates top passes after each job:

```powershell
python mt5_batch_optimize.py `
  --from-date 2020.01.01 `
  --to-date 2025.12.31 `
  --terminal "C:\Program Files\MetaTrader FTMO\terminal64.exe"
```

### Optimize only — skip validation

Useful when you want raw optimization reports first and will validate later:

```powershell
python mt5_batch_optimize.py `
  --from-date 2020.01.01 `
  --to-date 2025.12.31 `
  --no-validate
```

### Validate only — re-run gates on existing reports

Re-processes XML already in `reports/` without launching new optimizations. Honors `--symbols` and `--timeframes`:

```powershell
python mt5_batch_optimize.py --validate-only --verbose
```

Single job:

```powershell
python mt5_batch_optimize.py --validate-only `
  --symbols EURUSD --timeframes M15 `
  --param-files SetFiles/Classic/M15/TrendCurrent.set `
  --from-date 2020.01.01 --to-date 2025.12.31 --verbose
```

**No candidates after optimization?** Check forward-selection counts in `--verbose` output (`back_sharpe`, `forward_sharpe`, `forward_result` rejections).

**No survivors after validation?** Check `reject_reason` in `best_summary.csv` for `low_calmar`, `low_validation_sharpe`, `high_equity_dd`, `dd_fail`, `risk_scaling_zero_dd`, or `risk_scaling_probe_failed`. Empty stub reports (`deposit=0`, `bars=0`) usually mean **localhost:3000 was taken** during the run — preflight normally clears foreign listeners (e.g. `pnpm dev`); if something rebinds mid-batch, stop it and retry.

### Resume after interruption

Skip jobs whose optimization reports already exist:

```powershell
python mt5_batch_optimize.py `
  --from-date 2020.01.01 `
  --to-date 2025.12.31 `
  --resume
```

### Narrow the job matrix

```powershell
python mt5_batch_optimize.py `
  --from-date 2020.01.01 `
  --to-date 2025.12.31 `
  --symbols EURUSD GBPUSD `
  --timeframes M15 H1 `
  --strategies Classic
```

### Slow complete optimization (real ticks)

Default is fast genetic on 1-minute OHLC. For complete optimization on every tick:

```powershell
python mt5_batch_optimize.py `
  --from-date 2020.01.01 `
  --to-date 2025.12.31 `
  --complete-opt
```

### Survivor step-usage report

After validation, generate an Excel workbook showing which optimization grid steps the kept survivors used:

```powershell
python mt5_step_usage.py
```

Reads `reports/Best/best_survivors.csv`, compares chosen values to permutated (`Y`) inputs in `SetFiles/**`, writes `reports/step_usage.xlsx`. Domain terms: [CONTEXT.md](CONTEXT.md).

Workbook sheets:

- `Survivors` — one row per kept survivor with validation metrics and one column per permutated input
- `ValueCounts` — global counts per base `.set`, parameter, and grid value (including zero-use values)
- One detail sheet per base `.set` — survivor matrix, value counts, and per-parameter bar charts

```powershell
python mt5_step_usage.py --best-dir "C:\path\to\Best" --out reports/custom_step_usage.xlsx --allow-empty
```

### Per-company favorites portfolio

Merge favorited strategies into one trade-by-trade backtest **per broker company** and save each snapshot to Postgres. Cashflows are scaled to the shared account using **equity at entry** (lot size frozen for the life of the position), not re-levered at close when other strategies move the balance.

```powershell
python mt5_portfolio_favorites.py
```

Requires `TRADEECHO_USER_ID` only. Re-run after favorites change (rebuilds one portfolio snapshot per broker company). After upgrading from the old merged `all-favorites` snapshot, run this once to migrate. The dashboard shows **View portfolio** when a company is selected and that company's snapshot exists.

### Run unit tests

```powershell
python -m pytest -q
```

## Modes summary

| Mode                          | Command                                                  | Behavior                        |
| ----------------------------- | -------------------------------------------------------- | ------------------------------- |
| Optimize + validate (default) | `python mt5_batch_optimize.py --from-date … --to-date …` | Full pipeline per job           |
| Optimize only                 | add `--no-validate`                                      | Skip validation after each job  |
| Validate only                 | `python mt5_batch_optimize.py --validate-only`           | Re-validate existing `reports/` |

## Default job matrix

- **Symbols:** 28 majors/crosses (EURUSD, GBPUSD, … CHFJPY) — override with `--symbols`
- **Timeframes:** M5, M15, H1, H4 — override with `--timeframes` (e.g. add `D1` `W1` when SwingHA SetFiles exist for those chart TFs)
- **Param files:** all `.set` files under `SetFiles/` (auto-discovered). Staged as flat names like `Classic_M15_TrendH4.set`. Job count = param files × symbols × `DEFAULT_RUNS_PER_SET_FILE` (default **1** per file).
- **Expert:** `MT5_EXPERT` env or `--expert`
- **Forward mode:** `2` (built-in forward split; use `--forward-date` when `--forward-mode=4`)

## Validation logic

Each `.set` file is scheduled **`DEFAULT_RUNS_PER_SET_FILE` times** (default **1**) per symbol/timeframe. Every run gets a unique report stem and is validated independently.

Parses `reports/*.xml` (see [Forward data](#forward-data) below).

| Step                              | Behavior                                                                                                                                                                                                           |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Forward 1/3, Custom max criterion | INI defaults: `ForwardMode=2`, `OptimizationCriterion=6`                                                                                                                                                           |
| Optimization engine               | Fast genetic (`--optimization 2`) on 1-minute OHLC (`--model 1`); use `--complete-opt` for slow complete + real ticks                                                                                              |
| Custom-desc scan                  | Sort by in-sample **Custom/Result** descending; stop when Custom/Result **< 6**                                                                                                                                    |
| Back gates (per row in scan)      | Sharpe **≥ 1.0** (`--min-sharpe`)                                                                                                                                                                                  |
| Forward gates (per row)           | Forward Sharpe **≥ 1.0** (`--min-sharpe`), forward Result **≥ 3** (required)                                                                                                                                       |
| Pick from optimization            | Rank survivors by **Custom + forward Result**; take top `--validate-top-n-per-symbol` (default 15) per symbol                                                                                                      |
| Risk scaling (OHLC measure)       | One OHLC backtest at baseline RISK → set RISK once: `RISK × target / equity_DD` (scale-up or scale-down, including RISK **&lt; 1**); clamp RISK to **≥ 0.1**. OHLC DD is the scale input only — not a reject gate. |
| Real-ticks backtest (model 4)     | **One** full-period backtest at the scaled RISK                                                                                                                                                                    |
| Real-ticks validation gates       | Sharpe **≥ 1.0**, Calmar **≥ 1.0**, equity DD **≤ target × 1.12** (default target 15 → ceiling **16.8**) on real ticks only                                                                                        |
| Final ranking among survivors     | Composite `validation_score` on real ticks; keep top `--validate-keep-top-k` (default **15**)                                                                                                                      |

Recovery, LR Correlation, CAGR, K-Ratio, stagnation, ulcer index, time under water, and margin level are **logged** in `best_summary.csv` but **not** rejection gates. Calmar is both a gate and a factor in `validation_score`.

Dashboard soft-pass treats return-only rejects as amber “low return”: live token `low_calmar`, plus orphan historical `low_cagr` when Calmar is missing. A one-shot DB/CSV migrator rewrote eligible `low_cagr` rows and was removed on purpose.

### Forward data

Three sources (checked in order):

1. **Inline columns** — `Back Result` and `Forward Result` in the same `.xml`. Sharpe/Recovery on each row are treated as **forward-period** metrics; `Back Result` replaces in-sample `Custom` for forward selection.
2. **Merged files** — `report.xml` (in-sample) + `report.forward.xml` joined on `Pass`.
3. **No forward data** — validation is skipped (batch) / refused; re-run optimization. Partial back reports without `.forward.xml` are deleted before the next opt attempt.

### Optimization report columns

The parser in [`mt5_opt_report.py`](mt5_opt_report.py) maps headers automatically. Standard English MT5 export:

| Metric                            | Typical column                  |
| --------------------------------- | ------------------------------- |
| Pass                              | `Pass`                          |
| Custom (optimization criterion)   | `Custom` or `Result`            |
| Sharpe                            | `Sharpe Ratio`                  |
| Recovery                          | `Recovery Factor`               |
| Equity DD                         | `Equity DD %`                   |
| Trades                            | `Trades`                        |
| Profit                            | `Profit`                        |
| Back / Forward (UI combined view) | `Back Result`, `Forward Result` |

Override any column with `--col-sharpe`, `--col-recovery`, `--col-custom`, etc.

### Validation CSVs

**`best_summary.csv`** — all validated rows (appended across jobs). Key columns: gate metrics `validation_sharpe`, `validation_calmar`, `validation_pass`, and `reject_reason` (`low_calmar`, `low_validation_sharpe`, `high_equity_dd`, `risk_scaling_zero_dd`, `risk_scaling_probe_failed`, `dd_fail`, `missing_validation_metrics`, `backtest_error`). Informational columns include `validation_cagr_pct`, `validation_recovery`, `validation_score`, equity-quality metrics, DD %, and risk-scaling fields.

**`best_survivors.csv`** — subset where `keep=true` (header-only when none pass).

### Skip Robustness (calendar-skip stress)

After each job’s validate (unless `--no-skip-robustness`, or dashboard Start/Resume with **Run robustness after validate** unchecked → `skipRobustness: false`), the top **5** Survivors by `validation_score` are stress-tested automatically when their `.set` declares the configured skip-day/skip-month inputs (defaults `SKIP_TRADE_DAY` / `SKIP_MONTH`; override via `MT5_SKIP_*`):

1. Freeze the winning `.set` (keep `SKIP_*=0` on the Survivor).
2. Run complete optimization (`Optimization=1`, `Model=4`, `ForwardMode=0`) with only the configured skip grids (default 5×12 = 60 combos; count follows `MT5_SKIP_*_GRID`) at the Survivor’s scaled RISK and date window.
3. **Gate:** every combo’s equity DD ≤ `realticks_equity_dd_pct` + **1.0** pp.
4. **Pass:** `skip_robustness_pass=true` (Survivor unchanged, still no-skip).  
   **Fail:** `reject_reason=robustness_failed`, `keep=false`, remove Best artifacts; auto-unfavorite if favorited.

Dashboard: Passed/Favorites rows without stress show yellow **No stress** + **Stress test** button (manual, any pending Passed). Button hides after any robustness run. Domain terms: [CONTEXT.md](CONTEXT.md).

### Survivor output (`reports/Best/`)

| Path                 | Contents                                            |
| -------------------- | --------------------------------------------------- |
| `sets/`              | Winning `.set` files                                |
| `reports/<symbol>/`  | OHLC + real-ticks reports + source optimization XML |
| `best_summary.csv`   | All validation rows (appended across jobs)          |
| `best_survivors.csv` | Rows where `keep=true`                              |

Override output folder with `--best-dir`.

## TradeEcho dashboard integration

Ultimate subscribers can control runs from the TradeEcho optimizations dashboard instead of typing CLI commands.

### Step 1 — Configure `.env`

Copy [`.env.example`](.env.example) to `.env` (or `.env.local`) in **this folder** — the same folder as `README.md`:

```env
TRADEECHO_USER_ID=your-uuid-from-dashboard-setup
MT5_EXPERT=MyEA.ex5
```

Use the User ID shown on `/dashboard/setup` → **MT5 Optimizations** tab. Do not leave `TRADEECHO_USER_ID` empty; an unset value causes `TRADEECHO_USER_ID is not set` at startup.

Optional: `TRADEECHO_API_BASE_URL` (defaults to the production TradeEcho API host).

### Step 2 — Start the optimizer worker

Keep a terminal open on your Windows PC with the **optimizer heartbeat worker** running. It polls the TradeEcho API every **10 seconds**, reports idle/busy status, and executes **Start**, **Stop**, **Clean**, and **Resume** commands from the web UI.

Open the terminal in **this folder** (where `.env` lives), then start the worker:

```powershell
python mt5_heartbeat.py
```

Or double-click `start_mt5_heartbeat.bat` (same folder; uses `python` on PATH).

You should see `[mt5-heartbeat] Starting optimizer heartbeat (10s poll)`.

**Optional — start at Windows login:** run `.\install_heartbeat_startup.bat` once. It creates a Startup shortcut that opens a visible console and runs the worker after you sign in. Remove it with `.\uninstall_heartbeat_startup.bat`.

**`TRADEECHO_USER_ID is not set`?** Confirm the variable is set to your UUID (not blank) in `.env` or `.env.local` in this folder, then retry.

### Step 3 — Start a run from the dashboard

Open `/dashboard/optimizations`, choose date range, symbols, timeframes, strategies (Classic / Multi / SwingHA), optimization mode (fast genetic vs slow complete), **currency**, **account balance**, and **max equity drawdown %** (default 15; the worker sets `--target-equity-dd` to that value and `--max-equity-dd` to `target × 1.12`, e.g. 15 → 16.8, 4 → 4.48), then click **Start**. The worker launches `mt5_batch_optimize.py` and syncs results to your dashboard automatically.

### Step 4 — Monitor live results

While the worker is running, the dashboard shows batch progress, pass/fail feed, passed strategies, and parameter stats. Results are written by `mt5_db_report.py` during each job.

### Step 5 — Favorites and portfolio

1. Favorite passed strategies in the dashboard (records in `optimization_favorites`).
2. With the heartbeat worker running, favoriting or unfavoriting in the dashboard enqueues a worker command that moves matching `.set` and report files between `reports/Best/` and `reports/Favorites/`, then rebuilds per-company portfolio snapshots. **View portfolio** appears after you select a Company filter.
3. CLI-only: after favoriting, run `python mt5_sync_favorites.py`, then:

   ```powershell
   python mt5_portfolio_favorites.py
   ```

   If a favorite has no local realticks report (for example after **Clean** removed `Best/` artifacts), the portfolio builder uses the equity curve stored in the dashboard for that strategy. Removing the last favorite clears the stored portfolio snapshot.

4. Select a company in the dashboard and open **View portfolio**.

### CLI-only (no worker)

You can use all Python scripts without the dashboard worker. Run `mt5_batch_optimize.py` directly from this folder; results stay local under `reports/`. Live dashboard sync during a run still needs `TRADEECHO_USER_ID` in `.env`; remote Start/Stop from the web UI needs the heartbeat worker. Without the worker, run `python mt5_sync_favorites.py` after favoriting to copy files into `reports/Favorites/`.

## Local artifacts (gitignored)

| Path                 | Description                                             |
| -------------------- | ------------------------------------------------------- |
| `generated_configs/` | One `.ini` per optimization job                         |
| `reports/`           | Optimization XML/HTML reports (`NNN_SYMBOL_TF_Profile`) |
| `validate_staging/`  | Temporary validation backtest files                     |
| `mt5_batch_runs.csv` | Per-job status log (used by `--resume`)                 |

Use `--resume` to skip jobs whose reports already exist (**both** `report.xml` and `report.forward.xml` when forward mode is on). Incomplete pairs (back `.xml` without `.forward.xml`) are deleted automatically before re-optimization; validation is skipped until forward data exists. Deleting `mt5_batch_runs.csv` resets resume state.

## Key CLI options

| Option                        | Default                                           | Description                                                                    |
| ----------------------------- | ------------------------------------------------- | ------------------------------------------------------------------------------ |
| `--terminal`                  | `C:\Program Files\MetaTrader FTMO\terminal64.exe` | Path to MT5 terminal                                                           |
| `--mt5-data`                  | auto via `origin.txt`                             | MT5 data directory (or `--portable`)                                           |
| `--work-dir`                  | `.`                                               | Root for generated files and logs                                              |
| `--symbols` / `--timeframes`  | 28 symbols / M5 M15 H1 H4                         | Job matrix; also filters validate-only                                         |
| `--param-files`               | all under `SetFiles/`                             | Optimization parameter files (auto-discovered)                                 |
| `--strategies`                | all discovered                                    | Restrict to `Classic`, `Multi`, and/or `SwingHA`                               |
| `--from-date` / `--to-date`   | required (except validate-only)                   | `YYYY.MM.DD`                                                                   |
| `--optimization`              | `2`                                               | Fast genetic; use `--complete-opt` for complete + real ticks                   |
| `--model`                     | `1`                                               | 1-minute OHLC by default                                                       |
| `--complete-opt`              | off                                               | Shorthand: `--optimization 1` + `--model 4`                                    |
| `--criterion`                 | `6`                                               | Optimization criterion                                                         |
| `--forward-mode`              | `2`                                               | Forward testing mode                                                           |
| `--validate-top-n-per-symbol` | `15`                                              | Top passes per symbol to backtest                                              |
| `--validate-keep-top-k`       | `15`                                              | Top survivors per job after validation ranking                                 |
| `--min-forward-result`        | `3`                                               | Forward Result gate (≥)                                                        |
| `--min-back-result`           | `6`                                               | Optimization Custom/Result gate (≥)                                            |
| `--min-sharpe`                | `1.0`                                             | Sharpe gate (≥) for back, forward, and real-ticks validation                   |
| `--min-validation-calmar`     | `1`                                               | Real-ticks Calmar gate (≥)                                                     |
| `--deposit` / `--currency`    | `100000` / `USD`                                  | Tester account balance and currency (dashboard Start/Resume forwards these)    |
| `--target-equity-dd`          | `15.0`                                            | Linear RISK scaling target equity DD % (dashboard **Max equity drawdown %**)   |
| `--min-scaled-risk`           | `0.1`                                             | Clamp floor for scaled RISK (does not reject; avoids RISK 0)                   |
| `--max-equity-dd`             | `16.8`                                            | Max equity DD % on real ticks after scaling; dashboard derives `target × 1.12` |
| `--no-risk-scaling`           | off                                               | Disable RISK scaling OHLC probe                                                |
| `--verbose`                   | off                                               | Mapping, distributions, rejection diagnostics                                  |
| `--backtest-timeout-seconds`  | `1800`                                            | Per validation backtest timeout                                                |
| `--best-dir`                  | `reports/Best`                                    | Survivor output folder                                                         |
| `--delay-seconds`             | `2`                                               | Pause between jobs                                                             |
| `--timeout-minutes`           | `0` (none)                                        | Per-job optimization timeout                                                   |
| `--resume`                    | off                                               | Skip jobs with existing reports                                                |

Run `python mt5_batch_optimize.py --help` for the full list.

## Further reading

- [CONTEXT.md](CONTEXT.md) — domain glossary (survivor, permutated parameter, step usage, etc.)

## License

MIT — review the code before running on a machine with broker credentials.
