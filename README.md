# MT5 optimizations automation

Open-source Python tooling for MetaTrader 5 batch forward optimization, pass validation, and portfolio merging. Used by [TradeEcho](https://trade-echo.com) Ultimate subscribers with the dashboard at `/dashboard/optimizations`.

## What is included

| Script                       | Purpose                                                         |
| ---------------------------- | --------------------------------------------------------------- |
| `mt5_heartbeat.py`           | Poll TradeEcho API; run dashboard Start/Stop/Clean/Resume       |
| `mt5_stop.py`                | Stop `terminal64.exe` and batch optimizer Python processes      |
| `mt5_clean_cache.py`         | Clear MT5 tester cache and local batch artifacts                |
| `mt5_sync_favorites.py`      | Copy dashboard favorites from `Best/` to `Favorites/`           |
| `mt5_batch_optimize.py`      | Batch forward optimization + per-job validation                 |
| `mt5_opt_report.py`          | Optimization XML parsing and candidate filters                  |
| `mt5_equity_metrics.py`      | Equity-curve metrics from backtest HTML                         |
| `mt5_db_report.py`           | Push run status and validation rows to Postgres                 |
| `mt5_portfolio_favorites.py` | Merge all dashboard favorites into one portfolio snapshot       |
| `mt5_portfolio_merge.py`     | Trade-by-trade portfolio merge helpers                          |
| `mt5_favorite_strategy.py`   | Copy a survivor's `.set` + reports from `Best/` to `Favorites/` |
| `mt5_step_usage.py`          | Excel workbook: which grid steps survivors used                 |
| `mt5_set_files.py`           | Generic `.set` discovery (nested or flat layouts)               |

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

| Workflow                       | Command                                                                                                                          |
| ------------------------------ | -------------------------------------------------------------------------------------------------------------------------------- |
| Dashboard worker               | `python mt5_heartbeat.py`                                                                                                        |
| Full batch optimize + validate | `python mt5_batch_optimize.py --expert TrendReversalCluster.ex5 --from-date 2016.07.02 --to-date 2026.07.02`                     |
| Batch optimize only            | add `--no-validate` to the optimize command                                                                                      |
| Re-validate `reports/`         | `python mt5_batch_optimize.py --validate-only`                                                                                   |
| Stop MT5 + batch Python        | `python mt5_stop.py`                                                                                                             |
| Clean cache + artifacts        | `python mt5_clean_cache.py` (local only; dashboard **Clean** also clears optimization DB rows for your user and keeps favorites) |
| Preview clean                  | `python mt5_clean_cache.py --dry-run`                                                                                            |
| Cache only                     | `python mt5_clean_cache.py --cache-only`                                                                                         |
| Artifacts only                 | `python mt5_clean_cache.py --artifacts-only`                                                                                     |
| Sync favorites                 | `python mt5_sync_favorites.py`                                                                                                   |
| Build portfolio                | `python mt5_portfolio_favorites.py`                                                                                              |
| Step-usage report              | `python mt5_step_usage.py`                                                                                                       |
| Unit tests                     | `python -m pytest -q`                                                                                                            |

Scripts auto-detect one of two layouts under `SetFiles/` (or `MT5_SET_DIR` / `--validate-set-dir`):

### Nested (strategy + chart timeframe)

```
SetFiles/
  Classic/
    M15/
      TrendCurrent.set
  Multi/
    H1/
      HTFH4.set
```

Staged for MT5 as flat names like `Classic_M15_TrendCurrent.set`.

### Flat

```
SetFiles/
  EURUSD_M15_grid.set
  GBPUSD_H1_grid.set
```

Restrict runs with `--strategies Classic Multi` (nested) or `--strategies Default` (flat).

## Environment variables

| Variable                      | Required | Description                                      |
| ----------------------------- | -------- | ------------------------------------------------ |
| `MT5_SET_DIR`                 | No\*     | Folder with `.set` grids (default: `./SetFiles`) |
| `MT5_EXPERT`                  | Yes\*\*  | Compiled EA in `MQL5\Experts` (e.g. `MyEA.ex5`)  |
| `TRADEECHO_USER_ID`           | Yes      | Your TradeEcho User ID (Ultimate plan)           |
| `TRADEECHO_API_BASE_URL`      | No       | API host (default: `https://trade-echo.com`)     |
| `TRADEECHO_SKIP_ACCESS_CHECK` | No       | `1` to skip subscription check (local dev only)  |

\*Required when `SetFiles/` is empty and you do not pass `--validate-set-dir`.

\*\*Or pass `--expert` on every optimization run (not needed for `--validate-only`).

## How the pipeline works

Default mode (**optimize + validate**) runs this sequence for each job (symbol × timeframe × `.set` file):

1. Build one Strategy Tester `.ini` in `generated_configs/`.
2. Copy the `.set` file into `<mt5-data>/MQL5/Profiles/Tester` and launch `terminal64.exe /config:…`.
3. Wait for optimization to finish (`ShutdownTerminal=1` closes MT5).
4. Parse the forward optimization XML report, select top passes, and run OHLC + real-ticks backtests.
5. Copy surviving parameter sets and reports into `reports/Best/`.

When linked to the TradeEcho dashboard (heartbeat worker running), `mt5_db_report.py` pushes job progress and validation rows through the TradeEcho API.

MT5 ignores `[Tester]` config when another `terminal64.exe` is already running. The script calls `taskkill` before each job and before validation — do not run MT5 manually in parallel.

Set `MT5_DATA_DIR` or pass `--mt5-data` when auto-detection fails (e.g. `%APPDATA%\MetaQuotes\Terminal\<id>`).

## Example runs

All examples assume you are in this folder and `.env` is configured.

### Full batch — optimize + validate (default)

Runs every discovered `.set` × symbol × timeframe, validates top passes after each job:

```powershell
python mt5_batch_optimize.py `
  --from-date 2020.01.01 `
  --to-date 2025.12.31 `
  --terminal "C:\Program Files\MetaTrader 5\terminal64.exe"
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

**No survivors after validation?** Check `reject_reason` in `best_summary.csv` for `low_cagr`, `low_validation_sharpe`, `high_equity_dd`, `dd_fail`, or risk-scaling failures.

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

### All-favorites portfolio

Merge every strategy you favorited in the TradeEcho dashboard into one trade-by-trade backtest and save the snapshot to Postgres:

```powershell
python mt5_portfolio_favorites.py
```

Requires `TRADEECHO_USER_ID` only. Re-run after favorites change. The dashboard shows **View portfolio (all)** when a snapshot exists.

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
- **Timeframes:** M15, H1, H4 — override with `--timeframes`
- **Param files:** all `.set` files under `SetFiles/` (auto-discovered). Staged as flat names like `Classic_M15_TrendH4.set`. Job count = param files × symbols × `DEFAULT_RUNS_PER_SET_FILE` (default **1** per file).
- **Expert:** `MT5_EXPERT` env or `--expert`
- **Forward mode:** `2` (built-in forward split; use `--forward-date` when `--forward-mode=4`)

## Validation logic

Each `.set` file is scheduled **`DEFAULT_RUNS_PER_SET_FILE` times** (default **1**) per symbol/timeframe. Every run gets a unique report stem and is validated independently.

Parses `reports/*.xml` (see [Forward data](#forward-data) below).

| Step                              | Behavior                                                                                                                        |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| Forward 1/3, Custom max criterion | INI defaults: `ForwardMode=2`, `OptimizationCriterion=6`                                                                        |
| Optimization engine               | Fast genetic (`--optimization 2`) on 1-minute OHLC (`--model 1`); use `--complete-opt` for slow complete + real ticks           |
| Custom-desc scan                  | Sort by in-sample **Custom/Result** descending; stop when Custom/Result **< 6**                                                 |
| Back gates (per row in scan)      | Sharpe **≥ 1.5** (`--min-sharpe`)                                                                                               |
| Forward gates (per row)           | Forward Sharpe **≥ 1.5** (`--min-sharpe`), forward Result **≥ 3** (required)                                                    |
| Pick from optimization            | Rank survivors by **Custom + forward Result**; take top `--validate-top-n-per-symbol` (default 25) per symbol                   |
| Risk scaling probe (OHLC)         | Baseline RISK → linear scale toward **15%** equity DD; reject if scaled RISK **< 1**, or scaled OHLC or real-ticks DD **> 17%** |
| Real-ticks backtest (model 4)     | Full-period backtest at scaled or baseline RISK                                                                                 |
| Real-ticks validation gates       | Sharpe **≥ 1.5**, CAGR **≥ 10%**, equity DD **≤ 17%** on OHLC and real ticks at scaled RISK                                     |
| Final ranking among survivors     | Composite `validation_score` on real ticks; keep top `--validate-keep-top-k` (default **25**)                                   |

Recovery, LR Correlation, Calmar, K-Ratio, stagnation, ulcer index, time under water, and margin level are **logged** in `best_summary.csv` but **not** rejection gates.

### Forward data

Three sources (checked in order):

1. **Inline columns** — `Back Result` and `Forward Result` in the same `.xml`. Sharpe/Recovery on each row are treated as **forward-period** metrics; `Back Result` replaces in-sample `Custom` for forward selection.
2. **Merged files** — `report.xml` (in-sample) + `report.forward.xml` joined on `Pass`.
3. **No forward data** — warns; rows without forward metrics are rejected.

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

**`best_summary.csv`** — all validated rows (appended across jobs). Key columns: gate metrics `validation_sharpe`, `validation_cagr_pct`, `validation_pass`, and `reject_reason` (`low_cagr`, `low_validation_sharpe`, `high_equity_dd`, `risk_scaling_nonlinear`, `dd_fail`, `missing_validation_metrics`, `backtest_error`). Informational columns include `validation_recovery`, `validation_score`, equity-quality metrics, DD %, and risk-scaling fields.

**`best_survivors.csv`** — subset where `keep=true` (header-only when none pass).

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

You should see `[mt5-heartbeat] Starting optimizer heartbeat (10s poll)`.

**`TRADEECHO_USER_ID is not set`?** Confirm the variable is set to your UUID (not blank) in `.env` or `.env.local` in this folder, then retry.

### Step 3 — Start a run from the dashboard

Open `/dashboard/optimizations`, choose date range, symbols, timeframes, strategies (Classic / Multi), and optimization mode (fast genetic vs slow complete), then click **Start**. The worker launches `mt5_batch_optimize.py` and syncs results to your dashboard automatically.

### Step 4 — Monitor live results

While the worker is running, the dashboard shows batch progress, pass/fail feed, passed strategies, and parameter stats. Results are written by `mt5_db_report.py` during each job.

### Step 5 — Favorites and portfolio

1. Favorite passed strategies in the dashboard (records in `optimization_favorites`).
2. With the heartbeat worker running, favoriting or unfavoriting in the dashboard enqueues a worker command that moves matching `.set` and report files between `reports/Best/` and `reports/Favorites/`, then rebuilds the combined portfolio snapshot for **View portfolio (all)**.
3. CLI-only: after favoriting, run `python mt5_sync_favorites.py`, then:

   ```powershell
   python mt5_portfolio_favorites.py
   ```

   If a favorite has no local realticks report (for example after **Clean** removed `Best/` artifacts), the portfolio builder uses the equity curve stored in the dashboard for that strategy. Removing the last favorite clears the stored portfolio snapshot.

4. Open **View portfolio (all)** in the dashboard.

### CLI-only (no worker)

You can use all Python scripts without the dashboard worker. Run `mt5_batch_optimize.py` directly from this folder; results stay local under `reports/`. Live dashboard sync during a run still needs `TRADEECHO_USER_ID` in `.env`; remote Start/Stop from the web UI needs the heartbeat worker. Without the worker, run `python mt5_sync_favorites.py` after favoriting to copy files into `reports/Favorites/`.

## Local artifacts (gitignored)

| Path                 | Description                                             |
| -------------------- | ------------------------------------------------------- |
| `generated_configs/` | One `.ini` per optimization job                         |
| `reports/`           | Optimization XML/HTML reports (`NNN_SYMBOL_TF_Profile`) |
| `validate_staging/`  | Temporary validation backtest files                     |
| `mt5_batch_runs.csv` | Per-job status log (used by `--resume`)                 |

Use `--resume` to skip jobs whose reports already exist. Deleting `mt5_batch_runs.csv` resets resume state.

## Key CLI options

| Option                        | Default                                        | Description                                                  |
| ----------------------------- | ---------------------------------------------- | ------------------------------------------------------------ |
| `--terminal`                  | `C:\Program Files\MetaTrader 5\terminal64.exe` | Path to MT5 terminal                                         |
| `--mt5-data`                  | auto via `origin.txt`                          | MT5 data directory (or `--portable`)                         |
| `--work-dir`                  | `.`                                            | Root for generated files and logs                            |
| `--symbols` / `--timeframes`  | 28 symbols / M15 H1 H4                         | Job matrix; also filters validate-only                       |
| `--param-files`               | all under `SetFiles/`                          | Optimization parameter files (auto-discovered)               |
| `--strategies`                | all discovered                                 | Restrict to `Classic` and/or `Multi`                         |
| `--from-date` / `--to-date`   | required (except validate-only)                | `YYYY.MM.DD`                                                 |
| `--optimization`              | `2`                                            | Fast genetic; use `--complete-opt` for complete + real ticks |
| `--model`                     | `1`                                            | 1-minute OHLC by default                                     |
| `--complete-opt`              | off                                            | Shorthand: `--optimization 1` + `--model 4`                  |
| `--criterion`                 | `6`                                            | Optimization criterion                                       |
| `--forward-mode`              | `2`                                            | Forward testing mode                                         |
| `--validate-top-n-per-symbol` | `25`                                           | Top passes per symbol to backtest                            |
| `--validate-keep-top-k`       | `25`                                           | Top survivors per job after validation ranking               |
| `--min-forward-result`        | `3`                                            | Forward Result gate (≥)                                      |
| `--min-back-result`           | `6`                                            | Optimization Custom/Result gate (≥)                          |
| `--min-sharpe`                | `1.5`                                          | Sharpe gate (≥) for back, forward, and real-ticks validation |
| `--min-validation-cagr`       | `10`                                           | Real-ticks CAGR % gate (≥)                                   |
| `--target-equity-dd`          | `15.0`                                         | Linear RISK scaling target equity DD %                       |
| `--min-scaled-risk`           | `1.0`                                          | Reject when scaled RISK is below this                        |
| `--max-equity-dd`             | `17.0`                                         | Max equity DD % after scaling                                |
| `--no-risk-scaling`           | off                                            | Disable RISK scaling OHLC probe                              |
| `--verbose`                   | off                                            | Mapping, distributions, rejection diagnostics                |
| `--backtest-timeout-seconds`  | `300`                                          | Per validation backtest timeout                              |
| `--best-dir`                  | `reports/Best`                                 | Survivor output folder                                       |
| `--delay-seconds`             | `2`                                            | Pause between jobs                                           |
| `--timeout-minutes`           | `0` (none)                                     | Per-job optimization timeout                                 |
| `--resume`                    | off                                            | Skip jobs with existing reports                              |

Run `python mt5_batch_optimize.py --help` for the full list.

## Further reading

- [CONTEXT.md](CONTEXT.md) — domain glossary (survivor, permutated parameter, step usage, etc.)

## License

MIT — review the code before running on a machine with broker credentials.
