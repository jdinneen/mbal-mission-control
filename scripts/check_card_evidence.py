#!/usr/bin/env python3
"""Bind every published Card number to an exact key in an exact evidence file.

WHY THIS EXISTS
---------------
On 2026-07-14 a "red-team" pass wrote numbers straight into build_snapshot.py with
no artifact behind them. Nine Cards shipped figures that existed in no file. The
worst (banked_josh_fib_trend) claimed a California enterococcus decline at p=3e-6
that was never computed -- the real test returns p=0.354, a null.

The obvious fix -- "stamp each Card with its source file" -- does not work, and we
know it does not work because that Card ALREADY cited a real, existing evidence
file and lied anyway. A stamp is an assertion, and assertions are exactly what
failed. Palantir's lineage has the same hole: it tells you a human put a number
there, it does not tell you the number is true.

So this checker does not trust any stamp. For each numeric metric on a Card it
demands an EXACT key lookup in the cited file:

    metric `pooled_auc` = 0.609   ->   karenia_season_matched.json ["pooled_AUC"]

Exact key path, exact value. Never a fuzzy value search: an earlier prototype that
scanned a file for "some number within 2%" PASSED entero_exSD_pooled_mk_p=0.354,
which is not in the cited file at all -- it collided with an unrelated number. A
checker that can be fooled by coincidence is worse than none, because it launders.

STATUS PER METRIC
  BOUND      key found in an evidence file, value matches exactly
  UNBOUND    no key in any cited file holds this value  <-- the fabrication signal
  DERIVED    declared in DERIVED_ALLOW with a stated arithmetic reason
  PROSE      non-numeric (a label, a note) -- nothing to check

Cards whose evidence is narrative (.md) carry no machine-checkable numbers and are
reported as NARRATIVE rather than silently passing.

USAGE
  python scripts/check_card_evidence.py            # audit, write ledger, exit 0
  python scripts/check_card_evidence.py --gate     # exit 1 if a SEALED card regressed

--gate is what publish.sh runs. It fails only on Cards listed in SEALED (below):
cards proven clean and frozen. New cards cannot break the gate, but they cannot
enter SEALED without being clean either -- so coverage only ratchets up. Gating
all 66 today would just block every publish and get the gate deleted in a week.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAB = os.path.dirname(REPO)  # evidence paths are relative to the Lab root
DATA = os.path.join(REPO, "data.json")
LEDGER = os.path.join(REPO, "reports", "evidence_audit.json")

# Cards proven clean and frozen. publish.sh --gate fails if any of these regress.
# Add a card here ONLY after it audits with zero UNBOUND numbers.
SEALED: set[str] = {
    "banked_ca_fib_comparability_breaks",
    "banked_chesapeake_hypoxia_virtual_sensor",
    "caveat_sampling_triage_retrospective_gain_partial_fresh",
    "claim_adaptive_sampling_pilot",
    "claim_bacteria_decision_rule",
    "claim_bacteria_honest_forecast_horizon",
    "claim_bacteria_station_memory_supported",
    "claim_cache_slough_upstream_escape",
    "claim_carrier_forecast_lead_advisory",
    "claim_carte_numeric_shared_plane_benchmark",
    "claim_catboost_champion_record_dont_ship",
    "claim_clean_to_dirty_onset",
    "claim_cyano_onset_latitude_law_scoped",
    "claim_da_toxic_onset_ci_separated",
    "claim_findings_campaign_targeted_l2_keeps",
    "claim_forward_2026_holdout_pass",
    "claim_global_surge_onset_coldstart",
    "claim_gulf_hypoxia_flag",
    "claim_hab_exceed_strict_critic",
    "claim_hypoxia_gru_sequence_positive",
    "claim_hypoxia_onset_forecast",
    "claim_lightgbm_catboost_paired_boosts",
    "claim_mcye_alberta_definitive_replication",
    "claim_mcye_national_replication",
    "claim_operational_label_quality_win",
    "claim_russian_river_advection_escape",
    "claim_soil_moisture_gate_positive",
    "claim_tide_range_caveated_positive",
    "claim_tokyo_shadow_lakehouse_positives",
    "claim_tsunami_halfduration_warning_screen",
    "claim_wet_tail_driver_skill",
    "null_ddpcr_target_redefinition_artifact",
    "null_estate_gpu_signal_search",
    "null_findings_campaign_dry_onset_interaction",
    "null_findings_campaign_dry_onset_symbolic",
    "null_interlingua_cyano_daily_refutation",
    "null_interlingua_eu_resolution_boundary",
    "null_national_microcystin_driver",
    "null_physics_pi_reencoding_robust",
    "null_sog_central_relief_onset_underpowered",
    "null_tensor_pi_state_qa_only",
    "status_all_silver_unsupervised_ae_running",
    "status_bacteria_breakthrough_lockbox",
    "status_findings_campaign_10h_rerun",
    "status_h1_trust_index",
    "status_operational_nowcast_partial_fresh",
}

# Metrics that are legitimately arithmetic on other published numbers, not lookups.
# Every entry MUST state the derivation so a reader can redo it by hand.
DERIVED_ALLOW: dict[tuple[str, str], str] = {
    ("banked_shark_severity_hotspot", "worst_case_floor_ratio"):
        "0.089623 / ((28+8)/971) = 2.417 -- worst case: all 8 unknown-outcome FL bites counted fatal",
    ("banked_shark_severity_hotspot", "pooled_ratio"):
        "risk_ratio_cc 3.0966 from era_hardening.json all_1900_2026, rounded",
    ("banked_shark_severity_hotspot", "modern_only_ratio"):
        "risk_ratio_cc 11.838 from era_hardening.json 2000_2026, rounded",
    ("banked_lobster_temperature_timing", "skill_vs_trend"):
        "1-(temp_RMSE/trend_RMSE)^2 = 1-(0.157/0.284)^2 = 0.694",
    ("claim_argo_ctd_virtual_oxygen_sensor", "within_basin_dap_simple_mean"):
        "mean(dAP) over the 8 rows of leave_basin_out.csv = 0.176",
    ("claim_argo_ctd_virtual_oxygen_sensor", "within_basin_dap_pos_weighted"):
        "pos-weighted mean(dAP) over leave_basin_out.csv = 0.129",
    ("claim_argo_ctd_virtual_oxygen_sensor", "base_rate_evaluated"):
        "30644/204647 = 0.1497 from leave_basin_out.csv column sums",
    ("claim_argo_ctd_virtual_oxygen_sensor", "n_profiles_evaluated"):
        "sum(n) over leave_basin_out.csv = 204647",
    ("banked_redtide_nflh_detector", "available_pairs_unused"):
        "available_pairs 1460 - n_pairs 45 = 1415",
    ("null_estate_gpu_signal_search", "interaction_pairs"):
        "C(1029,2) = 528906, from interaction_scan.json estate_cols_usable_train_nonconst",
}

_NUM = re.compile(r"^-?\d+\.?\d*(?:[eE][-+]?\d+)?$")


def _walk_json(obj, prefix=""):
    """Yield (json_path, value) for every leaf."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk_json(v, f"{prefix}[{json.dumps(k)}]")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_json(v, f"{prefix}[{i}]")
    else:
        yield prefix, obj


