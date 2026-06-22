#!/usr/bin/env python3
"""
Build the static data snapshot for the public site.

Pulls the REAL state from the running Mission Control cockpit (http://127.0.0.1:8770)
and bundles the evidence files findings/models reference, so the published site shows
the same data + drill-downs offline. Re-run this whenever you want to refresh the
published snapshot, then commit + push data.json.

Usage:  python build_snapshot.py
"""
import json
import math
import os
import re
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

COCKPIT = "http://127.0.0.1:8770"
HERE = Path(__file__).resolve().parent
SOURCE_ROOT = Path(os.environ.get("MBAL_ROOT", HERE.parent)).resolve()

try:
    import pyarrow.parquet as _pq
except Exception:
    _pq = None

# Cap parquet footers read per dataset so a pathological part-count can't stall the build.
_ROWS_FILE_CAP = 5000


def _count_rows(path_str):
    """Sum parquet row counts for a dataset dir by reading footers only (no data).
    Returns (rows, estimated) or (None, False) if not countable. Read-only + defensive:
    any failure yields None so the build never breaks on a bad file."""
    if not _pq or not path_str:
        return None, False
    try:
        p = Path(path_str)
        if not p.exists():
            return None, False
        if p.is_file():
            files = [p] if p.suffix == ".parquet" else []
        else:
            files = sorted(p.rglob("*.parquet"))
        if not files:
            return None, False
        estimated = len(files) > _ROWS_FILE_CAP
        scan = files[:_ROWS_FILE_CAP]
        total = 0
        for f in scan:
            try:
                total += _pq.ParquetFile(f).metadata.num_rows
            except Exception:
                continue
        if estimated and scan:  # extrapolate from the sampled footers
            total = int(round(total * len(files) / len(scan)))
        return total, estimated
    except Exception:
        return None, False


def _get(path):
    with urllib.request.urlopen(COCKPIT + path, timeout=30) as r:
        return json.loads(r.read().decode())


def _public(state):
    """Keep the science in full; drop local-machine ops + scrub absolute paths so the
    public site can't leak the user's filesystem / username / process list."""
    keep = ("findings", "models", "gates", "data_assets", "next_actions",
            "overall_status", "snapshot_generated", "counts")
    out = {k: state.get(k) for k in keep if k in state}
    # lakehouse inventory: keep dataset names/sizes/sources, drop absolute paths
    inv = state.get("inventory", {}) or {}
    def clean_store(s):
        if not s:
            return s
        ds = []
        for d in s.get("datasets", []):
            d = dict(d)
            if d.get("rows") is None:  # enrich with footer-counted parquet rows before scrubbing path
                rows, est = _count_rows(d.get("path"))
                if rows is not None:
                    d["rows"] = rows
                    if est:
                        d["rows_estimated"] = True
            d.pop("path", None)
            d.pop("store_root", None)
            ds.append(d)
        s = dict(s)
        s["datasets"] = ds
        return s
    out["inventory"] = {"lake": clean_store(inv.get("lake")),
                        "shadow": clean_store(inv.get("shadow")),
                        "built_iso": inv.get("built_iso")}
    # git: branch + last commit message only (drop sample paths / surface lists)
    g = state.get("git", {}) or {}
    out["git"] = {k: g.get(k) for k in ("branch", "risk", "changed_path_count",
                                        "review_surface_count", "last_commit")}
    # findings/models: drop nothing of the science, but blank absolute evidence paths -> keep relative only
    return out


def _local_json(rel):
    path = SOURCE_ROOT / rel
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _local_text(rel, max_chars=12000):
    path = SOURCE_ROOT / rel
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:max_chars]
    except Exception:
        return None


def _r(v, n=4):
    if isinstance(v, float):
        if not math.isfinite(v):
            return None
        return round(v, n)
    return v


def _json_safe(obj):
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    return obj


def _short_feature(name):
    return str(name).split("::")[-1] if name is not None else None


def _finding(fid, title, kind, headline, plain, how, evidence, metrics=None, note=None):
    return {
        "id": fid,
        "title": title,
        "kind": kind,
        "headline": headline,
        "plain": plain,
        "how": how,
        "evidence": evidence,
        "metrics": metrics or {},
        "note": note,
    }


def _claim_card_findings():
    data = _local_json("reports/_eval_cards/claim_cards.json") or {}
    out = []
    bac = data.get("bacteria_headline_vs_station_memory") or {}
    if bac:
        model = bac.get("model", {})
        base = bac.get("baseline", {})
        margin = bac.get("margin", {})
        model_ap = model.get("ap") or bac.get("candidate_ap")
        base_ap = base.get("ap") or bac.get("baseline_ap")
        model_ece = model.get("ece") or bac.get("candidate_ece")
        out.append(_finding(
            "claim_bacteria_station_memory_supported",
            "Bacteria nowcast beats station-memory status quo",
            "claim",
            f"AP {_r(model_ap)} vs {_r(base_ap)} · dAP +{_r(margin.get('margin_ap'))}",
            "On the EXCLUDE_SAN_DIEGO 2022+ deployable stratum, the calibrated bacteria model beats the station-memory baseline with a clustered-bootstrap AP margin whose CI excludes zero.",
            "Frozen temporal holdout, n=89,321 / 8,999 events, compared against a station-memory baseline and checked by decision-curve net benefit.",
            "reports/_eval_cards/claim_cards.json",
            {
                "n": bac.get("n"),
                "events": bac.get("events"),
                "model_ap": _r(model_ap),
                "station_memory_ap": _r(base_ap),
                "margin_ap": _r(margin.get("margin_ap")),
                "ci95": margin.get("ci95"),
                "roc_auc": _r(model.get("roc_auc")),
                "ece": _r(model_ece),
            },
            "This is the stronger public bacteria headline than the stale pooled/Monterey-only table row.",
        ))
    tide = data.get("tidal_escape_vs_rain_discharge") or {}
    if tide:
        model = tide.get("model", {})
        base = tide.get("baseline", {})
        margin = tide.get("margin", {})
        if margin.get("margin_ap") is not None:
            out.append(_finding(
                "claim_tide_range_caveated_positive",
                "Harmonic tide range adds a small pooled bacteria lift",
                "caveat",
                f"dAP +{_r(margin.get('margin_ap'))} over rain+discharge",
                "The spring-neap/tide-table feature has a real pooled temporal lift, but the broader regional-transfer story was later softened by the expanded holdout audit.",
                "Claim-card margin test plus later H1 trust-index caveat: pooled lift holds; robust across-region generalization does not yet clear the bar.",
                "reports/_eval_cards/claim_cards.json",
                {
                    "model_ap": _r(model.get("ap")),
                    "baseline_ap": _r(base.get("ap")),
                    "margin_ap": _r(margin.get("margin_ap")),
                    "ci95": margin.get("ci95"),
                },
                "Keep as a caveated positive, not as a broad deployed feature claim.",
            ))
        else:
            out.append(_finding(
                "status_tide_range_data_gap",
                "Harmonic tide-range claim card is blocked by source-data gaps",
                "status",
                str(tide.get("verdict") or "DATA-GAP"),
                "The current tide-range card is not promoted as a positive claim because the harmonic-range cache and beach-to-gauge map are not materialized.",
                "Claim-card readout records the missing inputs instead of reporting a lift.",
                "reports/_eval_cards/claim_cards.json",
                {"reasons": tide.get("reasons") or []},
                "Rebuild the harmonic range features before re-testing this candidate.",
            ))
    return out


def _science_breakthrough_findings():
    cards = _local_json("reports/science_breakthrough/science_claim_cards.json") or {}
    gate = _local_json("reports/bacteria/breakthrough_gate/breakthrough_gate.json") or {}
    out = []
    onset = cards.get("onset_claim") or {}
    if onset:
        m = onset.get("surprise_slice", {})
        out.append(_finding(
            "claim_clean_to_dirty_onset",
            "Clean-to-dirty beach surprise reframe passes retrospective gate",
            "claim",
            f"AP {_r(m.get('model_ap'))} vs station-memory {_r(m.get('station_memory_ap'))}",
            "Among currently-clean beach-days, the frozen statewide model predicts clean-to-dirty exceedance surprises far better than station memory.",
            "Same frozen bacteria model, EXCLUDE_SAN_DIEGO, reveal-lag discipline, evaluated on the onset slice instead of the already-dirty persistence slice.",
            "reports/science_breakthrough/science_claim_cards.json",
            {
                "n": m.get("n"),
                "events": m.get("events"),
                "base_rate": _r(m.get("base_rate")),
                "model_ap": _r(m.get("model_ap")),
                "station_memory_ap": _r(m.get("station_memory_ap")),
                "delta_ap": _r(m.get("model_minus_station_memory")),
                "ratio_vs_station_memory": _r(onset.get("onset_ratio_vs_station_memory")),
            },
            "Final breakthrough claim remains blocked until the post-2026-06-18 prospective lockbox has rows and is scored.",
        ))
    sampling = cards.get("sampling_pilot") or {}
    if sampling:
        top20 = sampling.get("top_20pct", {})
        replay = sampling.get("full_protocol_replay", {})
        lift = (replay.get("lift_vs_station_memory") or {})
        out.append(_finding(
            "claim_adaptive_sampling_pilot",
            "Adaptive sampling replay beats site-memory sampling",
            "positive",
            f"top-20 capture {_r(top20.get('model_capture'))} vs {_r(top20.get('station_memory_capture'))}",
            "Under the same sampling budget, model-ranked sampling catches more exceedances than station-memory ranking in retrospective replay.",
            "Ranking replay and full mixed-arm weekly protocol replay on frozen bacteria scores.",
            "reports/science_breakthrough/science_claim_cards.json",
            {
                "top20_model_capture": _r(top20.get("model_capture")),
                "top20_station_memory_capture": _r(top20.get("station_memory_capture")),
                "top20_delta": _r(top20.get("model_minus_station_memory")),
                "full_protocol_capture_delta": _r(lift.get("capture_delta")),
                "events_delta": lift.get("events_delta"),
                "ci95": (lift.get("week_bootstrap") or {}).get("ci95"),
            },
            "Ops-validation ready; not yet a standalone prospective discovery claim.",
        ))
    status = gate.get("final_breakthrough_status")
    if status:
        out.append(_finding(
            "status_bacteria_breakthrough_lockbox",
            "Bacteria breakthrough gate is retrospective-pass, prospective-blocked",
            "status",
            f"{gate.get('retrospective_candidate_status')} · {status}",
            "The operational, onset, and spatial checks pass retrospectively, but the final breakthrough label is blocked by an empty prospective lockbox.",
            "Breakthrough gate reads FROZEN_HEADLINE plus onset and spatial holdout metrics, then enforces the post-freeze lockbox rule.",
            "reports/bacteria/breakthrough_gate/breakthrough_gate.json",
            {
                "freeze_cutoff": (gate.get("lockbox") or {}).get("freeze_cutoff"),
                "scored_forward": (gate.get("lockbox") or {}).get("scored_forward"),
                "operational_ap": _r(((gate.get("metrics") or {}).get("operational") or {}).get("ap")),
                "onset_delta_vs_memory": _r(((gate.get("metrics") or {}).get("onset_exc_prev_0") or {}).get("model_minus_station_memory")),
            },
            gate.get("final_breakthrough_reason"),
        ))
    return out


