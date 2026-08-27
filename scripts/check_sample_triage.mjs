#!/usr/bin/env node

import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(scriptDir, "..");
const htmlPath = path.join(root, "sample-triage.html");
const proofPath = path.join(root, "sample-triage-proof.json");
const imagePath = path.join(root, "sample-triage-og.png");

for (const required of [htmlPath, proofPath, imagePath]) {
  assert.ok(existsSync(required), `missing required Site file: ${path.basename(required)}`);
}

const html = readFileSync(htmlPath, "utf8");
const proof = JSON.parse(readFileSync(proofPath, "utf8"));
const close = (actual, expected, tolerance = 1e-12) =>
  assert.ok(Math.abs(actual - expected) <= tolerance, `${actual} != ${expected}`);

assert.equal(proof.schema_version, 2);
assert.equal(proof.release_ready, false);
assert.equal(proof.release_grade_status, "NEEDS-DATA");
assert.match(proof.public_status, /^GATED \/ WALKED-BACK/);
assert.equal(proof.scope.rows, 5328);
assert.equal(proof.scope.elevated_rows, 180);
assert.equal(proof.scope.recorded_elevated_runs, 102);
assert.equal(proof.scope.gap_separated_positive_clusters, 79);
assert.match(proof.scope.recorded_run_rule, /intervening non-elevated sample/);
assert.match(proof.scope.independence_status, /NOT ESTABLISHED/);
assert.equal(proof.scope.pier_count, 7);
assert.equal(proof.scope.date_max, "2026-06-22");
close(proof.scope.prevalence, 180 / 5328);
assert.match(proof.scope.elevated_definition, /strictly greater than 0\.5/);
assert.match(proof.scope.official_reference_definition, /at or above 0\.5/);
assert.equal(proof.scope.eligible_rows_exactly_on_boundary, 4);
assert.match(proof.scope.boundary_note, /not identical/);
assert.equal(proof.scope.source_toxin_rows, 5734);
assert.equal(proof.scope.source_strict_elevated_rows, 206);
assert.equal(proof.scope.source_toxin_station_count, 10);
assert.equal(proof.scope.excluded_toxin_rows, 406);
assert.equal(proof.scope.excluded_strict_elevated_rows, 26);
assert.deepEqual(proof.scope.excluded_toxin_stations, ["Humboldt", "HumboldtSouthBay", "TrinidadPier"]);
assert.match(proof.scope.eligibility, /at least 250 eligible rows/);
assert.match(proof.scope.hypotheses_note, /NOT REPORTED/);
assert.equal(proof.scope.calibration.status, "NOT VALIDATED FOR PROBABILITY USE");
close(proof.scope.calibration.candidate_ece, 0.0196);
close(proof.scope.calibration.baseline_ece, 0.0197);
assert.match(proof.scope.calibration.note, /ranking only/);

assert.match(proof.feature_contract.pn_observed_sum_definition, /not a provider-native total field/);
assert.match(proof.feature_contract.previous_toxin_definition, /previous eligible panel sample/);
assert.match(proof.feature_contract.previous_toxin_definition, /not necessarily the immediately previous/);

const gbm = proof.estimators.gradient_boosting;
const logistic = proof.estimators.logistic_regression;
close(gbm.candidate_ap, 0.5420411060484033);
close(gbm.fortified_baseline_ap, 0.3786775836391107);
close(gbm.margin_ap, 0.16336352240929258);
assert.deepEqual(gbm.ci95_pier_year, [0.07296816324862282, 0.2571449865806092]);
assert.equal(gbm.positive_control_passed, false);
close(gbm.max_destructive_control_recovery_fraction, 0.10985199350466332);
close(logistic.candidate_ap, 0.5223421807464577);
close(logistic.fortified_baseline_ap, 0.2987171032517309);
close(logistic.margin_ap, 0.22362507749472676);
assert.deepEqual(logistic.ci95_pier_year, [0.15608829018354395, 0.27492852896613634]);
assert.equal(logistic.positive_control_passed, true);
close(logistic.max_destructive_control_recovery_fraction, 0.14444117723912944);

