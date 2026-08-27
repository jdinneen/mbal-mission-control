#!/usr/bin/env python3
"""Build the public aggregate proof for the retrospective DA assay-budget explorer.

The public file contains only aggregate evaluation results. Raw sample rows and fitted
models remain on the Lab. The builder is deliberately bound to the 2026-08-24 recreation
receipts so a changed input fails instead of silently changing the public page.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pandas as pd


SITE = Path(__file__).resolve().parents[1]
LAB = SITE.parent
LANE = LAB / "reports/hab/da_triage_recreation_20260824"
OUT = SITE / "sample-triage-proof.json"
BUDGETS = range(2, 41)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def top_k(frame: pd.DataFrame, score: str, fraction: float, groups: list[str] | None) -> tuple[int, int]:
    def select(group: pd.DataFrame) -> tuple[int, int]:
        n = math.ceil(fraction * len(group))
        chosen = group.nlargest(n, score, keep="first")
        return int(chosen["target"].sum()), int(len(chosen))

    if groups is None:
        return select(frame)
    caught = assays = 0
    for _, group in frame.groupby(groups, sort=True):
        group_caught, group_assays = select(group)
        caught += group_caught
        assays += group_assays
    return caught, assays


def slim_estimator(record: dict) -> dict:
    return {
        "candidate_ap": record["candidate_ap"],
        "fortified_baseline_ap": record["fortified_baseline_ap"],
        "margin_ap": record["margin_ap"],
        "ci95_pier_year": record["ci95_pier_year"],
        "score_swap_p": record["score_swap_null"]["p_value"],
        "top10_budget_rows": record["top10_budget_rows"],
        "top10_candidate_capture": record["top10_candidate_capture"],
        "top10_baseline_capture": record["top10_baseline_capture"],
        "top10_observed_group_sum_capture": record["top10_raw_count_capture"],
        "piers_scored": record["by_pier"]["scored_units"],
        "piers_candidate_beats_baseline": record["by_pier"]["units_candidate_beats_baseline"],
        "years_scored": record["by_year"]["scored_units"],
        "years_candidate_beats_baseline": record["by_year"]["units_candidate_beats_baseline"],
        "worst_year_margin_ap": record["by_year"]["delta_min"],
        "positive_control_margin_ap": record["positive_control"]["margin_ap"],
        "positive_control_passed": record["positive_control"]["fires"],
        "max_destructive_control_recovery_fraction": record["max_destructive_control_recovery_fraction"],
        "passes_recorded_lab_only_gate": record["passes_frozen_lab_only_gate"],
    }


def main() -> int:
    result_path = LANE / "result.json"
    critic_path = LANE / "CRITIC_RECEIPT.json"
    claim_card_path = LANE / "claim_card.json"
    panel_path = LANE / "panel.parquet"
    gbm_path = LANE / "scored_rows_gbm.parquet"
    logistic_path = LANE / "scored_rows_logistic.parquet"
    source_path = LAB / "lakehouse/silver/external_curated/calhabmap_domoic_acid/calhabmap_domoic_acid.parquet"

    result = json.loads(result_path.read_text(encoding="utf-8"))
    critic = json.loads(critic_path.read_text(encoding="utf-8"))
    claim_card = json.loads(claim_card_path.read_text(encoding="utf-8"))
    expected = critic["evidence_sha256"]
    bindings = {
        "result_json": result_path,
        "claim_card_json": claim_card_path,
        "panel": panel_path,
        "scored_rows_gbm": gbm_path,
        "scored_rows_logistic": logistic_path,
        "source": source_path,
    }
    actual_hashes = {name: sha256(path) for name, path in bindings.items()}
    mismatches = {
        name: {"expected": expected[name], "actual": digest}
        for name, digest in actual_hashes.items()
        if digest != expected[name]
    }
    if mismatches:
        raise SystemExit("frozen evidence hash mismatch: " + json.dumps(mismatches, indent=2))

    panel = pd.read_parquet(panel_path, columns=["station", "date", "year", "pn_total", "pDA"])
    source = pd.read_parquet(
        source_path,
        columns=[
            "station",
            "pDA",
            "Pseudo_nitzschia_delicatissima_group",
            "Pseudo_nitzschia_seriata_group",
        ],
    )
    gbm = pd.read_parquet(gbm_path)
    logistic = pd.read_parquet(logistic_path)
    if not (len(panel) == len(gbm) == len(logistic) == result["n_rows"]):
        raise SystemExit("panel and score row counts do not match the result receipt")
    for scored in (gbm, logistic):
        if not panel["station"].astype(str).equals(scored["station"].astype(str)):
            raise SystemExit("station order differs between panel and scored rows")
        if not pd.to_datetime(panel["date"], utc=True).equals(pd.to_datetime(scored["date"], utc=True)):
            raise SystemExit("date order differs between panel and scored rows")

    frame = gbm[["station", "date", "year", "station_year", "target"]].copy()
    frame["pn_observed_sum"] = panel["pn_total"].to_numpy()
    frame["gbm_model"] = gbm["score_candidate"].to_numpy()
    frame["gbm_baseline"] = gbm["score_fortified_baseline"].to_numpy()
    frame["logistic_model"] = logistic["score_candidate"].to_numpy()
    frame["logistic_baseline"] = logistic["score_fortified_baseline"].to_numpy()
    if frame["pn_observed_sum"].isna().any():
        raise SystemExit("eligible panel unexpectedly contains a missing observed-group sum")

    ordered = frame.sort_values(["station", "date"], kind="stable").copy()
    ordered["date"] = pd.to_datetime(ordered["date"], utc=True)
    prior_target = ordered.groupby("station", sort=False)["target"].shift()
    prior_date = ordered.groupby("station", sort=False)["date"].shift()
    gap_days = (ordered["date"] - prior_date).dt.total_seconds() / 86400
    recorded_run_starts = ordered["target"].eq(1) & (
        prior_target.ne(1) | prior_target.isna() | gap_days.gt(21)
    )
    recorded_run_count = int(recorded_run_starts.sum())
    positive = ordered.loc[ordered["target"].eq(1), ["station", "date"]].copy()
    positive_gap_days = positive.groupby("station", sort=False)["date"].diff().dt.total_seconds() / 86400
    gap_separated_positive_clusters = int((positive_gap_days.isna() | positive_gap_days.gt(21)).sum())
    if recorded_run_count != result["independent_episode_count"]:
        raise SystemExit("recorded positive-run count no longer matches the saved result")
    if gap_separated_positive_clusters != 79:
        raise SystemExit("gap-separated positive-cluster count no longer reproduces")

    source_toxin = source.loc[source["pDA"].notna()].copy()
    source_strict = source_toxin["pDA"].gt(0.5)
    excluded_toxin_stations = sorted(
        set(source_toxin["station"].astype(str).unique()) - set(frame["station"].astype(str).unique())
    )

    series = ("gbm_model", "logistic_model", "gbm_baseline", "logistic_baseline", "pn_observed_sum")
    curves: dict[str, dict[str, dict[str, int]]] = {}
    for frame_name, groups in (("pooled_archive", None), ("within_pier_year", ["station", "year"])):
        rows: dict[str, dict[str, int]] = {}
        for percentage in BUDGETS:
            fraction = percentage / 100
            entry: dict[str, int] = {}
            assay_counts = set()
            for key in series:
                caught, assays = top_k(frame, key, fraction, groups)
                entry[key] = caught
                assay_counts.add(assays)
            if len(assay_counts) != 1:
                raise SystemExit(f"assay budgets diverged at {frame_name} {percentage}%")
            entry["assays"] = assay_counts.pop()
            rows[str(percentage)] = entry
        curves[frame_name] = rows

    pooled_ten = curves["pooled_archive"]["10"]
    if pooled_ten["gbm_model"] != result["estimators"]["gbm"]["top10_candidate_capture"]:
        raise SystemExit("10% gradient-boosting capture no longer reproduces")
    if pooled_ten["gbm_baseline"] != result["estimators"]["gbm"]["top10_baseline_capture"]:
        raise SystemExit("10% fortified-baseline capture no longer reproduces")
    if pooled_ten["pn_observed_sum"] != result["estimators"]["gbm"]["top10_raw_count_capture"]:
        raise SystemExit("10% observed-group-sum capture no longer reproduces")

    payload = {
        "schema_version": 2,
        "artifact": "SampleNext retrospective assay-budget explorer",
        "evidence_date": result["run_date"],
        "public_status": "GATED / WALKED-BACK — retrospective research evidence only",
        "release_ready": result["release_ready"],
        "release_blockers": [
            "gradient-boosting planted-signal control failed the recorded minimum; both model types were required to pass",
            "pre-result experiment contract is missing",
            "experiment-admission receipt is missing",
            "input-availability proof is missing",
            "key-shuffle proof is missing",
            "full-refit permutation proof is missing",
            "execution-completion proof is missing",
            "source has not cleared the Lab's source review",
            "historical hypotheses attempted are not reported",
            "same-sample decision timing and assay budget have not been validated prospectively",
        ],
        "release_grade_status": "NEEDS-DATA",
        "scope": {
            "rows": result["n_rows"],
            "elevated_rows": result["positive_rows"],
            "prevalence": result["prevalence"],
            "recorded_elevated_runs": recorded_run_count,
            "recorded_run_rule": "new run after any intervening non-elevated sample or a gap greater than 21 days at the same pier",
            "gap_separated_positive_clusters": gap_separated_positive_clusters,
            "positive_cluster_rule": "new within-pier cluster only when consecutive elevated samples are more than 21 days apart",
            "independence_status": "NOT ESTABLISHED — 79 within-pier gap-separated positive clusters are reported without claiming cross-pier independence",
            "piers": sorted(frame["station"].astype(str).unique().tolist()),
            "pier_count": int(frame["station"].nunique()),
            "date_min": pd.to_datetime(frame["date"], utc=True).min().date().isoformat(),
            "date_max": pd.to_datetime(frame["date"], utc=True).max().date().isoformat(),
            "elevated_definition": "particulate domoic acid strictly greater than 0.5 micrograms per litre",
            "official_reference_definition": "C-HARM's published product definition is at or above 0.5 micrograms per litre",
            "eligible_rows_exactly_on_boundary": int((panel["pDA"] == 0.5).sum()),
            "boundary_note": "The recorded Lab analysis used a strict greater-than comparator; it is not identical to the official at-or-above comparator.",
            "eligibility": "particulate domoic acid measured, at least one of two Pseudo-nitzschia groups reported, and at least 250 eligible rows at the pier",
            "source_toxin_rows": int(len(source_toxin)),
            "source_strict_elevated_rows": int(source_strict.sum()),
            "source_toxin_station_count": int(source_toxin["station"].nunique()),
            "excluded_toxin_rows": int(len(source_toxin) - len(frame)),
            "excluded_strict_elevated_rows": int(source_strict.sum() - frame["target"].sum()),
            "excluded_toxin_stations": excluded_toxin_stations,
            "held_out_unit": "whole pier",
            "cross_validation": "leave one pier out, train on the other six, repeat for all seven",
            "hypotheses_attempted": None,
            "hypotheses_note": "NOT REPORTED — blocks a positive release verdict",
            "calibration": {
                "status": "NOT VALIDATED FOR PROBABILITY USE",
                "candidate_ece": claim_card["candidate_ece"],
                "baseline_ece": claim_card["baseline_ece"],
                "note": "Expected calibration error was computed, but the release evidence does not establish the model scores as trustworthy probabilities; this artifact uses ranking only.",
            },
        },
        "feature_contract": {
            "candidate": "seasonal encodings, chlorophyll, water temperature, prior eligible-panel particulate domoic-acid value and elapsed days, and three transforms derived from two reported Pseudo-nitzschia morphogroup counts",
            "baseline": "the same inputs except the Pseudo-nitzschia count transforms",
            "pn_observed_sum_definition": "sum of the reported delicatissima- and seriata-group counts, using the one reported group when the other is missing; not a provider-native total field",
            "previous_toxin_definition": "previous eligible panel sample after the panel filters, not necessarily the immediately previous particulate domoic-acid assay in the full archive",
        },
        "estimators": {
            "gradient_boosting": slim_estimator(result["estimators"]["gbm"]),
            "logistic_regression": slim_estimator(result["estimators"]["logistic"]),
        },
        "curves": curves,
        "curve_definition": {
            "budgets": "2% through 40%",
            "selection": "ceil(budget fraction × rows), highest score first, stable first-row tie break",
            "pooled_archive": "one ranking over the full historical archive",
            "within_pier_year": "the target budget applied separately inside each pier and calendar year, with each group rounded up; the effective archive-wide share can exceed the target",
        },
        "proof": {
            "shared_file": "sample-triage-proof.json",
            "lab_only_result": "reports/hab/da_triage_recreation_20260824/result.json",
            "lab_only_critic": "reports/hab/da_triage_recreation_20260824/CRITIC_RECEIPT.json",
            "lab_only_report": "reports/hab/da_triage_recreation_20260824/REPORT.md",
            "input_sha256": actual_hashes,
            "fidelity_checks": {
                "frozen_hashes_match": True,
                "row_identity_match": True,
                "pooled_10_percent_matches_banked_result": True,
            },
        },
    }
    OUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "wrote": str(OUT),
                "pooled_10": curves["pooled_archive"]["10"],
                "within_pier_year_10": curves["within_pier_year"]["10"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