def _model_lab_findings():
    out = []
    champ = _local_json("reports/model_lab/champion_claimcard_B.json") or {}
    card = champ.get("claim_card") or {}
    if card:
        model = card.get("model", {})
        base = card.get("baseline", {})
        margin = card.get("margin", {})
        out.append(_finding(
            "claim_catboost_champion_record_dont_ship",
            "CatBoost has a real ranking edge over frozen HGBT",
            "positive",
            f"AP {_r(model.get('ap'))} vs {_r(base.get('ap'))} · CI {margin.get('ci95')}",
            "The CatBoost champion-upgrade candidate beats the frozen HGBT ranking metric with a clustered-bootstrap margin that excludes zero.",
            "Same EXCLUDE_SAN_DIEGO temporal holdout; decision-curve analysis then checks whether the ranking edge changes operations.",
            "reports/model_lab/champion_claimcard_B.json",
            {
                "n": card.get("n"),
                "events": card.get("events"),
                "catboost_ap": _r(model.get("ap")),
                "hgbt_ap": _r(base.get("ap")),
                "margin_ap": _r(margin.get("margin_ap")),
                "ci95": margin.get("ci95"),
                "catboost_ece": _r(model.get("ece")),
                "hgbt_ece": _r(base.get("ece")),
            },
            champ.get("decision"),
        ))
    paired = _local_json("reports/model_lab/paired_model_test.json") or {}
    if paired:
        cb = (paired.get("paired_vs_hgbt") or {}).get("catboost", {})
        lg = (paired.get("paired_vs_hgbt") or {}).get("lightgbm", {})
        loco = paired.get("loco_vs_hgbt") or {}
        out.append(_finding(
            "claim_lightgbm_catboost_paired_boosts",
            "LightGBM and CatBoost both clear the paired HGBT test",
            "positive",
            f"CatBoost +{_r(cb.get('delta_ap_vs_hgbt'))}; LightGBM +{_r(lg.get('delta_ap_vs_hgbt'))}",
            "Two independent tree learners beat frozen HGBT in paired AP tests, confirming the model ceiling is slightly above the old HGBT run.",
            "Paired bootstrap plus leave-one-county-out win counts on the same 89,321-row deployable stratum.",
            "reports/model_lab/paired_model_test.json",
            {
                "n_test": paired.get("n_test"),
                "catboost_delta_ap": _r(cb.get("delta_ap_vs_hgbt")),
                "catboost_ci95": cb.get("boot_ci95"),
                "catboost_loco_wins": (loco.get("catboost") or {}).get("wins_vs_hgbt"),
                "lightgbm_delta_ap": _r(lg.get("delta_ap_vs_hgbt")),
                "lightgbm_ci95": lg.get("boot_ci95"),
                "lightgbm_loco_wins": (loco.get("lightgbm") or {}).get("wins_vs_hgbt"),
            },
            "Registry still keeps them as benchmark/champion-upgrade candidates pending freeze/reproduction and operational-value gates.",
        ))
    return out


def _bacteria_operational_findings():
    out = []
    f1 = _local_json("reports/experiment_program/F1/f1_scorecard.json") or {}
    if f1:
        m = f1.get("frozen_model_forward") or {}
        rule = f1.get("stronger_baseline_rule") or {}
        out.append(_finding(
            "claim_forward_2026_holdout_pass",
            "Frozen bacteria model passes held-from-fitting 2026 season",
            "positive",
            f"AP {_r(m.get('ap'))} vs station-memory {_r(rule.get('best_baseline_ap'))}",
            "On the locked 2026-01-05 to 2026-03-05 held-from-fitting season, the frozen model beats the strongest baseline and retains discrimination.",
            "D3 holdout keys are hash-pinned; F1 scores the frozen model against persistence, seasonal, and station-memory baselines.",
            "reports/experiment_program/F1/f1_scorecard.json",
            {
                "n": m.get("n"),
                "events": m.get("events"),
                "base_rate": _r(m.get("base_rate")),
                "model_ap": _r(m.get("ap")),
                "model_roc_auc": _r(m.get("roc_auc")),
                "model_ece": _r(m.get("ece")),
                "best_baseline": rule.get("best_baseline"),
                "best_baseline_ap": _r(rule.get("best_baseline_ap")),
                "delta_ap": _r(rule.get("model_minus_best_baseline_ap")),
            },
            f1.get("honest_framing"),
        ))
    horizon = _local_json("bacteria_results/forecast_horizon.json") or {}
    if horizon:
        row2 = None
        for row in horizon.get("skill_decay_EXCLUDE_SAN_DIEGO", []):
            if row.get("lead_days") == 2:
                row2 = row
                break
        out.append(_finding(
            "claim_bacteria_honest_forecast_horizon",
            "Bacteria forecast horizon is honest to about 2 days",
            "positive",
            "skill over station-memory through lead 2d; lead 3d loses",
            "The corrected horizon claim compares against a multi-feature station-memory model, not the weak single-bit persistence baseline.",
            "Lead-day sweep with reveal-lag discipline and paired AP margin over station-memory model.",
            "bacteria_results/forecast_horizon.json",
            {
                "lead2_model_ap": _r((row2 or {}).get("model_ap")),
                "lead2_station_memory_ap": _r((row2 or {}).get("station_memory_model_ap")),
                "lead2_margin": _r((row2 or {}).get("margin_vs_station_memory_model")),
                "lead2_ci": (row2 or {}).get("margin_vs_station_memory_model_ci"),
                "lead2_skill": (row2 or {}).get("skill_over_station_memory_model"),
            },
            "Current per-lead rows show L=2 is CI-separated over station-memory; L=3 is a loss. Do not claim a 3-day horizon.",
        ))
    decision = _local_json("reports/bacteria/decision_rule.json") or {}
    if decision:
        op = (decision.get("deployable_rule") or {}).get("test_operating_point") or {}
        out.append(_finding(
            "claim_bacteria_decision_rule",
            "Deployable bacteria advisory rule catches most exceedances",
            "operational",
            f"recall {_r(op.get('recall'))} · precision {_r(op.get('precision'))}",
            "The calibrated decision rule turns the model into an advisory threshold chosen on validation, then read on the 2022+ test era.",
            "Validation-chosen threshold tau, then test operating-point and decision-curve readout.",
            "reports/bacteria/decision_rule.json",
            {
                "tau": (decision.get("deployable_rule") or {}).get("tau"),
                "tp": op.get("TP"),
                "fp": op.get("FP"),
                "fn": op.get("FN"),
                "tn": op.get("TN"),
                "recall": _r(op.get("recall")),
                "precision": _r(op.get("precision")),
                "flag_rate": _r(op.get("flag_rate")),
                "false_advisories_per_caught": _r(op.get("false_advisories_per_caught_exceedance")),
            },
            decision.get("honest_cost_note"),
        ))
    return out


def _experiment_program_findings():
    out = []
    b1 = _local_json("reports/experiment_program/B1/b1_results.json") or {}
    lead = ((b1.get("lead_sweep") or {}).get(str(b1.get("best_lead"))) or {})
    if lead:
        out.append(_finding(
            "claim_russian_river_advection_escape",
            "Russian River upstream turbidity adds lead-2 skill",
            "positive",
            f"dAP +{_r(lead.get('delta_ap_combo_vs_persist'))} vs persistence",
            "Upstream turbidity plus downstream persistence beats downstream persistence alone for the Guerneville intake turbidity event target.",
            "Leave-year-style out-of-fold lead sweep over Hopland/Digger Bend/Guerneville USGS IV turbidity.",
            "reports/experiment_program/B1/b1_results.json",
            {
                "best_lead_days": b1.get("best_lead"),
                "n_oof": lead.get("n_oof"),
                "events": lead.get("pos"),
                "ap_persist": _r(lead.get("ap_persist")),
                "ap_combo": _r(lead.get("ap_combo")),
                "delta_ap": _r(lead.get("delta_ap_combo_vs_persist")),
                "ci95": lead.get("delta_ci95"),
            },
            "A narrow advection escape, not a general bacteria-beach driver claim.",
        ))
    b2 = _local_json("reports/experiment_program/B2/b2_results.json") or {}
    try:
        new_escape = b2["results_by_param"]["turbidity_fnu"]["confirmations"][1]
        best = new_escape.get("best_lead") or {}
        out.append(_finding(
            "claim_cache_slough_upstream_escape",
            "Cache Slough upstream lead sweep finds one new escape",
            "positive",
            f"dAP +{_r(best.get('dap'))} at lead {best.get('lead')}d",
            "A systematic USGS-IV upstream/downstream sweep found one new turbidity advection escape beyond the Russian River positive control.",
            "Screen thousands of gauge pairs, then confirm top candidates with bootstrap and permutation gates.",
            "reports/experiment_program/B2/b2_results.json",
            {
                "upstream_gauge": new_escape.get("up"),
                "downstream_gauge": new_escape.get("dn"),
                "lead_days": best.get("lead"),
                "delta_ap": _r(best.get("dap")),
                "ci95": [best.get("lo"), best.get("hi")],
                "baseline": best.get("base"),
            },
            "Rare/special-case advection escape; possible tidal-phase artifact remains a caveat.",
        ))
    except Exception:
        pass
    b5 = _local_json("reports/experiment_program/B5/b5_results.json") or {}
    wet = (((b5.get("per_hazard") or {}).get("bacteria_fib") or {}).get("strata") or {}).get("wet_tail") or {}
    if wet:
        out.append(_finding(
            "claim_wet_tail_driver_skill",
            "Drivers matter on wet-tail bacteria extremes",
            "positive",
            f"wet-tail AP gain +{_r(wet.get('ap_gain'))}",
            "Although drivers do not improve the pooled mean, they add strong, CI-separated skill on the wet/storm high-consequence tail.",
            "Driver-free versus driver-augmented HGBT contrast on exogenous train-derived p90 severity regimes.",
            "reports/experiment_program/B5/b5_results.json",
            {
                "n": wet.get("n"),
                "events": wet.get("events"),
                "driver_free_ap": _r(wet.get("driver_free_ap")),
                "driver_aug_ap": _r(wet.get("driver_aug_ap")),
                "ap_gain": _r(wet.get("ap_gain")),
                "ci95": [((wet.get("ap_gain_boot_ci") or {}).get("ci_lo")), ((wet.get("ap_gain_boot_ci") or {}).get("ci_hi"))],
                "beats_best_naive": wet.get("aug_beats_best_naive_ap"),
            },
            "This is a tail-only claim; pooled AP slightly regresses.",
        ))
    return out