assert.deepEqual(proof.curves.pooled_archive["10"], {
  gbm_model: 153,
  logistic_model: 147,
  gbm_baseline: 126,
  logistic_baseline: 110,
  pn_observed_sum: 125,
  assays: 533,
});
assert.deepEqual(proof.curves.within_pier_year["10"], {
  gbm_model: 102,
  logistic_model: 100,
  gbm_baseline: 77,
  logistic_baseline: 77,
  pn_observed_sum: 97,
  assays: 595,
});

for (const frameName of ["pooled_archive", "within_pier_year"]) {
  const frame = proof.curves[frameName];
  assert.deepEqual(Object.keys(frame), Array.from({ length: 39 }, (_, index) => String(index + 2)));
  let prior = null;
  for (let budget = 2; budget <= 40; budget += 1) {
    const row = frame[String(budget)];
    for (const key of ["gbm_model", "logistic_model", "gbm_baseline", "logistic_baseline", "pn_observed_sum"]) {
      assert.ok(Number.isInteger(row[key]) && row[key] >= 0 && row[key] <= proof.scope.elevated_rows);
      if (prior) assert.ok(row[key] >= prior[key], `${frameName} ${key} must be monotone at ${budget}%`);
    }
    assert.ok(Number.isInteger(row.assays) && row.assays > 0 && row.assays <= proof.scope.rows);
    if (prior) assert.ok(row.assays >= prior.assays, `${frameName} assay count must be monotone at ${budget}%`);
    prior = row;
  }
}

for (const [name, digest] of Object.entries(proof.proof.input_sha256)) {
  assert.match(digest, /^[0-9a-f]{64}$/, `invalid SHA-256 binding for ${name}`);
}
assert.deepEqual(proof.proof.fidelity_checks, {
  frozen_hashes_match: true,
  row_identity_match: true,
  pooled_10_percent_matches_banked_result: true,
});

const requiredCopy = [
  "Retrospective research prototype",
  "It does not score a new sample",
  "It does not score a new sample, forecast a harmful algal bloom, or determine whether water or seafood is safe.",
  "strictly greater than 0.5",
  "at or above 0.5",
  "Four otherwise eligible samples",
  "GATED / WALKED-BACK",
  "Hypotheses attempted: NOT REPORTED.",
  "Calibration for probability use: NOT VALIDATED.",
  "computed as 0.0196",
  "0.0197 for its baseline",
  "Planted-signal control: FAIL overall.",
  "79",
  "cross-pier independence is not established",
  "excluded 406 toxin rows",
  "26 of the 206 rows",
  "does not verify that every result was available",
  "Release proof package: MISSING.",
  "admission receipt",
  "input-availability proof",
  "key-shuffle proof",
  "execution-completion proof",
  "recovered at most 11.0%",
  "14.4% of the simpler-model improvement",
  "previous eligible sample in this analysis panel",
  "not a source-native “total” field",
  "Pattern model’s matched baseline",
  "an effective archive-wide share of ${effectiveShare}%",
  "href=\"./sample-triage-proof.json\"",
  "fetch(\"./sample-triage-proof.json\"",
  "data-proof-status=\"gated\"",
  "https://jdinneen.github.io/mbal-mission-control/sample-triage-og.png",
];
for (const needle of requiredCopy) assert.ok(html.includes(needle), `required page copy missing: ${needle}`);

assert.ok(!/raw\s+<i>Pseudo-nitzschia<\/i>\s+cell count/i.test(html), "derived group sum must not be called a raw total");
assert.ok(!html.includes("operational \"elevated\" threshold"), "strict comparator must not be equated to the official reference");
assert.ok(!html.includes("independent elevated episodes"), "gap-separated clusters must not be called independent episodes");
assert.ok(!html.includes("most informative"), "information gain was not tested");
assert.ok(!html.includes("Learn more."), "learning or information gain was not tested");
assert.ok(!html.includes("fitted models remain"), "the recreation did not persist fitted model objects");
assert.ok(!html.includes("frozen 50%"), "control limits must be described as recorded rules");
assert.ok(!html.includes("frozen minimum"), "control limits must be described as recorded rules");
assert.ok(html.includes("The explorer is unavailable rather than showing unverified numbers."), "proof fetch must fail closed");

console.log("PASS sample-triage: bound proof, visible claims, slider curves, eligibility, episode wording, and fail-closed behavior verified");
