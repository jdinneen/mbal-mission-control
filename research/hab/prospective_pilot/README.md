# Domoic-acid onset — prospective pilot

A forward-in-time test of the one model the lab has that already passed its
retrospective gate: **`da_forecast_hgbt`**, the domoic-acid (the algal toxin that
closes shellfish harvests) toxic-onset warning. Everything else on the board is a
retrospective win. This harness is the machinery to turn that one candidate into
either a real deployment or an honest null.

## Why this exists

The retrospective result (`reports/hab/da_forecast.json`): on the prior-clean
toxic-onset slice, the model beats the rolling-domoic-acid baseline
`pda_roll3_value` — AP 0.144 vs 0.096, dAP +0.049, 95% CI [0.014, 0.105],
permutation p = 0.002. That is a *look-back* result. It has never been tested on
data collected *after* the model was frozen. Until it is, it is a candidate, not a
product. This pilot runs that test the only honest way: log the prediction before
the outcome exists, then wait.

**"Prior-clean"** = a station with no recent domoic-acid detections. **"Onset"** =
that station crossing into toxic at the next visit. The claim is scoped to exactly
that: catching the *first* toxic visit at a station that looked clean.

## The pre-registered bar (frozen — see `pilot_config.json`)

The pilot **passes** only if, on data collected forward in time:

- dAP > 0 (model ranks onset better than the baseline), **and**
- the 95% bootstrap CI lower bound (clustered by onset event) is **> 0**, **and**
- permutation p ≤ **0.05**, **and**
- at least **30 independent onset events** have accrued (power).

Fewer than 30 events → `COLLECTING`, and you do not read the result. dAP ≤ 0 or
p > 0.05 at power → `FAIL`, recorded as an honest null. These constants mirror the
MBAL evidence gate (n ≥ 30, α = 0.05) and are frozen *before* any forward data —
that is the whole point; it stops the goalposts from moving.

## What you must wire before this is real

This harness is deliberately model- and data-agnostic. It does **not** contain the
model, the CalHABMAP feed, or any statistics. The one file to edit is
**`adapter.py`**, which has three functions:

1. `predict(decision_utc)` — load the frozen model, pull current features for
   prior-clean stations, return model + baseline scores.
2. `resolve_outcomes(pending)` — read new lab results, label onset using the
   **same** definition as `reports/hab/da_forecast.json`, return outcomes.
3. `score(rows, config)` — hand the resolved rows to the lab's **existing** scorer
   (the one that produced `da_forecast.json`). Do **not** write new statistics
   here; a second implementation is how the colocation-MAE aggregation bug
   happened.

Also fill the three `null` fields in `pilot_config.json → onset_definition`
(prior-clean window, toxic threshold, units) from the frozen model's training
config. A wrong threshold silently relabels everything.

Until those are wired, `log`/`resolve`/`score` raise `NotImplementedError` on
purpose — the harness will not silently fake a result.

## Running it

Confirm the plumbing works (synthetic data, no real model needed):

```bash
python3 run_pilot.py selftest
```

Then, once `adapter.py` is wired, the weekly loop:

```bash
# each CalHABMAP sampling round, BEFORE the visits are read:
python3 run_pilot.py log        # append this round's predictions

# after the lab results for prior rounds come back:
python3 run_pilot.py resolve    # join outcomes to pending predictions

# anytime, to see accrual:
python3 run_pilot.py status

# once status shows >= 30 onset events:
python3 run_pilot.py score      # apply the frozen gate, write the summary
```

`--synthetic` on any command runs against the fake adapter for a dry run.

## Files

| File | What it is |
|------|-----------|
| `pilot_config.json` | Frozen pre-registration: claim, baseline, pass bar, onset definition. |
| `run_pilot.py` | The ledger CLI. Append-only, leakage-guarded, delegates scoring. No statistics of its own. |
| `adapter.py` | The one integration seam. Real functions to fill + a synthetic version for the self-test. |
| `runtime/` | Created at run time. Holds `predictions.jsonl`, `outcomes.jsonl`, `pilot_state.json`. **Git-ignored** — real station data never enters the repo. |

## How results reach the dashboard

`score` writes `runtime/pilot_state.json` with **aggregate numbers only** — n, dAP,
CI, p, verdict, dates. No per-station rows. That summary is what a future snapshot
build can surface to the site so the pilot's status shows up without exposing raw
data. (The snapshot builder is not wired to read it yet — there is nothing to show
until real forward data accrues; wire it when the first `score` produces a number.)

## Honest status

- The **plumbing** is done and tested (`selftest` passes).
- The **adapter** to the real model + feed is **not** written — it can't be from
  the public repo, which has neither. That is the work to do at home.
- The **result** cannot exist for weeks: it needs ≥ 30 forward onset events at
  CalHABMAP's roughly-weekly cadence. That wait is the actual bottleneck, and no
  amount of compute shortens it.