def _hypoxia_tokyo_findings():
    out = []
    gru = _local_json("reports/predict10x/hypoxia_gru.json") or {}
    if gru:
        h14 = (gru.get("per_horizon") or {}).get("14") or {}
        h28 = (gru.get("per_horizon") or {}).get("28") or {}
        out.append(_finding(
            "claim_hypoxia_gru_sequence_positive",
            "GRU sequence model beats HGBT at 14-28 day hypoxia lead",
            "positive",
            f"14d dAP +{_r((h14.get('gru_minus_hgbt_ap') or {}).get('median'))}; 28d +{_r((h28.get('gru_minus_hgbt_ap') or {}).get('median'))}",
            "A genuine temporal GRU extracts multi-week hypoxia lead dynamics that the stratification HGBT misses at 14 and 28 days.",
            "Train <=2021, test >2021; station-block/bootstrap deltas against HGBT-strat and persistence.",
            "reports/predict10x/hypoxia_gru.json",
            {
                "n_test_origins": gru.get("n_test_origins"),
                "h14_gru_ap": _r(((h14.get("scored") or {}).get("GRU_genuine") or {}).get("ap")),
                "h14_hgbt_ap": _r(((h14.get("scored") or {}).get("HGBT_strat") or {}).get("ap")),
                "h14_delta_ci": [
                    (h14.get("gru_minus_hgbt_ap") or {}).get("lo"),
                    (h14.get("gru_minus_hgbt_ap") or {}).get("hi"),
                ],
                "h28_gru_ap": _r(((h28.get("scored") or {}).get("GRU_genuine") or {}).get("ap")),
                "h28_hgbt_ap": _r(((h28.get("scored") or {}).get("HGBT_strat") or {}).get("ap")),
                "h28_delta_ci": [
                    (h28.get("gru_minus_hgbt_ap") or {}).get("lo"),
                    (h28.get("gru_minus_hgbt_ap") or {}).get("hi"),
                ],
            },
            "7d and 21d are not CI-separated against HGBT; report the horizon-specific result.",
        ))
    onset = _local_json("reports/experiment_program/G2/hypoxia_onset_forecast.json") or {}
    if onset:
        h7 = (onset.get("per_horizon") or {}).get("7") or {}
        out.append(_finding(
            "claim_hypoxia_onset_forecast",
            "Hypoxia onset forecast beats better naive across horizons",
            "positive",
            f"7d AP {_r(h7.get('best_model_ap'))} vs naive {_r(h7.get('better_naive_ap'))}",
            "On the currently-non-hypoxic origins, the stratification model beats the better seasonal/DO-proximity naive for 3-28 day onset windows.",
            "Tokyo Bay TBEIC onset target; train year <=2021, test year >2021; comparison against seasonal and DO-proximity nulls.",
            "reports/experiment_program/G2/hypoxia_onset_forecast.json",
            {
                "h7_n_test": h7.get("n_test"),
                "h7_events": h7.get("events_test"),
                "h7_model_ap": _r(h7.get("best_model_ap")),
                "h7_better_naive_ap": _r(h7.get("better_naive_ap")),
                "h14_model_ap": _r(((onset.get("per_horizon") or {}).get("14") or {}).get("best_model_ap")),
                "h28_model_ap": _r(((onset.get("per_horizon") or {}).get("28") or {}).get("best_model_ap")),
            },
            "Some leave-station-out stratification-lift cells are weak; this is an onset-gate positive with generalization caveats.",
        ))
    shadow = _local_json("reports/tokyo_bay_tbeic/lakehouse_shadow_model_probe.json") or {}
    if shadow:
        rows = [r for r in shadow.get("results", []) if r.get("positive_signal")]
        first = rows[0] if rows else {}
        out.append(_finding(
            "claim_tokyo_shadow_lakehouse_positives",
            "Tokyo Bay shadow probe finds multiple positive physical targets",
            "positive",
            f"{len(rows)} positive probe rows",
            "The Tokyo Bay lakehouse/shadow probe finds positive signals for haline stratification, surface chlorophyll, and low-bottom-oxygen targets.",
            "CPU ablation against station-month/persistence baselines with station-block support.",
            "reports/tokyo_bay_tbeic/lakehouse_shadow_model_probe.json",
            {
                "positive_rows": len(rows),
                "first_target": ((first.get("target") or {}).get("name")),
                "first_horizon_days": ((first.get("target") or {}).get("horizon_days")),
                "first_feature_set": first.get("feature_set"),
                "first_n_test": first.get("n_test"),
                "incumbent_gpu_ap": _r(first.get("incumbent_gpu_ap")),
            },
            "Research-only/noncommercial Tokyo Bay data; not an MBAL deployment claim.",
        ))
    gpu = _local_json("reports/tokyo_bay_tbeic/gpu_signal_miner.json") or {}
    if gpu:
        rows = [r for r in gpu.get("results", []) if r.get("positive_signal")]
        first = rows[0] if rows else {}
        if first:
            out.append(_finding(
                "claim_tokyo_gpu_bottom_chl_positive",
                "Tokyo Bay GPU miner finds bottom-chlorophyll signal",
                "positive",
                "high_bottom_chl_p90 neural AP 0.4195",
                "A GRU temporal multi-target classifier found a positive high-bottom-chlorophyll target signal in the Tokyo Bay test bed.",
                "GPU signal miner over Tokyo Bay TBEIC, train <=2021, test >=2022.",
                "reports/tokyo_bay_tbeic/gpu_signal_miner.json",
                {
                    "target": first.get("target"),
                    "n_test": first.get("n_test"),
                    "events": first.get("events"),
                    "neural_ap": _r(first.get("neural_ap")),
                    "delta_ap": _r(first.get("delta_ap")),
                },
                "Research-only/noncommercial Tokyo Bay data.",
            ))
    return out


def _cyano_findings():
    out = []
    alberta = _local_json("reports/hab/cyano/mcye_alberta.json") or {}
    if alberta:
        a = alberta.get("A_total_mcyE_given_biomass_cells") or {}
        out.append(_finding(
            "claim_mcye_alberta_definitive_replication",
            "mcyE genotype-to-toxin effect definitively replicates in Alberta",
            "replication",
            f"rho {_r(a.get('partial_rho'))} · n={a.get('n')} · {a.get('lakes')} lakes",
            "The toxic-genotype signal predicts microcystin beyond biomass across 55 lakes and 5 years in an independent Alberta bloom-monitoring corpus.",
            "Partial Spearman of mcyE versus microcystin controlling biomass and lake, with permutation and leave-one-lake/year robustness.",
            "reports/hab/cyano/mcye_alberta.json",
            {
                "n": a.get("n"),
                "lakes": a.get("lakes"),
                "partial_rho": _r(a.get("partial_rho")),
                "perm_p": a.get("perm_p"),
                "boot_ci95": a.get("boot_ci95"),
                "leave_one_lake_all_positive": ((alberta.get("robustness") or {}).get("leave_one_lake_out") or {}).get("all_positive"),
                "leave_one_year_all_positive": ((alberta.get("robustness") or {}).get("leave_one_year_out") or {}).get("all_positive"),
            },
            "Scope is detectable/bloom-regime samples; biomass is total-cyano cell count, not chlorophyll.",
        ))
    national = _local_json("reports/hab/cyano/mcye_national_replication.json") or {}
    if national:
        a = national.get("A_mcyE_absolute_given_biomass") or {}
        out.append(_finding(
            "claim_mcye_national_replication",
            "mcyE genotype-to-toxin effect replicates nationally",
            "replication",
            f"rho {_r(a.get('partial_rho'))} · perm-p {a.get('perm_p')}",
            "A national WQP paired corpus confirms mcyE absolute predicts microcystin beyond biomass across independent lakes and years.",
            "Partial Spearman with biomass control, permutation null, leave-one-site and leave-one-year checks.",
            "reports/hab/cyano/mcye_national_replication.json",
            {
                "n": a.get("n"),
                "stations": a.get("stations"),
                "partial_rho": _r(a.get("partial_rho")),
                "perm_p": a.get("perm_p"),
                "boot_ci95": a.get("boot_ci95"),
                "loso_positive_folds": ((a.get("LOSO") or {}).get("positive_folds")),
                "loyo_positive_folds": ((a.get("LOYO") or {}).get("positive_folds")),
            },
            (national.get("verdict") or {}).get("honest_note"),
        ))
    driver = _local_json("reports/hab/erie/national_microcystin_driver_gate.json") or {}
    if driver:
        out.append(_finding(
            "null_national_microcystin_driver",
            "National microcystin driver-null holds",
            "null",
            f"met dAP {_r((driver.get('leave_year_out') or {}).get('delta_ap'))} LOYO",
            "Adding antecedent meteorology to history+season does not improve national in-situ microcystin toxin forecasts.",
            "National WQP microcystin panel, leave-year-out and leave-site-out driver gates.",
            "reports/hab/erie/national_microcystin_driver_gate.json",
            {
                "n_pairs": driver.get("n_pairs"),
                "n_sites": driver.get("n_sites"),
                "loyo_delta_ap": _r((driver.get("leave_year_out") or {}).get("delta_ap")),
                "loso_delta_ap": _r((driver.get("leave_site_out") or {}).get("delta_ap")),
                "any_driver_break": driver.get("ANY_DRIVER_BREAK"),
            },
            driver.get("verdict"),
        ))
    return out


