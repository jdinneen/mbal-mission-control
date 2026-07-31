#!/usr/bin/env python3
"""Prospective-pilot ledger for the da_forecast_hgbt domoic-acid onset model.

Purpose: make a forward-time test leakage-proof. Predictions are logged BEFORE
outcomes exist; outcomes are joined later; scoring is delegated to the lab's
existing scorer via adapter.score(). This file deliberately contains no
statistics of its own.

Commands:
    log       Record today's predictions for prior-clean stations (append-only).
    resolve   Join newly-sampled lab outcomes to pending predictions (append-only).
    status    Show accrual: predictions logged, outcomes resolved, onset events.
    score     Hand resolved rows to the lab scorer, apply the pre-registered gate,
              write runtime/pilot_state.json (aggregate only).
    selftest  Run the whole loop on a synthetic adapter to prove the plumbing.

The pre-registered claim, baseline, and pass bar live in pilot_config.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "pilot_config.json"


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _paths(config: dict) -> dict:
    rt = config.get("runtime_files", {})
    def p(key, default):
        return HERE / rt.get(key, default)
    d = {
        "predictions": p("predictions_ledger", "runtime/predictions.jsonl"),
        "outcomes": p("outcomes_ledger", "runtime/outcomes.jsonl"),
        "summary": p("summary_out", "runtime/pilot_state.json"),
    }
    d["predictions"].parent.mkdir(parents=True, exist_ok=True)
    return d


def _append_jsonl(path: Path, rows: list[dict]) -> int:
    with path.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, separators=(",", ":"), sort_keys=True) + "\n")
    return len(rows)


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def _key(row: dict) -> tuple:
    return (row["station_id"], row["target_visit_date"])


def _require_prediction_fields(rows: list[dict]) -> None:
    need = {"station_id", "target_visit_date", "model_score", "baseline_score", "prior_clean"}
    for r in rows:
        missing = need - set(r)
        if missing:
            raise ValueError(f"prediction row missing {sorted(missing)}: {r}")
        if not r["prior_clean"]:
            raise ValueError(f"non prior-clean station returned by adapter: {r['station_id']}")


def _require_outcome_fields(rows: list[dict]) -> None:
    need = {"station_id", "target_visit_date", "outcome_utc", "da_value", "is_onset", "event_id"}
    for r in rows:
        missing = need - set(r)
        if missing:
            raise ValueError(f"outcome row missing {sorted(missing)}: {r}")


# --------------------------------------------------------------------------- #
# commands                                                                     #
# --------------------------------------------------------------------------- #

def cmd_log(adapter, config: dict) -> None:
    paths = _paths(config)
    decision_utc = _now_utc()
    preds = adapter.predict(decision_utc)
    _require_prediction_fields(preds)
    stamped = []
    for i, r in enumerate(preds):
        stamped.append({
            "prediction_id": f"{decision_utc}#{i}",
            "decision_utc": decision_utc,
            "station_id": r["station_id"],
            "target_visit_date": r["target_visit_date"],
            "model_score": r["model_score"],
            "baseline_score": r["baseline_score"],
            "prior_clean": bool(r["prior_clean"]),
        })
    n = _append_jsonl(paths["predictions"], stamped)
    print(f"logged {n} predictions at {decision_utc}")


def cmd_resolve(adapter, config: dict) -> None:
    paths = _paths(config)
    preds = _read_jsonl(paths["predictions"])
    resolved_keys = {_key(o) for o in _read_jsonl(paths["outcomes"])}
    pending = [p for p in preds if _key(p) not in resolved_keys]
    if not pending:
        print("no pending predictions to resolve")
        return
    outcomes = adapter.resolve_outcomes(pending)
    _require_outcome_fields(outcomes)
    # leakage guard: an outcome must not predate the decision it resolves
    pred_by_key = {}
    for p in preds:
        pred_by_key.setdefault(_key(p), p)
    clean = []
    for o in outcomes:
        p = pred_by_key.get(_key(o))
        if p is None:
            print(f"  skip outcome with no matching prediction: {_key(o)}", file=sys.stderr)
            continue
        if o["outcome_utc"] <= p["decision_utc"]:
            raise ValueError(
                f"LEAKAGE: outcome_utc {o['outcome_utc']} <= decision_utc {p['decision_utc']} "
                f"for {_key(o)} — an outcome cannot precede its prediction"
            )
        clean.append(o)
    n = _append_jsonl(paths["outcomes"], clean)
    print(f"resolved {n} outcomes ({len(pending) - n} still pending)")


def _join(config: dict) -> list[dict]:
    paths = _paths(config)
    preds = {_key(p): p for p in _read_jsonl(paths["predictions"])}
    rows = []
    for o in _read_jsonl(paths["outcomes"]):
        p = preds.get(_key(o))
        if p is None:
            continue
        rows.append({
            "station_id": o["station_id"],
            "target_visit_date": o["target_visit_date"],
            "model_score": p["model_score"],
            "baseline_score": p["baseline_score"],
            "is_onset": o["is_onset"],
            "event_id": o["event_id"],
            "decision_utc": p["decision_utc"],
            "outcome_utc": o["outcome_utc"],
        })
    return rows


def cmd_status(config: dict) -> None:
    paths = _paths(config)
    preds = _read_jsonl(paths["predictions"])
    joined = _join(config)
    events = {r["event_id"] for r in joined if r["is_onset"] == 1}
    need = config["prospective_success_gate"]["min_independent_onset_events"]
    print(f"pilot        : {config['pilot_id']}")
    print(f"predictions  : {len(preds)} logged")
    print(f"resolved     : {len(joined)} joined to outcomes")
    print(f"onset events : {len(events)} / {need} needed for power")
    print(f"first logged : {preds[0]['decision_utc'] if preds else '-'}")
    print(f"status       : {'COLLECTING' if len(events) < need else 'READY TO SCORE'}")


def _apply_gate(config: dict, scored: dict) -> dict:
    gate = config["prospective_success_gate"]
    n = scored.get("n_events") or 0
    d_ap = scored.get("d_ap")
    ci = scored.get("d_ap_ci95")
    p = scored.get("perm_p")
    powered = n >= gate["min_independent_onset_events"]
    if not powered or d_ap is None or ci is None or p is None:
        return {"status": "COLLECTING",
                "verdict": f"underpowered or unscored (n={n}); keep collecting"}
    ci_lo = ci[0]
    if d_ap > 0 and ci_lo > gate["d_ap_ci95_lower_must_exceed"] and p <= gate["perm_p_max"]:
        return {"status": "PASS", "verdict": "prospectively confirmed; eligible to promote"}
    if d_ap <= 0 or p > gate["perm_p_max"]:
        return {"status": "FAIL", "verdict": "retrospective win did not replicate forward"}
    return {"status": "INCONCLUSIVE", "verdict": "signal present but CI touches 0; collect more"}


def cmd_score(adapter, config: dict) -> None:
    paths = _paths(config)
    rows = _join(config)
    if not rows:
        print("nothing resolved yet; run `resolve` after outcomes arrive")
        return
    scored = adapter.score(rows, config)
    gate = _apply_gate(config, scored)
    preds = _read_jsonl(paths["predictions"])
    summary = {
        "pilot_id": config["pilot_id"],
        "model_id": config["model_id"],
        "claim": config["claim_under_test"],
        "baseline": config["retrospective_result_being_replicated"]["baseline_id"],
        "pre_registered": {
            "d_ap_ci95_lower_must_exceed": config["prospective_success_gate"]["d_ap_ci95_lower_must_exceed"],
            "perm_p_max": config["prospective_success_gate"]["perm_p_max"],
            "min_independent_onset_events": config["prospective_success_gate"]["min_independent_onset_events"],
        },
        "n_predictions_logged": len(preds),
        "n_events_resolved": scored.get("n_events"),
        "d_ap": scored.get("d_ap"),
        "d_ap_ci": scored.get("d_ap_ci95"),
        "perm_p": scored.get("perm_p"),
        "powered": (scored.get("n_events") or 0) >= config["prospective_success_gate"]["min_independent_onset_events"],
        "status": gate["status"],
        "verdict": gate["verdict"],
        "first_prediction_utc": preds[0]["decision_utc"] if preds else None,
        "last_scored_utc": _now_utc(),
        "synthetic": bool(scored.get("_synthetic")),
    }
    paths["summary"].write_text(json.dumps(summary, indent=2), encoding="utf-8")
    try:
        shown = paths["summary"].relative_to(HERE)
    except ValueError:
        shown = paths["summary"]
    print(f"[{gate['status']}] {gate['verdict']}")
    print(f"wrote {shown} (aggregate only, no per-station rows)")


def cmd_selftest(config: dict) -> int:
    """Run log -> resolve -> status -> score on a synthetic adapter.

    Proves the plumbing: append-only ledgers, leakage-safe join, accrual counts,
    gate wiring. Uses a throwaway runtime dir so it never touches real ledgers.
    """
    from adapter import get_adapter
    import tempfile, shutil

    tmp = Path(tempfile.mkdtemp(prefix="pilot_selftest_"))
    try:
        cfg = json.loads(json.dumps(config))  # deep copy
        cfg["runtime_files"] = {
            "predictions_ledger": str(tmp / "predictions.jsonl"),
            "outcomes_ledger": str(tmp / "outcomes.jsonl"),
            "summary_out": str(tmp / "pilot_state.json"),
        }
        # re-point _paths at absolute tmp files
        global _paths
        orig_paths = _paths
        def _tmp_paths(_config):
            d = {"predictions": tmp / "predictions.jsonl",
                 "outcomes": tmp / "outcomes.jsonl",
                 "summary": tmp / "pilot_state.json"}
            return d
        _paths = _tmp_paths
        adapter = get_adapter(synthetic=True)

        cmd_log(adapter, cfg)
        cmd_resolve(adapter, cfg)
        cmd_status(cfg)
        cmd_score(adapter, cfg)

        preds = _read_jsonl(tmp / "predictions.jsonl")
        outs = _read_jsonl(tmp / "outcomes.jsonl")
        assert preds, "selftest: no predictions written"
        assert outs, "selftest: no outcomes written"
        # leakage invariant: every outcome strictly after its decision
        by_key = {(p["station_id"], p["target_visit_date"]): p for p in preds}
        for o in outs:
            p = by_key[(o["station_id"], o["target_visit_date"])]
            assert o["outcome_utc"] > p["decision_utc"], "selftest: leakage invariant violated"
        # resolve is idempotent: a second pass adds nothing
        before = len(outs)
        cmd_resolve(adapter, cfg)
        after = len(_read_jsonl(tmp / "outcomes.jsonl"))
        assert after == before, "selftest: resolve not idempotent"
        _paths = orig_paths
        print("\nSELFTEST PASS — plumbing works (this proves logging/join, NOT the science)")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"\nSELFTEST FAIL — {exc}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["log", "resolve", "status", "score", "selftest"])
    parser.add_argument("--synthetic", action="store_true",
                        help="use the synthetic adapter (for dry runs; real runs omit this)")
    args = parser.parse_args(argv)
    config = _load_config()

    if args.command == "selftest":
        return cmd_selftest(config)

    from adapter import get_adapter
    adapter = get_adapter(synthetic=args.synthetic)

    if args.command == "log":
        cmd_log(adapter, config)
    elif args.command == "resolve":
        cmd_resolve(adapter, config)
    elif args.command == "status":
        cmd_status(config)
    elif args.command == "score":
        cmd_score(adapter, config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