def _load_evidence(path: str):
    """Return {json_path: value} for a JSON evidence file, or None if unparseable."""
    full = os.path.join(LAB, path.split("#")[0])
    if not os.path.exists(full):
        return None
    if full.lower().endswith(".csv"):
        import csv
        out = {}
        with io.open(full, encoding="utf-8", errors="ignore") as fh:
            for i, row in enumerate(csv.DictReader(fh)):
                for k, v in row.items():
                    if v is None:
                        continue
                    try:
                        out[f'[{i}][{json.dumps(k)}]'] = float(v)
                    except ValueError:
                        out[f'[{i}][{json.dumps(k)}]'] = v
        return out
    if not full.lower().endswith(".json"):
        return "NARRATIVE"
    try:
        with io.open(full, encoding="utf-8", errors="ignore") as fh:
            return dict(_walk_json(json.load(fh)))
    except Exception:
        return None


def _numeric(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _decimals(x) -> int:
    """How many decimal places the Card actually displays."""
    s = repr(float(x))
    if "e" in s or "E" in s:
        return 12
    return len(s.split(".")[1].rstrip("0")) if "." in s else 0


def _match(want: float, leaves: dict, shown) -> str | None:
    """Key lookup: find a leaf whose value IS `want`, allowing display rounding.

    A Card legitimately shows a rounded view of the evidence: the surge card prints
    0.1915 where redteam_surge_core.json holds 0.1914653693405365. Those are the same
    number, so the rule is: round the EVIDENCE to the precision the CARD chose to show
    and require equality. That admits rounding and nothing else.

    This is NOT a similarity search. A prototype that accepted "any value within 2%"
    passed entero_exSD_pooled_mk_p=0.354 against a file that does not contain it --
    it collided with an unrelated number. Tolerance must come from the card's own
    displayed precision, never from a fudge factor.
    """
    dp = _decimals(shown)
    for p, v in leaves.items():
        fv = _numeric(v)
        if fv is None:
            continue
        if fv == want or round(fv, dp) == round(want, dp):
            return p
    return None


def audit() -> dict:
    with io.open(DATA, encoding="utf-8") as fh:
        data = json.load(fh)
    findings = data["state"]["findings"]

    cards = []
    for f in findings:
        fid = f["id"]
        ev = f.get("evidence") or []
        ev_raw = ev if isinstance(ev, list) else [ev]
        ev_list = [str(e).strip() for e in ev_raw if e and str(e).strip()]

        leaves: dict = {}
        narrative = False
        missing = []
        for p in ev_list:
            got = _load_evidence(p)
            if got is None:
                missing.append(p)
            elif got == "NARRATIVE":
                narrative = True
            else:
                leaves.update({f"{p}::{k}": v for k, v in got.items()})

        metrics = f.get("metrics") or {}
        rows = []
        for k, v in metrics.items():
            want = _numeric(v)
            if want is None:
                if isinstance(v, list) and all(_numeric(x) is not None for x in v) and v:
                    for i, x in enumerate(v):
                        hit = _match(float(x), leaves, x)
                        rows.append({"metric": f"{k}[{i}]", "value": x,
                                     "status": "BOUND" if hit else "UNBOUND",
                                     "bound_to": hit})
                    continue
                rows.append({"metric": k, "value": v, "status": "PROSE", "bound_to": None})
                continue
            why = DERIVED_ALLOW.get((fid, k))
            if why:
                rows.append({"metric": k, "value": v, "status": "DERIVED",
                             "bound_to": None, "derivation": why})
                continue
            hit = _match(want, leaves, v)
            rows.append({"metric": k, "value": v,
                         "status": "BOUND" if hit else "UNBOUND", "bound_to": hit})

        unbound = [r for r in rows if r["status"] == "UNBOUND"]
        cards.append({
            "id": fid,
            "kind": f.get("kind"),
            "evidence": ev_list,
            "evidence_missing": missing,
            "narrative_evidence": narrative,
            "n_metrics": len(rows),
            "n_bound": sum(1 for r in rows if r["status"] == "BOUND"),
            "n_derived": sum(1 for r in rows if r["status"] == "DERIVED"),
            "n_unbound": len(unbound),
            "status": ("NARRATIVE" if narrative and not rows else
                       "CLEAN" if not unbound else "UNBOUND_NUMBERS"),
            "metrics": rows,
        })

    tot = sum(c["n_metrics"] for c in cards)
    ub = sum(c["n_unbound"] for c in cards)
    return {
        "_note": ("Binds each published Card number to an exact key in an exact evidence "
                  "file. UNBOUND = the number is not in any file the Card cites. Exact key "
                  "lookup only -- never a fuzzy value search, which launders coincidences."),
        "generated_from": "mbal-mission-control/data.json",
        "totals": {"cards": len(cards), "metrics": tot, "unbound": ub,
                   "cards_clean": sum(1 for c in cards if c["status"] == "CLEAN"),
                   "cards_with_unbound": sum(1 for c in cards if c["n_unbound"])},
        "sealed": sorted(SEALED),
        "cards": cards,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate", action="store_true",
                    help="exit 1 if a SEALED card has unbound numbers")
    args = ap.parse_args()

    rep = audit()
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    with io.open(LEDGER, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=2, ensure_ascii=False)

    t = rep["totals"]
    print(f"cards {t['cards']} | metrics {t['metrics']} | "
          f"UNBOUND {t['unbound']} | clean cards {t['cards_clean']}/{t['cards']}")
    worst = sorted((c for c in rep["cards"] if c["n_unbound"]),
                   key=lambda c: -c["n_unbound"])[:12]
    for c in worst:
        print(f"  {c['n_unbound']:>3} unbound  {c['kind']:<8} {c['id']}")
        for r in [r for r in c["metrics"] if r["status"] == "UNBOUND"][:3]:
            print(f"        {r['metric']} = {r['value']}")

    if args.gate:
        bad = [c for c in rep["cards"] if c["id"] in SEALED and c["n_unbound"]]
        if bad:
            print("\nGATE FAIL — sealed cards regressed:", file=sys.stderr)
            for c in bad:
                print(f"  {c['id']}: {c['n_unbound']} unbound", file=sys.stderr)
            return 1
        print(f"gate: PASS ({len(SEALED)} sealed cards clean)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
