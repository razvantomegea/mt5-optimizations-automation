# MT5 batch selection and validation

This document describes how [`mt5_batch_optimize.py`](mt5_batch_optimize.py) mirrors the forward-optimization workflow and how to tune or debug it.

Each `.set` file is scheduled **`DEFAULT_RUNS_PER_SET_FILE` times** (default **1**) per symbol/timeframe. Every run gets a unique report stem and is validated independently, as if it were a separate set file.

## Forward selection workflow

| Step                              | Script behavior                                                                                                                              |
| --------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| Forward 1/3, Custom max criterion | INI defaults: `ForwardMode=2`, `OptimizationCriterion=6`                                                                                     |
| Optimization engine               | Fast genetic (`--optimization 2`) on 1-minute OHLC (`--model 1`); use `--complete-opt` for slow complete + real ticks                        |
| Custom-desc scan                  | Sort by in-sample **Custom/Result** descending; stop when Custom/Result **< 6**                                                              |
| Back gates (per row in scan)      | Sharpe **≥ 1.5** (`--min-sharpe`)                                                                                                            |
| Forward gates (per row)           | Forward Sharpe **≥ 1.5** (`--min-sharpe`), forward Result **≥ 3** (required)                                                                 |
| Pick from optimization            | Rank survivors by **Custom + forward Result**; take top `--validate-top-n-per-symbol` (default 25) per symbol                                |
| Risk scaling probe (OHLC)         | Baseline RISK → linear scale toward **15%** equity DD; reject if scaled RISK **< 1**, or scaled OHLC or real-ticks DD **> 17%** (non-linear) |
| Real-ticks backtest (model 4)     | Full-period backtest at scaled or baseline RISK                                                                                              |
| Real-ticks validation gates       | Sharpe **≥ 1.5**, CAGR **≥ 10%**, equity DD **≤ 17%** on OHLC and real ticks at scaled RISK                                                  |
| Final ranking among survivors     | Composite `validation_score` on real ticks; keep top `--validate-keep-top-k` (default **10**)                                                |

Recovery, LR Correlation, Calmar, K-Ratio, stagnation, ulcer index, time under water, and margin level are **logged** in `best_summary.csv` but **not** rejection gates.

## Optimization report columns

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

## Forward data

Three sources (checked in order):

1. **Inline columns** — `Back Result` and `Forward Result` in the same `.xml`. Sharpe/Recovery on each row are treated as **forward-period** metrics; `Back Result` replaces in-sample `Custom` for forward selection.
2. **Merged files** — `report.xml` (in-sample) + `report.forward.xml` joined on `Pass`.
3. **No forward data** — warns; rows without forward metrics are rejected.

## Threshold tuning

| Flag                    | Default | Purpose                                                              |
| ----------------------- | ------- | -------------------------------------------------------------------- |
| `--min-back-result`     | `6`     | Min optimization Custom/Result (≥)                                   |
| `--min-forward-result`  | `3`     | Min forward Custom/Result (≥)                                        |
| `--min-sharpe`          | `1.5`   | Min Sharpe (≥) for back, forward, and real-ticks validation          |
| `--min-validation-cagr` | `10`    | Real-ticks CAGR % gate (≥)                                           |
| `--max-equity-dd`       | `17.0`  | Max equity DD % after scaling; OHLC or real ticks above = non-linear |
| `--target-equity-dd`    | `15.0`  | Linear RISK scaling target equity DD %                               |
| `--min-scaled-risk`     | `1.0`   | Reject when scaled RISK is below this                                |
| `--complete-opt`        | off     | Optimization=1, Model=4 (complete + every tick real ticks)           |
| `--optimization`        | `2`     | MT5 optimization mode (genetic by default)                           |
| `--model`               | `1`     | MT5 tester model (1-minute OHLC by default)                          |
| `--no-risk-scaling`     | off     | Disable RISK scaling probe                                           |
| `--validate-keep-top-k` | `10`    | Max survivors per job after validation ranking                       |

## Output CSVs

### `best_summary.csv`

Key columns include gate metrics `validation_sharpe`, `validation_cagr_pct`, `validation_pass`, and `reject_reason` (`low_cagr`, `low_validation_sharpe`, `high_equity_dd`, `risk_scaling_nonlinear`, `dd_fail`, `missing_validation_metrics`, `backtest_error`). Informational columns include `validation_recovery`, `validation_score`, equity-quality metrics, DD %, and risk-scaling fields.

### `best_survivors.csv`

Subset where `keep=true`.

## Debugging

Re-validate existing reports:

```bash
python mt5_batch_optimize.py --validate-only --validate-set-dir /path/to/SetFiles --verbose
```

Single job:

```bash
python mt5_batch_optimize.py --validate-only \
  --symbols EURUSD --timeframes M15 \
  --validate-set-dir /path/to/SetFiles \
  --param-files /path/to/SetFiles/Classic/M15/TrendCurrent.set \
  --from-date 2016.06.24 --to-date 2026.06.24 --verbose
```

**No candidates after optimization?** Check forward-selection counts in `--verbose` output (`back_sharpe`, `forward_sharpe`, `forward_result` rejections).

**No survivors after validation?** Check `reject_reason` in `best_summary.csv` for `low_cagr`, `low_validation_sharpe`, `high_equity_dd`, `dd_fail`, or risk-scaling failures.