def _latest_research_findings():
    out = []

    da = _local_json("reports/hab/da_forecast.json") or {}
    h = da.get("headline") or {}
    onset = da.get("onset_unit_primary") or {}
    if h and onset:
        out.append(_finding(
            "claim_da_toxic_onset_ci_separated",
            "Domoic-acid toxic-onset forecast clears the scoped gate",
            "claim",
            f"onset AP {_r(h.get('onset_model_ap'))} vs {h.get('onset_strongest_baseline')} {_r(h.get('onset_strongest_baseline_ap'))}",
            "The updated domoic-acid result is strongest on the decision-relevant toxic-onset slice: prior-clean pier visits where persistence is degenerate.",
            "Train <=2018, calibration 2019-2020, test >2020; station-clustered bootstrap and permutation-null read on prior-clean toxic-onset rows.",
            "reports/hab/da_forecast.json",
            {
                "n": h.get("onset_n"),
                "events": h.get("onset_events"),
                "model_ap": _r(h.get("onset_model_ap")),
                "strongest_baseline": h.get("onset_strongest_baseline"),
                "strongest_baseline_ap": _r(h.get("onset_strongest_baseline_ap")),
                "margin_ap": _r(h.get("onset_margin_ap")),
                "ci95": h.get("onset_cluster_boot_ci95"),
                "perm_p": h.get("onset_perm_p"),
                "pooled_model_ap": _r(h.get("pooled_model_ap")),
                "pooled_best_baseline_ap": _r(h.get("pooled_best_baseline_ap")),
                "loso_model_beats_seasonal": h.get("loso_model_beats_seasonal"),
            },
            "Scope discipline: this promotes the prior-clean onset unit, not the older pooled persistence comparison or the broken C-HARM framing.",
        ))

    cyano = _local_json("reports/hab/cyano/cyano_onset_claim_card_redteam.json") or {}
    cyano_law = _local_json("reports/hab/cyano/cyano_onset_law.json") or {}
    law = cyano.get("law_evidence_original") or {}
    state_cv = law.get("lat_quad_leave_one_state") or {}
    met_cv = (((cyano.get("adversarial_checks") or {}).get("forecast_valid_prior_winter_met_competitor") or {})
              .get("cv") or {}).get("leave_one_state_out", {}).get("models", {}).get("lat_quad+winter_met") or {}
    if cyano and state_cv:
        out.append(_finding(
            "claim_cyano_onset_latitude_law_scoped",
            "Cyano onset has a scoped cold-start phenology law",
            "positive",
            f"lat-quad skill {_r(state_cv.get('skill_vs_global'))} CI {state_cv.get('skill_ci')}",
            "Latitude/quadratic geography gives a real cold-start cyanobacteria bloom-onset timing prior under held-out state, lat-band, and year tests.",
            "Continental lake-year onset panel with adversarial red-team checks: current-year censoring, held-out state/lat-band/year CV, and prior-winter meteorology hardening.",
            "reports/hab/cyano/cyano_onset_claim_card_redteam.json",
            {
                "n_lake_years": cyano_law.get("n_lake_years") or state_cv.get("n_test"),
                "n_lakes": cyano_law.get("n_lakes"),
                "n_states": cyano_law.get("n_states"),
                "year_range": cyano_law.get("year_range"),
                "original_leave_one_state_skill": _r(state_cv.get("skill_vs_global")),
                "original_leave_one_state_ci95": state_cv.get("skill_ci"),
                "winter_met_rows": ((cyano.get("adversarial_checks") or {}).get("forecast_valid_prior_winter_met_competitor") or {}).get("n_lake_years_with_met"),
                "lat_quad_plus_winter_met_skill": _r(met_cv.get("skill_vs_global")),
                "lat_quad_plus_winter_met_ci95": met_cv.get("skill_ci"),
            },
            "Claim onset timing/sampling-start prior only; the photoperiod-only mechanism is explicitly not the claim anchor.",
        ))

    cyano_pi = _local_json("reports/hab/interlingua_cyano_transfer_daily_report.json") or {}
    daily = cyano_pi.get("freshwater_pi_daily") or {}
    if daily:
        out.append(_finding(
            "null_interlingua_cyano_daily_refutation",
            "Daily freshwater cyano interlingua still does not beat seasonality",
            "null",
            f"pi AP {_r(daily.get('mean_ap_pi'))} vs calendar {_r(daily.get('mean_ap_calendar'))}",
            "Daily meteorology narrows the freshwater cyano interlingua gap but does not flip the prior null: physics π features do not separate from calendar or persistence on held-out lakes.",
            "Leave-one-state/region-out daily-met π features against calendar and persistence controls.",
            "reports/hab/interlingua_cyano_transfer_daily_report.json",
            {
                "n_lakes": cyano_pi.get("n_lakes"),
                "n_regions": daily.get("n_regions"),
                "n_rows_total": daily.get("n_rows_total"),
                "mean_ap_pi": _r(daily.get("mean_ap_pi")),
                "mean_ap_calendar": _r(daily.get("mean_ap_calendar")),
                "mean_ap_persistence": _r(daily.get("mean_ap_persistence")),
                "pi_minus_calendar_ci95": daily.get("pi_minus_calendar_ci95"),
                "pi_minus_persistence_ci95": daily.get("pi_minus_persistence_ci95"),
            },
            cyano_pi.get("verdict"),
        ))

    eu = _local_json("reports/interlingua_eu_transfer.json") or {}
    panel = eu.get("eu_panel") or {}
    if eu:
        out.append(_finding(
            "null_interlingua_eu_resolution_boundary",
            "EU annual bathing-water transfer exposes an interlingua boundary",
            "null",
            str(eu.get("verdict") or "BOUNDARY"),
            "The donor π signal survives annual coarsening, but it still does not outrank calendar plus site-memory on held-out EU coasts, so the manifold does not generalize to annual EU bathing-water classes.",
            "Leave-country-out EU annual-class transfer plus a resolution-control donor test separating data resolution from geography.",
            "reports/interlingua_eu_transfer.json",
            {
                "n_site_years": panel.get("n_site_years"),
                "n_countries": panel.get("n_countries"),
                "n_sites": panel.get("n_sites"),
                "bad_base_rate": _r(panel.get("bad_base_rate")),
                "pi_finite_frac": _r(panel.get("pi_finite_frac")),
            },
            eu.get("verdict_reason") or eu.get("data_resolution_caveat"),
        ))

    ddpcr = _local_json("reports/bacteria/ddpcr_real_vs_artifact.json") or {}
    ctrl = ddpcr.get("confound_control_vs_culture_history") or {}
    raw = ddpcr.get("future_culture_exceed_within_14d") or {}
    if ddpcr:
        out.append(_finding(
            "null_ddpcr_target_redefinition_artifact",
            "ddPCR-positive/culture-safe days are mostly a persistence artifact",
            "null",
            f"raw lift { _r(raw.get('delta_pos_minus_neg'))}; controlled dAUC {_r(ctrl.get('ddpcr_adds_auc'))}",
            "Same-day ddPCR positives among culture-safe San Diego beach-days have raw forward culture-exceedance lift, but ddPCR does not add clean signal after full culture-memory controls.",
            "San Diego culture-safe station-days with same-day ddPCR call; full culture-memory baseline plus bootstrap CI and within-history-tertile checks.",
            "reports/bacteria/ddpcr_real_vs_artifact.json",
            {
                "n_culture_safe_with_future": ddpcr.get("n_culture_safe_with_future"),
                "ddpcr_pos_n": ddpcr.get("ddpcr_pos_n"),
                "ddpcr_neg_n": ddpcr.get("ddpcr_neg_n"),
                "future_exc_given_ddpcr_pos": _r(raw.get("given_ddpcr_pos")),
                "future_exc_given_ddpcr_neg": _r(raw.get("given_ddpcr_neg")),
                "raw_delta_ci95": raw.get("delta_ci95"),
                "history_only_auc": _r(ctrl.get("oof_auc_full_history_only")),
                "history_plus_ddpcr_auc": _r(ctrl.get("oof_auc_full_history_plus_ddpcr")),
                "ddpcr_adds_auc": _r(ctrl.get("ddpcr_adds_auc")),
                "ddpcr_adds_auc_ci95": ctrl.get("ddpcr_adds_auc_ci95"),
            },
            ddpcr.get("verdict"),
        ))

    sog = _local_json("reports/predict10x/sog_central_onset.json") or {}
    if sog:
        out.append(_finding(
            "null_sog_central_relief_onset_underpowered",
            "SoG Central relief-onset transfer auto-rejects as underpowered",
            "null",
            f"{sog.get('n_onset_events')} events < gate {((sog.get('power_gate') or {}).get('min_events'))}",
            "The pre-registered Saanich-to-SoG relief-onset transfer test does not fit a model because SoG Central has too few locked-definition deep-oxygen relief onsets.",
            "Locked hypoxia-line relief-onset definition, event-count power gate, and no model fit after gate failure.",
            "reports/predict10x/sog_central_onset.json",
            {
                "n_days": sog.get("n_days"),
                "date_range": sog.get("date_range"),
                "n_onset_events": sog.get("n_onset_events"),
                "min_events": ((sog.get("power_gate") or {}).get("min_events")),
                "power_gate_passed": ((sog.get("power_gate") or {}).get("passed")),
            },
            sog.get("read") or sog.get("honest_verdict"),
        ))

    semantic = _local_json("lakehouse/semantic_shadow/gold/discovery_runs/latest.json") or {}
    if semantic:
        top_corr = (semantic.get("top_correlations") or [{}])[0]
        top_model = (semantic.get("top_model_lifts") or [{}])[0]
        out.append(_finding(
            "status_semantic_shadow_refresh_discovery_leads",
            "Semantic shadow refresh produced discovery leads, not claims",
            "caveat",
            f"{semantic.get('targets_scanned')} targets · {semantic.get('correlation_rows')} correlations",
            "The universal shadow-lakehouse refresh found cross-domain correlations and temporal-holdout model lifts, but they remain discovery leads until independently reproduced.",
            "All-domain semantic-shadow discovery runner over bacteria, HAB, and whale targets with temporal holdouts and target-namespace exclusions.",
            "reports/semantic_lakehouse/semantic_shadow_discovery.md",
            {
                "run_id": semantic.get("run_id"),
                "targets_scanned": semantic.get("targets_scanned"),
                "model_rows": semantic.get("model_rows"),
                "correlation_rows": semantic.get("correlation_rows"),
                "top_correlation_domain": top_corr.get("domain"),
                "top_correlation_target": top_corr.get("target"),
                "top_correlation_feature": top_corr.get("feature"),
                "top_correlation_rho": _r(top_corr.get("rho")),
                "top_correlation_ci95": [top_corr.get("ci_low"), top_corr.get("ci_high")],
                "top_model_domain": top_model.get("domain"),
                "top_model_target": top_model.get("target"),
                "top_model_delta_vs_baseline": _r(top_model.get("delta_vs_baseline")),
            },
            "Promote only after a pre-registered reproduction job with a permutation/null control.",
        ))

    tsunami = _local_json("reports/tsunami/tsunami_warning_value.json") or {}
    if tsunami:
        fa80 = (tsunami.get("false_alarm_reduction_at_fixed_recall") or [{}])[0]
        out.append(_finding(
            "claim_tsunami_halfduration_warning_screen",
            "Rupture half-duration can reduce tsunami false alarms at fixed recall",
            "positive",
            f"{fa80.get('pct_reduced')}% fewer false alarms at 80% recall",
            "Adding rupture half-duration to magnitude+depth reduces false alarms in the NOAA tsunami label screen at 80% and 90% fixed recall, but not at 95% recall.",
            "Leave-one-region-out seismic/tsunami screen over recorded tsunamis, comparing magnitude+depth to magnitude+depth+half-duration.",
            "reports/tsunami/tsunami_warning_value.json",
            {
                "n_eqs": tsunami.get("n_eqs"),
                "recorded_tsunamis": tsunami.get("recorded_tsunamis"),
                "base_rate": _r(tsunami.get("base_rate")),
                "held_out": tsunami.get("held_out"),
                "false_alarm_reduction_at_fixed_recall": tsunami.get("false_alarm_reduction_at_fixed_recall"),
            },
            "Screening value only; not a public warning deployment claim.",
        ))

    carte = _local_json("reports/bacteria/carte_critic.json") or {}
    auc = carte.get("primary_seed_auc") or {}
    cis = carte.get("decisive_cis_primary_seed") or {}
    if carte:
        out.append(_finding(
            "claim_carte_numeric_shared_plane_benchmark",
            "CARTE-style shared-plane benchmark adds bacteria ranking signal",
            "positive",
            f"numeric-only AUC {_r(auc.get('carte_numeric_only'))} vs raw {_r(auc.get('raw'))}",
            "A CARTE-style tabular shared-plane benchmark improves held-out bacteria ranking over raw and fair-ID baselines in the small pilot/critic read.",
            "Pilot plus critic stability read on held-out bacteria rows with bootstrap deltas against raw, memory, and fair-ID baselines.",
            "reports/bacteria/carte_critic.json",
            {
                "n_test": carte.get("n_test"),
                "test_events": carte.get("test_events"),
                "base_rate": _r(carte.get("base_rate")),
                "memory_auc": _r(auc.get("memory")),
                "raw_auc": _r(auc.get("raw")),
                "fair_id_baseline_auc": _r(auc.get("fair_id_baseline")),
                "carte_full_auc": _r(auc.get("carte_full")),
                "carte_numeric_only_auc": _r(auc.get("carte_numeric_only")),
                "carte_full_minus_fair_ci95": cis.get("carte_full_minus_fair"),
                "carte_numeric_only_minus_raw_ci95": cis.get("carte_numeric_only_minus_raw"),
            },
            "Benchmark lead, not the shipped champion; operational threshold value still needs a deployment-style gate.",
        ))

    return out


def _source_signal_findings():
    out = []
    soil = _local_json("reports/bacteria/new_source_signal_gate/soil_permutation_null.json") or {}
    if soil:
        out.append(_finding(
            "claim_soil_moisture_gate_positive",
            "Soil-moisture bacteria signal survives a shuffle null",
            "caveat",
            f"observed dAP +{_r(soil.get('observed_delta_ap'))} > null max {_r(soil.get('null_max_delta_ap'))}",
            "The soil-moisture feature lift is genuine under the source-gate permutation test, but later operational reads say it is mostly redundant with discharge.",
            "Shuffle soil features and compare observed AP lift to a permutation null.",
            "reports/bacteria/new_source_signal_gate/soil_permutation_null.json",
            {
                "observed_delta_ap": _r(soil.get("observed_delta_ap")),
                "null_mean_delta_ap": _r(soil.get("null_mean_delta_ap")),
                "null_max_delta_ap": _r(soil.get("null_max_delta_ap")),
                "p_value_one_sided": soil.get("p_value_one_sided"),
            },
            soil.get("verdict") + "; keep as gate-positive, not operationally shipped.",
        ))
    verdicts = _local_json("reports/bacteria/new_source_signal_gate/verdicts.json") or {}
    cands = [c for c in verdicts.get("candidates", []) if c.get("verdict") == "KEEP"]
    if cands:
        best = cands[0]
        out.append(_finding(
            "claim_new_source_signal_gate_keeps",
            "New-source bacteria gate has multiple KEEP candidates",
            "positive",
            f"{len(cands)} KEEP candidates; best dAP +{_r(best.get('delta_ap'))}",
            "The new-source signal gate found several small but positive bacteria feature candidates before stricter operational redundancy checks.",
            "EXCLUDE_SAN_DIEGO source gate against FEATS+rain baseline with LOBO deltas.",
            "reports/bacteria/new_source_signal_gate/verdicts.json",
            {
                "n_test": verdicts.get("n_test"),
                "events": verdicts.get("events"),
                "baseline_ap": _r((verdicts.get("baseline") or {}).get("ap")),
                "keep_count": len(cands),
                "best_candidate": best.get("name"),
                "best_delta_ap": _r(best.get("delta_ap")),
                "best_lobo_delta_ap": _r(best.get("lobo_delta_ap")),
            },
            "Treat these as feature-gate positives; the frozen deployed headline remains the controlled reference.",
        ))
    return out


