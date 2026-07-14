# EA Optimization Glossary

Canonical vocabulary for the MT5 batch-optimization + step-usage analysis domain. Glossary only — no implementation details.

## Terms

### Survivor

A parameter set that passed full validation and was kept in the final top-K ranking (`keep=true` in `best_survivors.csv`). Not merely a `validation_pass=true` row, and not any optimization pass. The step-usage report counts only Survivors.

### Base set

A `.set` file under your `MT5_SET_DIR` (or `--validate-set-dir`) defines the optimization **grid**. Each optimizable line has the form `VALUE||START||STEP||STOP||ENABLED`.

Nested layout example:

```
SetFiles/
  Classic/M15/TrendCurrent.set
  Multi/H1/HTFH4.set
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