def _findings_campaign_findings():
    out = []
    ledger = _local_json("reports/findings_campaign/final_evidence_ledger.json") or {}
    if ledger:
        counts = ledger.get("counts") or {}
        missing = counts.get("missing_output", 0) or 0
        frontier = {
            j.get("id"): j.get("category")
            for j in ledger.get("jobs", [])
            if j.get("priority", 999) < 100
        }
        missing_text = (
            "no declared outputs are missing in the current ledger"
            if missing == 0
            else f"{missing} declared output{' is' if missing == 1 else 's are'} still pending"
        )
        out.append(_finding(
            "status_findings_campaign_10h_rerun",
            "10-hour findings campaign rerun is mostly complete",
            "status",
            (
                f"{counts.get('claim_or_keep', 0)} keep · "
                f"{counts.get('strong_null_or_refutation', 0)} null/refutation · "
                f"{missing} pending"
            ),
            f"Fresh dry-onset outputs now synthesize into the final evidence ledger; {missing_text}.",
            "Campaign synthesis reads the declared 10-hour job queue plus prior evidence reports and categorizes each output without recomputing science.",
            "reports/findings_campaign/final_evidence_ledger.json",
            {
                "job_count": ledger.get("job_count"),
                "claim_or_keep": counts.get("claim_or_keep"),
                "strong_null_or_refutation": counts.get("strong_null_or_refutation"),
                "missing_output": counts.get("missing_output"),
                "frontier_categories": frontier,
            },
            "Current frontier read: dry-onset interaction and symbolic are strong_null_or_refutation; targeted bacteria L2/L3 is claim_or_keep; serendipity top100 is still pending.",
        ))

    targeted = _local_json("reports/findings_campaign/bacteria_signal_discovery_targeted/signal_discovery.json") or {}
    l2 = targeted.get("l2") or {}
    kept = targeted.get("kept_l2") or [
        c.get("name") for c in l2.get("candidates", []) if c.get("verdict") == "KEEP"
    ]
    candidates = [c for c in l2.get("candidates", []) if c.get("verdict") == "KEEP"]
    if kept and candidates:
        best = max(candidates, key=lambda c: c.get("delta_ap") or -999)
        l3 = targeted.get("l3_proposal") or {}
        out.append(_finding(
            "claim_findings_campaign_targeted_l2_keeps",
            "Targeted bacteria L2 gate keeps residual signals",
            "positive",
            f"{len(kept)} L2 KEEP: {', '.join(kept)}",
            "The narrowed bacteria L2/L3 rerun keeps neighborhood-plus-location, location, and waves over the established FEATS+rain baseline.",
            "Temporal 2022+ A/B plus leave-one-beach-out generalization on the existing bacteria target and driver tables.",
            "reports/findings_campaign/bacteria_signal_discovery_targeted/signal_discovery.json",
            {
                "n_test": l2.get("n_test"),
                "events": l2.get("events"),
                "baseline_ap": _r((l2.get("baseline") or {}).get("ap")),
                "kept_l2": kept,
                "best_candidate": best.get("name"),
                "best_delta_ap": _r(best.get("delta_ap")),
                "best_lobo_delta_ap": _r(best.get("lobo_delta_ap")),
                "l3_registered": l3.get("registered"),
                "l3_min_independence": l3.get("min_independence"),
            },
            l3.get("reason") or "L3 composition is not promoted unless two kept L2 signals are residual-independent.",
        ))

    interaction = _local_json("reports/findings_campaign/bacteria_dry_onset_interaction.json") or {}
    ixn = ((interaction.get("engines") or {}).get("interaction") or {})
    survivor = (ixn.get("real_survivors_onset_ab") or [None])[0]
    if survivor:
        fdr = ixn.get("fdr") or {}
        scan = ixn.get("scan") or {}
        cols = [_short_feature(c) for c in survivor.get("feat_cols", []) if c != "estate_ixn"]
        out.append(_finding(
            "null_findings_campaign_dry_onset_interaction",
            "Dry-onset interaction scan finds only a weak inspect-before-ship hit",
            "null",
            f"dAP +{_r(survivor.get('delta_ap_over_deployed_onset'))}; {fdr.get('n_pairs_pass_perm_fwer_p05', 0)} FWER survivors",
            "One discharge-by-precipitation interaction adds a small onset-slice AP lift in the tree A/B read, but the broader interaction screen does not clear the permutation family-wise error gate.",
            "GPU pairwise screen over existing estate signals, then onset-slice refit against the deployed FEATS+rain model with block-bootstrap AP CI.",
            "reports/findings_campaign/bacteria_dry_onset_interaction.json",
            {
                "candidate_features": cols,
                "n_test_onset": survivor.get("n_test_onset"),
                "events_test_onset": survivor.get("events_test_onset"),
                "deployed_ap_onset": _r(survivor.get("ap_deployed_model_onset")),
                "candidate_ap_onset": _r(survivor.get("ap_deployed_plus_candidate_onset")),
                "delta_ap": _r(survivor.get("delta_ap_over_deployed_onset")),
                "delta_ap_ci95": survivor.get("delta_ap_block_boot_ci95"),
                "pairs_tested": scan.get("pairs_tested"),
                "n_perm": fdr.get("n_perm"),
                "bh_fdr_pass": fdr.get("n_pairs_pass_bh_fdr_q10"),
                "perm_fwer_pass": fdr.get("n_pairs_pass_perm_fwer_p05"),
            },
            interaction.get("verdict") or ixn.get("verdict"),
        ))

    symbolic = _local_json("reports/findings_campaign/bacteria_dry_onset_symbolic.json") or {}
    sym = ((symbolic.get("engines") or {}).get("symbolic") or {})
    if sym:
        search = sym.get("search") or {}
        controls = sym.get("selection_bias_controls") or {}
        perm = controls.get("permutation_null") or {}
        fdr = controls.get("bh_fdr") or {}
        out.append(_finding(
            "null_findings_campaign_dry_onset_symbolic",
            "Dry-onset symbolic search stays null after FDR",
            "null",
            f"{search.get('formulas_evaluated', 0):,} formulas · {fdr.get('n_survivors', 0)} FDR survivors",
            "The symbolic rerun found training-skill formulas, but none survived out-of-time onset residual FDR, so it does not create a new bacteria driver.",
            "Bounded symbolic search over existing estate signals with best-of-search permutation control and BH-FDR on the Pareto front.",
            "reports/findings_campaign/bacteria_dry_onset_symbolic.json",
            {
                "formulas_evaluated": search.get("formulas_evaluated"),
                "formulas_per_sec": _r(search.get("formulas_per_sec")),
                "perm_p": perm.get("perm_p"),
                "n_pareto": fdr.get("n_pareto"),
                "n_survivors": fdr.get("n_survivors"),
                "total_seconds": _r(sym.get("total_seconds")),
            },
            symbolic.get("verdict") or sym.get("verdict"),
        ))
    return out


def _serendipity_findings():
    rows = _local_json("reports/serendipity/couplings_gated.json") or []
    supported = [r for r in rows if r.get("verdict") == "SUPPORTED"]
    if not supported:
        return []
    top = supported[0]
    return [_finding(
        "claim_serendipity_couplings_supported",
        "Cross-domain serendipity couplings have two supported rows",
        "positive",
        f"{len(supported)} supported couplings",
        "The gated cross-domain search found two supported couplings over target memory+season baselines.",
        "Permutation/stability-gated AUC/AP lift over memory+season for paired driver-target rows.",
        "reports/serendipity/couplings_gated.json",
        {
            "supported_count": len(supported),
            "top_driver": top.get("driver"),
            "top_target": top.get("target"),
            "top_auc_lift": _r(top.get("auc_lift")),
            "top_ap_lift": _r(top.get("ap_lift")),
            "top_perm_p": top.get("perm_p"),
            "top_n_test": top.get("n_test"),
        },
        "Discovery queue evidence, not a production model claim.",
    )]


def _trust_index_findings():
    h1 = _local_json("reports/experiment_program/H1/claims_index.json") or {}
    summary = h1.get("summary") or {}
    out = []
    if summary:
        out.append(_finding(
            "status_h1_trust_index",
            "H1 trust index tracks promoted claims and nulls",
            "status",
            f"{summary.get('n_signal_claims')} signals · {summary.get('n_experiment_claims')} experiments",
            "The trust-layer index is the broader source that the public page was missing: it joins promoted claims, logged nulls, and frozen headline integrity.",
            "Read-only join from signal catalog, experiment ledger, and frozen headline.",
            "reports/experiment_program/H1/claims_index.json",
            {
                "signal_claims": summary.get("n_signal_claims"),
                "signal_status_counts": summary.get("signal_status_counts"),
                "experiment_claims": summary.get("n_experiment_claims"),
                "experiment_gate_counts": summary.get("experiment_gate_counts"),
                "target_headlines": summary.get("n_target_headlines"),
                "frozen_headline_integrity_ok": summary.get("frozen_headline_integrity_ok"),
            },
            "This card explains why the old six-row cockpit payload was under-scoped.",
        ))
    return out


def _major_null_findings():
    out = []
    estate = _local_text("reports/estate_search/FINDINGS.md")
    if estate:
        out.append(_finding(
            "null_estate_gpu_signal_search",
            "Estate-wide GPU signal search finds no new bacteria carrier",
            "null",
            "474,825 interactions + 3M formulas + knockoffs: zero shippable survivors",
            "A large GPU search over nonlinear interactions and symbolic forms did not find a new bacteria signal beyond the frozen baseline.",
            "Interaction scan, symbolic regression, and deep knockoff selection, each with FDR/permutation controls and critic review.",
            "reports/estate_search/FINDINGS.md",
            {
                "interaction_pairs": 474825,
                "symbolic_formulas": 3000000,
                "deep_knockoff_candidates": 321,
                "real_survivors": 0,
            },
            "The null is valid; future positives from the wide matrices require stricter leakage filters first.",
        ))
    erie = _local_text("reports/hab/erie/ERIE_CYANO_DRIVER_BREAK_FINDINGS.md")
    if erie:
        out.append(_finding(
            "null_erie_loading_driver_break",
            "Western Lake Erie loading does not break the microcystin driver-null",
            "null",
            "loading dAP -0.028; national met dAP -0.007",
            "Even in the canonical nutrient-driven Erie system, tributary loading does not add station-level microcystin forecast skill once history and season are controlled.",
            "Erie GLERL toxin plus Heidelberg loading, critic probes, and national WQP microcystin driver gate.",
            "reports/hab/erie/ERIE_CYANO_DRIVER_BREAK_FINDINGS.md",
            {
                "erie_loading_delta_ap": -0.0278,
                "national_leave_year_delta_ap": -0.007,
                "driver_break": False,
            },
            "The positive molecular lever is genotype/toxin; the loading/weather forecast lever remains null.",
        ))
    return out


def _extra_findings():
    cards = []
    for maker in (
        _claim_card_findings,
        _science_breakthrough_findings,
        _model_lab_findings,
        _bacteria_operational_findings,
        _experiment_program_findings,
        _hypoxia_tokyo_findings,
        _cyano_findings,
        _latest_research_findings,
        _source_signal_findings,
        _findings_campaign_findings,
        _serendipity_findings,
        _trust_index_findings,
        _major_null_findings,
    ):
        cards.extend(maker())
    seen = set()
    out = []
    for c in cards:
        if c["id"] in seen:
            continue
        seen.add(c["id"])
        out.append(c)
    return out


def _augment_models(state):
    models = state.get("models") or []
    champ = _local_json("reports/model_lab/champion_claimcard_B.json") or {}
    card = champ.get("claim_card") or {}
    for m in models:
        if m.get("model_id") == "bacteria_catboost" and card:
            model = card.get("model") or {}
            base = card.get("baseline") or {}
            margin = card.get("margin") or {}
            m["evidence"] = "reports/model_lab/champion_claimcard_B.json"
            m["evidence_exists"] = True
            m["beats_baseline"] = (
                "SUPPORTED ranking upgrade over frozen HGBT: "
                f"AP {_r(model.get('ap'))} vs {_r(base.get('ap'))}, "
                f"dAP +{_r(margin.get('margin_ap'))}, CI {margin.get('ci95')}. "
                "Record-don't-ship operationally because advisory-threshold net-benefit gain is negligible."
            )
            m["plain"] = (
                "CatBoost is a real model-level ranking improvement over frozen HGBT, "
                "but the current decision card says record it rather than swap the operational champion."
            )
        if m.get("model_id") == "bacteria_lightgbm":
            paired = _local_json("reports/model_lab/paired_model_test.json") or {}
            lg = ((paired.get("paired_vs_hgbt") or {}).get("lightgbm") or {})
            if lg:
                m["evidence"] = "reports/model_lab/paired_model_test.json"
                m["evidence_exists"] = True
                m["beats_baseline"] = (
                    f"REAL BOOST vs HGBT: dAP +{_r(lg.get('delta_ap_vs_hgbt'))}, "
                    f"paired CI {lg.get('boot_ci95')} excludes zero."
                )
        if m.get("model_id") == "da_forecast_hgbt":
            da = _local_json("reports/hab/da_forecast.json") or {}
            h = da.get("headline") or {}
            if h:
                m["evidence"] = "reports/hab/da_forecast.json"
                m["evidence_exists"] = True
                m["beats_baseline"] = (
                    "SUPPORTED on toxic-onset prior-clean slice: "
                    f"AP {_r(h.get('onset_model_ap'))} vs {h.get('onset_strongest_baseline')} "
                    f"{_r(h.get('onset_strongest_baseline_ap'))}, "
                    f"dAP +{_r(h.get('onset_margin_ap'))}, CI {h.get('onset_cluster_boot_ci95')}, "
                    f"perm-p {h.get('onset_perm_p')}. Pooled AP {_r(h.get('pooled_model_ap'))} vs "
                    f"best pooled baseline {_r(h.get('pooled_best_baseline_ap'))}; headline stays scoped to onset."
                )
                m["plain"] = (
                    "The updated DA/HAB model is a scoped toxic-onset warning candidate: "
                    "use the prior-clean onset gate as the claim, not the older pooled persistence or C-HARM framing."
                )
    return state


def _asset_entry(asset_id, rel, source, status="ready", time_column=None, note=None):
    p = SOURCE_ROOT / rel
    if not p.exists():
        return None
    rows, est = _count_rows(str(p))
    item = {
        "asset_id": asset_id,
        "path": rel,
        "kind": p.suffix.lstrip(".") or "file",
        "exists": True,
        "size_mb": round(p.stat().st_size / (1024 * 1024), 3),
        "modified_utc": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat().replace("+00:00", "Z"),
        "age_days": round((time.time() - p.stat().st_mtime) / 86400, 1),
        "rows": rows,
        "time_column": time_column,
        "status": status,
        "source": source,
    }
    if est:
        item["rows_estimated"] = True
    if note:
        item["note"] = note
    return item


def _append_latest_public_data_assets(state):
    assets = list(state.get("data_assets") or [])
    existing = {a.get("asset_id") for a in assets}
    additions = [
        _asset_entry(
            "merra2_prithvi_ee_surface_sample",
            "data/external_raw/merra2_prithvi_input/merra2_ee_surface_sample.parquet",
            "NASA MERRA-2 surface variables via Google Earth Engine; no GES DISC EULA needed for station-level surface features.",
            time_column="date",
            note="Prithvi-WxC surface input sample at CA coastal points; 3D pressure-level/global rollout path still needs GES DISC authorization.",
        ),
        _asset_entry(
            "sentinel3_olci_chlorophyll_adapter",
            "data/external_curated/sentinel3_olci_chl/sentinel3_olci_chl.parquet",
            "ESA Copernicus Sentinel-3 OLCI/SLSTR chlorophyll source adapter for Granite-Ocean/NPP/HAB work.",
            time_column="time",
            note="Source data present locally; original 512k Granite-Ocean pretraining images are not publicly released.",
        ),
        _asset_entry(
            "semantic_shadow_model_scores",
            "lakehouse/semantic_shadow/gold/discovery_runs/model_scores.parquet",
            "Semantic-shadow all-domain discovery runner; temporal-holdout model score table.",
            note="Discovery leads only until independent reproduction.",
        ),
        _asset_entry(
            "semantic_shadow_candidate_correlations",
            "lakehouse/semantic_shadow/gold/discovery_runs/candidate_correlations.parquet",
            "Semantic-shadow all-domain discovery runner; deseasonalized lead/lag candidate correlations.",
            note="Candidate correlations are not promoted claims without a pre-registered reproduction gate.",
        ),
        _asset_entry(
            "semantic_shadow_target_profiles",
            "lakehouse/semantic_shadow/gold/discovery_runs/target_profiles.parquet",
            "Semantic-shadow all-domain discovery runner; target profile metadata for scanned domains.",
            note="Public-safe metadata for the refreshed shadow-discovery read.",
        ),
    ]
    for item in additions:
        if item and item["asset_id"] not in existing:
            assets.append(item)
            existing.add(item["asset_id"])
    state["data_assets"] = assets
    counts = dict(state.get("counts") or {})
    counts["data_assets"] = len(assets)
    state["counts"] = counts
    return state


def _augment_state(state):
    extra = _extra_findings()
    legacy = state.get("findings") or []
    legacy_keep = []
    legacy_archive = []
    for f in legacy:
        fid = f.get("id", "")
        if fid.startswith("claim_"):
            continue
        if fid in {"bacteria_statewide", "bacteria_monterey", "bacteria_ablation"}:
            legacy_archive.append(f)
        else:
            legacy_keep.append(f)
    state["findings"] = extra + legacy_keep + legacy_archive
    _augment_models(state)
    _append_latest_public_data_assets(state)
    counts = dict(state.get("counts") or {})
    counts["findings"] = len(state["findings"])
    counts["models"] = len(state.get("models") or [])
    counts["claimable"] = sum(1 for m in state.get("models", []) if m.get("claimable"))
    counts["positive_findings"] = sum(
        1 for f in state["findings"]
        if f.get("kind") in {"claim", "positive", "replication", "operational"}
    )
    counts["caveated_findings"] = sum(1 for f in state["findings"] if f.get("kind") == "caveat")
    counts["null_findings"] = sum(1 for f in state["findings"] if f.get("kind") in {"null", "negative"})
    state["counts"] = counts
    note = "Static build joined cockpit state with H1 trust index, latest HAB/bacteria/tsunami/semantic-shadow claim cards, public data assets, and report evidence."
    actions = list(state.get("next_actions") or [])
    if note not in actions:
        actions.insert(0, note)
    state["next_actions"] = actions
    return state


def _base_state():
    try:
        return _public(_get("/api/state"))
    except Exception as exc:
        ps = _local_json("reports/project_status/project_status.json") or {}
        models = ((ps.get("models") or {}).get("models") or [])
        return {
            "findings": [],
            "models": models,
            "gates": ps.get("gates") or [],
            "data_assets": ps.get("data_assets") or [],
            "next_actions": [f"cockpit API unavailable during build ({exc}); using project_status + report corpus"],
            "overall_status": ps.get("overall_status") or "UNKNOWN",
            "snapshot_generated": ps.get("generated_at"),
            "counts": {
                "models": len(models),
                "claimable": sum(1 for m in models if m.get("claimable")),
                "lake_datasets": 0,
                "shadow_datasets": 0,
            },
            "inventory": {"lake": {"datasets": []}, "shadow": {"datasets": []}, "built_iso": None},
            "git": ps.get("git_status_summary") or {},
        }


def _read_local_evidence(rel):
    path = SOURCE_ROOT / rel
    if not path.exists():
        return None
    try:
        if path.suffix.lower() == ".json":
            return json.loads(path.read_text(encoding="utf-8"))
        if path.suffix.lower() == ".md":
            return {"text": path.read_text(encoding="utf-8", errors="replace")[:20000]}
    except Exception:
        return None
    return None


def _tracked_files():
    try:
        r = subprocess.run(
            ["git", "-C", str(SOURCE_ROOT), "ls-files"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except Exception:
        return []
    return [p.strip().replace("\\", "/") for p in r.stdout.splitlines() if p.strip()]


def _project_doc_priority(rel):
    root_docs = {
        "README.md": 1000,
        "DATA.md": 980,
        "MODEL_SUITE.md": 960,
        "PRODUCTION_READINESS.md": 940,
        "MBAL_PRODUCTION_LAKEHOUSE_CONTRACTS.md": 930,
        "reports/semantic_lakehouse/semantic_shadow_discovery.md": 928,
        "reports/data_fetch/foundation_models/ACQUISITION.md": 927,
        "reports/hab/cyano/cyano_onset_claim_card_redteam.md": 926,
        "reports/hab/da_forecast.md": 925,
        "REMOTE_RESEARCH_CONTRACT.md": 900,
        "docs/VALUE_GATE.md": 890,
        "docs/open_source_readiness.md": 870,
        "docs/PUBLISH_RUNBOOK.md": 860,
        "research/model_lab/model_registry.yaml": 850,
        "research/bacteria/reproduce/REPRODUCE.md": 840,
        "research/bacteria/reproduce/PAPER_DRAFT.md": 835,
        "research/bacteria/reproduce/FROZEN_HEADLINE.json": 830,
        "reports/research_loop/summary.md": 735,
    }
    if rel in root_docs:
        return root_docs[rel]
    if rel.startswith("docs/findings/") and rel.endswith(".md"):
        return 760
    if rel.startswith("reports/science_breakthrough/") and rel.endswith((".md", ".json")):
        return 740
    if rel.startswith("reports/meta_synthesis/") and rel.endswith(".md"):
        return 730
    if rel.startswith("reports/hab/") and rel.endswith((".md", ".json")):
        return 725
    if rel.startswith("reports/bacteria/") and rel.endswith((".md", ".json")):
        return 715
    if rel.startswith("reports/tsunami/") and rel.endswith((".md", ".json")):
        return 710
    if rel.startswith("reports/data_fetch/") and rel.endswith(".md"):
        return 705
    if rel.startswith("reports/semantic_lakehouse/") and rel.endswith(".md"):
        return 700
    if rel.startswith("reports/experiment_program/H1/") and rel.endswith((".md", ".json")):
        return 720
    if rel.startswith("research/cross_dataset/") and rel.endswith(".md"):
        return 700
    if rel.startswith("research/bacteria/reproduce/expected/") and rel.endswith((".md", ".json")):
        return 690
    name = rel.rsplit("/", 1)[-1].upper()
    if rel.startswith("reports/") and rel.endswith(".md") and any(
        token in name for token in (
            "FINDINGS", "DIRECTION", "NEXT", "THESIS", "REVALIDATION",
            "BENCHMARK", "READINESS", "ACQUISITION", "DISCOVERY",
            "FORECAST", "REDTEAM", "CLAIM", "VALIDATION",
        )
    ):
        return 650
    if rel.startswith("docs/") and rel.endswith(".md") and any(
        token in name for token in ("PROGRAM", "PROTOCOL", "MODEL", "LAKEHOUSE", "RUNBOOK", "FINDINGS", "READINESS")
    ):
        return 620
    return 0


def _is_project_doc(rel):
    blocked = (
        ".github/",
        "archive/",
        "docs/agent_brain/",
        "docs/agent_pair/",
        "ops/overnight/",
        "reports/findings_campaign/",
    )
    blocked_names = ("CLAUDE.md", "GEMINI.md", "SECURITY.md", "COORDINATION.md")
    if rel.startswith(blocked) or rel.rsplit("/", 1)[-1] in blocked_names:
        return False
    if any(part in rel.lower() for part in ("decision_log", "learnings.jsonl", "intents.json", "private", "secret")):
        return False
    if not rel.endswith((".md", ".json", ".yaml", ".yml")):
        return False
    return _project_doc_priority(rel) > 0


def _doc_title(rel, text):
    for line in text.splitlines()[:40]:
        m = re.match(r"^\s{0,3}#{1,3}\s+(.+?)\s*$", line)
        if m:
            return m.group(1)[:120]
    return rel.rsplit("/", 1)[-1]


def _split_doc(text, max_chars=5200, max_chunks=3):
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    chunks = []
    starts = [m.start() for m in re.finditer(r"\n#{1,3}\s+", "\n" + text)]
    if len(starts) > 1:
        spans = []
        for i, start in enumerate(starts):
            real_start = max(0, start - 1)
            end = starts[i + 1] - 1 if i + 1 < len(starts) else len(text)
            spans.append(text[real_start:end].strip())
        for span in spans:
            if len(span) <= max_chars:
                chunks.append(span)
            else:
                for i in range(0, len(span), max_chars):
                    chunks.append(span[i:i + max_chars])
            if len(chunks) >= max_chunks:
                return chunks[:max_chunks]
    if not chunks:
        for i in range(0, min(len(text), max_chars * max_chunks), max_chars):
            chunks.append(text[i:i + max_chars])
    return [c for c in chunks if c.strip()][:max_chunks]


def _build_project_docs():
    latest_public_docs = {
        "reports/semantic_lakehouse/semantic_shadow_discovery.md",
        "reports/hab/cyano/cyano_onset_claim_card_redteam.md",
    }
    docs = []
    candidates = set(_tracked_files())
    candidates.update(
        rel for rel in latest_public_docs
        if (SOURCE_ROOT / rel).exists() and _project_doc_priority(rel) > 0
    )
    files = sorted(
        [p for p in candidates if _is_project_doc(p) or p in latest_public_docs],
        key=lambda p: (-_project_doc_priority(p), p),
    )
    for rel in files:
        path = SOURCE_ROOT / rel
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        # These chunks are for public Q&A, not source reproduction. Keep them compact.
        text = re.sub(r"\n{3,}", "\n\n", text)
        title = _doc_title(rel, text)
        for idx, chunk in enumerate(_split_doc(text)):
            docs.append({
                "path": rel,
                "title": title,
                "chunk": idx + 1,
                "priority": _project_doc_priority(rel),
                "text": chunk,
            })
            if len(docs) >= 120:
                return docs
    return docs


def _build_lab_memory():
    """Curated, public-safe 'mission / how we work / what we rejected / open questions' cards.

    Scope discipline (after critic review):
      * This layer carries NARRATIVE and PRINCIPLE only — mission, working philosophy,
        deliberately-rejected ideas, and open questions. It deliberately carries NO
        independent metrics or empirical result claims: the findings/models sections are
        the single source of truth for "what we measured", so lab_memory cannot drift from
        or overclaim past the gated evidence.
      * Hand-vetted literal (NOT an auto-dump of docs/agent_brain/*), matching this
        builder's existing curated-finding convention.
      * `basis` is a COARSE provenance label, never an internal file path, so public
        data.json does not expose the structure of excluded internal docs.
    """
    cards = [
        {"id": "mission_unified_model", "kind": "mission",
         "claim": "Build one shared model of the coastal ocean — how its physics, weather, runoff, waves, tides, and life connect — so it can warn of rare water-quality events before they are measured directly.",
         "basis": "lab mission"},
        {"id": "mission_higher_order", "kind": "mission",
         "claim": "Look for second- and third-order signal combinations across moorings, satellite, flow and weather data, rather than generic regression on raw sensors.",
         "basis": "lab mission"},
        {"id": "done_reframe_surprise", "kind": "done",
         "claim": "We reframed the bacteria problem around clean-to-dirty surprises, so the science targets events people cannot infer from yesterday's status alone. (See the Findings tab for the measured result.)",
         "basis": "research direction"},
        {"id": "learn_transfer_logged", "kind": "learning",
         "claim": "When we test whether a model carries over to a new region, we log the outcome with its baseline and permutation-null checks rather than asserting success — the regional-transfer story has been softened by tougher held-out audits.",
         "basis": "research direction"},
        {"id": "learn_driver_null", "kind": "learning",
         "claim": "Across many tested inputs, external rainfall is the main clear driver of beach-bacteria risk; most other candidate drivers — including waves and tide level — wash out against a full baseline. We call this the driver-null pattern.",
         "basis": "experiment record"},
        {"id": "learn_scoped_claims", "kind": "learning",
         "claim": "The current strongest new claims are intentionally scoped: domoic acid is promoted on prior-clean toxic-onset rows, cyanobacteria is promoted as an onset-timing prior, and warning-screen work is not treated as a deployed alert system.",
         "basis": "claim-card record"},
        {"id": "learn_evidence_is_combination", "kind": "learning",
         "claim": "The strongest evidence is a combination — honest baselines, held-out tests, calibration, and failure analysis — not any single score.",
         "basis": "operating principles"},
        {"id": "learn_nulls_on_record", "kind": "learning",
         "claim": "Null results are kept on the record beside the wins, so the next experiment starts from the truth instead of a forgotten dead end.",
         "basis": "operating principles"},
        {"id": "learn_breadth_over_depth", "kind": "learning",
         "claim": "Broad station coverage tends to matter more than long history at a single site when the goal is a statewide model.",
         "basis": "experiment record"},
        {"id": "data_foundation_model_inputs", "kind": "data",
         "claim": "The latest data acquisition work separates usable surface inputs from blocked global rollout inputs: MERRA-2 surface fields are available through Earth Engine for station-level features, Sentinel-3 chlorophyll source data is present locally, and full global Prithvi pressure-level rollout still needs the NASA GES DISC agreement.",
         "basis": "data acquisition record"},
        {"id": "learn_semantic_shadow_leads", "kind": "learning",
         "claim": "The semantic-shadow lakehouse is now useful as a question generator across bacteria, HAB, and whale targets, but its correlations and temporal-holdout lifts remain discovery leads until a reproduction job turns them into evidence.",
         "basis": "discovery-run record"},
        {"id": "reject_foundation_models", "kind": "rejected",
         "claim": "Time-series foundation models repeatedly failed to beat a simple persistence baseline on the mooring data, so they were pruned rather than promoted.",
         "basis": "rejected-ideas record"},
        {"id": "reject_auto_self_repair", "kind": "rejected",
         "claim": "Automatic model self-repair when training loss spikes was rejected: a loss spike can be a real ocean anomaly, so the system may flag it but must not silently rewrite itself.",
         "basis": "rejected-ideas record"},
        {"id": "open_prospective_scoring", "kind": "open_question",
         "claim": "The open frontier is prospective scoring — testing new observations after the freeze date before calling a retrospective result final.",
         "basis": "open questions"},
        {"id": "open_driver_aware_neural", "kind": "open_question",
         "claim": "Can driver-aware neural models overcome the modest or negative skill seen in early multi-driver forecasting runs?",
         "basis": "open questions"},
        {"id": "open_foundation_rollouts", "kind": "open_question",
         "claim": "Can the acquired Prithvi and Granite source adapters create reusable coastal features that beat existing tabular baselines, once they are evaluated under the same held-out gates as the current registry?",
         "basis": "open questions"},
    ]
    return cards


def _build_vocabulary():
    """Curated concept matrix: what KIND of thing each artifact is.

    Teaches the distinction the registry depends on — e.g. an unfitted XGBoost or
    neural net is a *learner*, not a model, until it is fitted, evaluated and
    registered; a baseline is a comparator, not a discovery; a histogram is a lens.
    Examples are real names drawn from the registry / signal catalog, chosen to be
    public-safe (no external-product criticism, no licensing/business notes).
    """
    return [
        {"key": "source_dataset", "label": "Source dataset",
         "what": "Raw, joinable evidence we collect — coverage, rows, recency, provenance. Not a model.",
         "examples": ["ASOS rainfall", "NOAA CO-OPS tide & water level", "CalHABMAP domoic-acid piers", "USGS river discharge", "MBARI M1/M2 moorings"]},
        {"key": "target", "label": "Target / label",
         "what": "The thing being explained or predicted — it defines the scientific question.",
         "examples": ["bacteria exceedance (AB411 > 104 MPN/100 mL)", "domoic-acid pier exceedance", "hypoxia lead (DO ≤ 2 mg/L, 7–14 d)", "renewal onset", "cyano bloom onset"]},
        {"key": "signal", "label": "Signal / feature",
         "what": "A candidate explanatory input, tested through gates until it earns KEEP, WASH, or REJECT.",
         "examples": ["station lab history (primary)", "rainfall (keep)", "river discharge (keep)", "spring–neap tide range (keep, pooled)", "pseudo-nitzschia (keep, for domoic acid)"]},
        {"key": "representation", "label": "Representation",
         "what": "A transformed feature space used to expose hidden structure or cross-domain transfer.",
         "examples": ["thermal stratification (ΔT surface−bottom)", "non-dimensional π-groups", "station-memory features", "harmonic tide range"]},
        {"key": "diagnostic", "label": "Diagnostic / lens",
         "what": "A way to look for structure. A discovery aid, not a model.",
         "examples": ["leave-one-beach-out test", "permutation-null + bootstrap CI", "confusion matrix", "residual maps", "spatial synchrony fields"]},
        {"key": "learner", "label": "Learner / discovery engine",
         "what": "An algorithm used to search for relationships. Before it is fitted to data, this is NOT a trained model.",
         "examples": ["HGBT", "XGBoost", "CatBoost", "LightGBM", "neural net (MLP)", "TabPFN", "CUSUM detector"]},
        {"key": "trained_model", "label": "Trained model card",
         "what": "A fitted, evaluated, registered learner plus its evidence and status. Can be claimable, benchmark, or null.",
         "examples": ["bacteria_hgbt_isotonic (claimable)", "da_forecast_hgbt (claimable)", "bacteria_catboost (benchmark)", "bacteria_mlp_plr (benchmark)"]},
        {"key": "baseline", "label": "Baseline / null",
         "what": "An honest comparator that prevents fake discoveries. A baseline is not a discovery claim.",
         "examples": ["AB411 rain rule", "Virtual-Beach MLR", "station memory", "seasonal-naive / persistence", "permutation nulls"]},
        {"key": "finding", "label": "Finding",
         "what": "A promoted scientific statement — only after leakage, baseline, and bootstrap/null checks pass.",
         "examples": ["driver-null law (rainfall is the main clear external driver)", "spring–neap lift is real but does not generalize spatially", "soil moisture is redundant with discharge", "harmful-algae transfer did not generalize (null)"]},
    ]


def _build_targets():
    """Every hazard/target the lab has actually worked on — the multi-hazard breadth that
    the bacteria-centric `findings` list alone does not convey. Curated from the signal
    catalog's target ledger (signals/catalog.yaml meta.targets) + finding reports, with
    HONEST status (the failures and nulls are kept on the record, not hidden).
    status: Supported | Caveated | Null | Exploratory.
    """
    return [
        {"id": "bacteria_exceedance", "label": "Beach bacteria exceedance", "status": "Supported",
         "question": "Will a beach exceed the AB411 enterococcus standard (>104 MPN/100 mL)?",
         "plain": "Our strongest result: a statewide nowcast at AP 0.5099 / ROC-AUC 0.868, calibrated, independently audited, out-of-time stable 2022–2026, and deploy-ready in 8 of 9 counties — beating station-memory, seasonal-naive, and the AB411 rain rule."},
        {"id": "domoic_acid", "label": "Domoic-acid shellfish toxin", "status": "Supported",
         "question": "Will a pier's next sample exceed particulate domoic acid 500 ng/L?",
         "plain": "Scoped positive: the promoted claim is the prior-clean toxic-onset slice, where the updated model reaches AP 0.1347 versus seasonal-naive AP 0.0259, margin +0.1089 with station-clustered CI [0.0566, 0.2078] and permutation-p 0.002. The pooled model also ranks, but the headline is the onset gate; the older C-HARM framing remains rejected as a broken baseline."},
        {"id": "cross_country_transfer", "label": "Cross-country zero-shot transfer", "status": "Caveated",
         "question": "Can a label-free model rank bacteria exceedance in a country with no local training labels?",
         "plain": "Real but modest: non-dimensional physics groups transfer US↔Ireland zero-shot at AP ~0.136 (~1.9× over a matched-prevalence baseline) and to Australia, but the regional-transfer story softens under tougher held-out audits."},
        {"id": "hypoxia_lead", "label": "Coastal hypoxia (low oxygen) lead", "status": "Supported",
         "question": "Will bottom water go hypoxic (DO ≤ 2 mg/L) 7–14 days ahead?",
         "plain": "The driver-null's first clean escape: thermal stratification adds +0.021 AP of real lead-skill over persistence+season and survives a permutation null (p=0.005); wind, salinity, and chlorophyll wash out."},
        {"id": "renewal_onset", "label": "Fjord oxygen-renewal onset", "status": "Supported",
         "question": "Will a dense-water renewal re-oxygenate an anoxic fjord within ~3 weeks?",
         "plain": "Deep-water density gives +0.08 AP of renewal-onset lead-skill that is year-robust at ~21-day lead — the slow-driver principle partially transfers to a second basin once the carrier and objective match the physics."},
        {"id": "cyano_onset", "label": "Lake cyanobacteria bloom onset", "status": "Supported",
         "question": "When will a lake's cyanobacteria bloom season start?",
         "plain": "Claimable but scoped: latitude/quadratic geography is a real cold-start bloom-onset timing prior under held-out state, lat-band, year, and red-team checks. The claim is onset timing and sampling-start prior, not abundance prediction; photoperiod-only is not the anchor, and daily meteorology/interlingua transfer remains null."},
        {"id": "whale_mortality", "label": "Whale (cetacean) mortality", "status": "Null",
         "question": "Do prior ocean drivers predict California whale mortality beyond seasonal climatology?",
         "plain": "We reconstructed the 2019–2023 gray-whale die-off from citizen-science (iNaturalist) records and tested prey-collapse, climate, and domoic-acid pathways. Honest result: the prey-collapse link (krill, rockfish) is SUGGESTIVE but NOT robust — it fails a 17-year cluster bootstrap — and the domoic-acid→death link does not hold. The ecology/mortality lane is largely signal-poor; we keep it on the record as an open frontier, not a win."},
        {"id": "sea_star_kelp_ecosystem", "label": "Sea-star wasting / kelp ecosystem", "status": "Exploratory",
         "question": "Can ocean drivers explain sea-star wasting and kelp-forest ecosystem shifts?",
         "plain": "An exploratory cross-dataset lane in the ecology/mortality family; like whale mortality, the early ecology correlations have been hard to make survive strict cross-validation."},
        {"id": "sfbay_compound_flood", "label": "SF Bay compound coastal flood", "status": "Null",
         "question": "Will SF Bay see an extreme still-water-level (coastal-flood) day, and is it compound (rain + tide/surge)?",
         "plain": "A driver-lift wash: the free harmonic tide table alone is the best naive predictor (AP 0.223 / AUROC 0.945); adding weather/runoff drivers (AP 0.294) is NOT CI-separated from tide-only, so there is no demonstrated driver lift yet."},
        {"id": "ddpcr_culture_safe", "label": "ddPCR-positive culture-safe days", "status": "Null",
         "question": "Are same-day ddPCR positives among culture-safe beach-days a real early warning target?",
         "plain": "No clean target yet. In San Diego, ddPCR-positive/culture-safe days have raw forward exceedance lift, but that lift largely disappears after full culture-history controls: ddPCR adds only +0.0147 AUC with CI crossing zero. Treat the raw effect as persistence/history structure, not a promoted molecular early-warning claim."},
        {"id": "tsunami_halfduration_screen", "label": "Tsunami half-duration warning screen", "status": "Caveated",
         "question": "Can rupture half-duration reduce tsunami false alarms at fixed recall?",
         "plain": "Promising screening value, not a deployment claim: adding rupture half-duration to magnitude+depth reduces false alarms at 80% and 90% fixed recall in leave-one-region-out NOAA tsunami labels, but not at 95% recall. This belongs in a warning-screen research lane, not public alert automation."},
        {"id": "semantic_shadow_lakehouse", "label": "Semantic-shadow discovery lakehouse", "status": "Exploratory", "kind": "method",
         "question": "Can one lakehouse generate better cross-domain research questions from bacteria, HAB, and ecology data?",
         "plain": "Yes as a discovery engine: the latest refresh scanned bacteria, HAB, and whale targets and produced model-lift and lead-lag correlation tables. No correlation is promoted as a claim until it survives an independent reproduction/null-control run."},
        {"id": "foundation_model_inputs", "label": "Foundation-model coastal inputs", "status": "Exploratory", "kind": "data",
         "question": "Which foundation-model source data is ready for coastal-hazard experiments?",
         "plain": "Source adapters are now separated by readiness: Sentinel-3 OLCI/SLSTR chlorophyll data is present locally for Granite-Ocean style work, and MERRA-2 surface variables are available through Earth Engine for station-level Prithvi inputs. Full global Prithvi-WxC pressure-level rollouts still need NASA GES DISC authorization."},
        {"id": "interlingua", "label": "Interlingua — a shared physics language", "status": "Caveated", "kind": "method",
         "question": "Can different regions' coastal data be re-encoded onto one physics 'language' that transfers without local labels?",
         "plain": "Caveated yes: hand-built non-dimensional π-groups remain the cleanest label-free cross-region bacteria language, but the newer audits define hard boundaries. The same representation does not carry annual EU bathing-water classes past calendar+memory, and daily freshwater cyano π features do not beat calendar or persistence.",
         "detail": "We re-encoded coastal data onto a label-free physics manifold and raced learned encoders against hand-built π-groups. The π-groups won the US/Ireland/Australia bacteria transfer lane and survived permutation-null checks, but the later boundary tests matter: annual EU class resolution and freshwater cyano daily-met transfer do not promote. Interlingua is a useful coordinate system per signal family, not one universal vector across hazards."},
    ]


def _build_signals():
    """The full signal catalog — every candidate input/feature/method tested, with its
    HONEST per-target verdict (PRIMARY / KEEP / WASH / REJECT / UNTESTED). Read live from
    signals/catalog.yaml so it stays in sync with the real ledger. Science-only fields
    (name, what, modality, verdicts) — no evidence paths, no internal file references.
    """
    try:
        import yaml
        cat = yaml.safe_load((SOURCE_ROOT / "signals" / "catalog.yaml").read_text(encoding="utf-8"))
    except Exception:
        return []

    def find_signals(o):
        if isinstance(o, dict):
            if isinstance(o.get("signals"), list):
                return o["signals"]
            for v in o.values():
                r = find_signals(v)
                if r is not None:
                    return r
        elif isinstance(o, list):
            for it in o:
                r = find_signals(it)
                if r is not None:
                    return r
        return None

    sigs = find_signals(cat) or []
    rank = {"PRIMARY": 0, "KEEP": 1, "KEEP-research": 2, "UNTESTED": 3, "WASH": 4, "REJECT": 5}
    out = []
    for s in sigs:
        if not isinstance(s, dict) or not s.get("name"):
            continue
        pv = s.get("predictive_value") or {}
        verdicts = []
        if isinstance(pv, dict):
            for tgt, info in pv.items():
                st = info.get("status") if isinstance(info, dict) else info
                if st:
                    verdicts.append({"target": tgt, "status": str(st)})
        what = str(s.get("captures") or s.get("does") or "").strip()
        if len(what) > 170:
            what = what[:169] + "…"
        best = min([rank.get(v["status"], 9) for v in verdicts], default=9)
        out.append({"name": s["name"], "what": what, "modality": str(s.get("modality") or ""),
                    "verdicts": verdicts, "_r": best})
    out.sort(key=lambda x: (x["_r"], x["name"]))
    for o in out:
        o.pop("_r", None)
    return out


def main():
    state = _augment_state(_base_state())
    state["lab_memory"] = _build_lab_memory()
    state["vocabulary"] = _build_vocabulary()
    state["targets"] = _build_targets()
    state["signals"] = _build_signals()
    project_docs = _build_project_docs()
    counts = dict(state.get("counts") or {})
    counts["targets"] = len(state["targets"])
    counts["signals"] = len(state["signals"])
    counts["project_docs"] = len(project_docs)
    state["counts"] = counts

    # bundle the evidence JSON each finding/model points at (so drill-downs work offline)
    evidence = {}
    paths = set()
    for f in state.get("findings", []):
        if f.get("evidence"):
            paths.add(f["evidence"])
    for m in state.get("models", []):
        if m.get("evidence"):
            paths.add(m["evidence"])
    for p in paths:
        try:
            local = _read_local_evidence(p)
            if local is not None:
                evidence[p] = local
                continue
            j = _get("/api/finding?evidence=" + urllib.parse.quote(p))
            if j.get("data") is not None:
                evidence[p] = j["data"]
        except Exception:
            pass

    snap = {
        "built_iso": datetime.now(timezone.utc).isoformat(),
        "built_pacific": time.strftime("%Y-%m-%d %H:%M %Z"),
        "state": state,
        "evidence": evidence,
        "project_docs": project_docs,
    }
    out = HERE / "data.json"
    text = json.dumps(_json_safe(snap), separators=(",", ":"), allow_nan=False)
    # final scrub: strip any absolute local prefixes that survived inside strings
    user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    # strip absolute local prefixes (generic user segment) so no machine paths/usernames leak
    text = re.sub(r"C:[\\\\/]+Users[\\\\/]+[^\\\\/\"]+[\\\\/]+AI-Machine[\\\\/]+(?:projects|_deagent)?[\\\\/]+", "", text)
    text = re.sub(r"C:[\\\\/]+Users[\\\\/]+[^\\\\/\"]+[\\\\/]+", "", text)
    text = re.sub(r"/Users/[^/\\\"]+/", "/[local-path-redacted]/", text)
    text = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
                  "[private-key-redacted]", text, flags=re.I | re.S)
    text = re.sub(r"\b(?:sk|ghp|github_pat|glpat|xox[baprs])[-_][-A-Za-z0-9_]{16,}\b",
                  "[token-redacted]", text)
    text = re.sub(r"\bAIza[0-9A-Za-z_-]{20,}\b", "[api-key-redacted]", text)
    text = re.sub(
        r"([?&](?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|secret|key)=)[^&\\\"']+",
        r"\\1[redacted]",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"((?:api[_-]?key|access[_-]?token|refresh[_-]?token|secret|password|credential)\\s*[:=]\\s*)[\\\"]?[^\\\",}\\s]+[\\\"]?",
        r"\\1[redacted]",
        text,
        flags=re.I,
    )
    if user:
        text = text.replace(user, "lab")
    out.write_text(text, encoding="utf-8")
    kb = round(out.stat().st_size / 1024, 1)
    print(f"wrote {out}  ({kb} KB)  · findings={len(state.get('findings',[]))} "
          f"models={len(state.get('models',[]))} evidence_files={len(evidence)}")


if __name__ == "__main__":
    import urllib.parse  # noqa: E402
    main()
