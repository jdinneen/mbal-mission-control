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

assert.equal(proof.schema_version, 3);
assert.equal(proof.artifact, "SampleNext microscope-count assay-priority tool");
assert.equal(proof.release_ready, false);
assert.equal(proof.release_grade_status, "NEEDS-DATA");
assert.match(proof.public_status, /^RETROSPECTIVE DECISION SUPPORT/);

const scope = proof.scope;
assert.equal(scope.rows, 5328);
assert.equal(scope.elevated_rows, 180);
close(scope.prevalence, 180 / 5328);
assert.equal(scope.recorded_elevated_runs, 102);
assert.equal(scope.pier_count, 7);
assert.equal(scope.date_min, "2008-06-30");
assert.equal(scope.date_max, "2026-06-22");
assert.match(scope.elevated_definition, /strictly greater than 0\.5/);
assert.match(scope.official_reference_definition, /at or above 0\.5/);
assert.match(scope.independence_status, /NOT ESTABLISHED/);

const contract = proof.recommendation_contract;
assert.ok(contract.required_inputs.includes("sea-surface temperature in degrees Celsius"));
assert.ok(contract.required_inputs.some((value) => value.includes("microscope-count groups")));
assert.deepEqual(contract.optional_inputs, ["chlorophyll in milligrams per cubic metre"]);
assert.equal(contract.primary_score, "reported Pseudo-nitzschia group sum, descending");
assert.match(contract.priority_rule, /user chooses the number of expedited slots/);
assert.match(contract.sea_surface_temperature_role, /^required context field/);
assert.match(contract.chlorophyll_role, /^optional context field/);
assert.match(contract.why_environment_does_not_change_priority, /did not establish/);
assert.equal(contract.not_a_probability, true);
assert.equal(contract.not_a_safety_determination, true);
assert.match(contract.browser_only, /not transmitted or stored/);

const reference = proof.calculator_reference;
assert.equal(reference.reference_population, "5,328 eligible historical samples from seven California piers");
assert.match(reference.count_definition, /not a true total/);
assert.deepEqual(reference.pooled_count_quantiles, {
  "0.5": 2600,
  "0.75": 15019,
  "0.9": 61095.30000000004,
  "0.95": 131186,
  "0.99": 460099.8863999949,
});
assert.equal(reference.priority_bands.length, 4);
assert.deepEqual(reference.priority_bands.map(({ id, rows, strict_elevated_rows }) => ({ id, rows, strict_elevated_rows })), [
  { id: "routine", rows: 3995, strict_elevated_rows: 10 },
  { id: "review", rows: 800, strict_elevated_rows: 45 },
  { id: "expedite", rows: 266, strict_elevated_rows: 47 },
  { id: "expedite_highest", rows: 267, strict_elevated_rows: 78 },
]);
close(reference.priority_bands[0].strict_elevated_rate, 10 / 3995);
close(reference.priority_bands[1].strict_elevated_rate, 45 / 800);
close(reference.priority_bands[2].strict_elevated_rate, 47 / 266);
close(reference.priority_bands[3].strict_elevated_rate, 78 / 267);
assert.equal(Object.keys(reference.by_station).length, 7);
assert.equal(reference.by_station.MontereyWharf.rows, 276);
assert.equal(reference.by_station.MontereyWharf.count_quantiles["0.9"], 176677.5);
assert.deepEqual(reference.sea_surface_temperature, {
  n: 5193, unit: "degrees Celsius", min: 8.8, p05: 12, median: 15.9, p95: 21.6, max: 25.5,
});
assert.deepEqual(reference.chlorophyll, {
  n: 5202, unit: "milligrams per cubic metre", min: 0, p05: 0.54, median: 2.55,
  p95: 17.318999999999996, max: 467.66,
});
assert.match(reference.association_warning, /not a calibrated probability/);

const replay = proof.operational_replay;
assert.match(replay.status, /^NULL FOR MODEL ADVANTAGE/);
assert.match(replay.workflow, /chronological monthly batches/);
assert.match(replay.workflow, /10% quota/);
assert.equal(replay.rows, 5328);
assert.equal(replay.strict_elevated_rows, 180);
assert.equal(replay.recorded_episodes, 102);
assert.deepEqual(
  [replay.raw_count.assays, replay.raw_count.elevated_captured, replay.raw_count.episodes_captured, replay.raw_count.first_positive_captured],
  [512, 66, 48, 39],
);
assert.deepEqual(
  [replay.random_floor.assays, replay.random_floor.elevated_captured],
  [512, 20],
);
assert.deepEqual(
  [replay.perfect_foresight_ceiling.assays, replay.perfect_foresight_ceiling.elevated_captured],
  [512, 116],
);
assert.equal(replay.candidate_gradient_boosting.elevated_captured, 78);
assert.equal(replay.candidate_logistic.elevated_captured, 81);
assert.deepEqual(replay.model_edge_gate.per_estimator.gbm.sample_capture.ci95, [-3, 28]);
assert.deepEqual(replay.model_edge_gate.per_estimator.logistic.sample_capture.ci95, [3, 29]);
assert.equal(replay.model_edge_gate.ci_excludes_zero.gbm, false);
assert.equal(replay.model_edge_gate.ci_excludes_zero.logistic, true);
assert.match(replay.model_edge_gate.verdict, /^NULL/);

const evaluation = replay.deployment_ap;
assert.deepEqual(
  [evaluation.rows, evaluation.strict_elevated_rows, evaluation.prevalence, evaluation.candidate, evaluation.raw_count],
  [5162, 179, 0.0347, 0.392, 0.2652],
);
assert.deepEqual(evaluation.margin.ci95, [0.0182, 0.2236]);
assert.equal(evaluation.independent_event_count, 101);
assert.equal(evaluation.pier_year_variation.units_candidate_beats_baseline, "11/51");
assert.equal(evaluation.year_variation.units_candidate_beats_baseline, "13/18");
assert.equal(evaluation.hypotheses_attempted, 1);
assert.equal(evaluation.permutation_null, "NOT RUN");

for (const hashes of [proof.proof.input_sha256, proof.proof.batch_input_sha256]) {
  for (const [name, digest] of Object.entries(hashes)) {
    assert.match(digest, /^[0-9a-f]{64}$/, `invalid SHA-256 binding for ${name}`);
  }
}
assert.deepEqual(proof.proof.fidelity_checks, {
  frozen_hashes_match: true,
  row_identity_match: true,
  pooled_10_percent_matches_banked_result: true,
  monthly_raw_count_capture_reproduced: true,
  monthly_raw_count_assays_reproduced: true,
  monthly_oracle_capture_reproduced: true,
  monthly_oracle_assays_reproduced: true,
});

const requiredCopy = [
  "Microscope counts in.",
  "Built for the pier-monitoring workflow",
  "the algae group that includes domoic-acid-producing species",
  "Use the microscope counts available within a few days.",
  "Include sea-surface temperature; add chlorophyll only if it is back.",
  "At least one microscope-count group is required. Chlorophyll is optional.",
  "Sea-surface temperature — °C",
  "Required context. It does not change the count-based queue rank.",
  "Chlorophyll — mg/m³",
  "Leave blank if pending",
  "Optional—leave it blank when it is not available for this decision.",
  "A blank group is treated as unreported—not zero.",
  "Expedited slots this batch",
  "Highest reported-group sum ranks first.",
  "Entered values stay in this browser tab.",
  "The simple rule survived the realistic replay.",
  "Average Precision ranking score (0–1; higher is better)",
  "The earlier pattern model looked stronger when 18 years were ranked as one giant list.",
  "No prospective validation.",
  "No calibrated probability.",
  "No automatic transfer.",
  "Normal protocols still apply.",
  "This is a relative queue suggestion, not a toxin probability or safety determination.",
  "The tool is unavailable rather than showing an unverified recommendation.",
  "href=\"./sample-triage-proof.json\"",
  "fetch(\"./sample-triage-proof.json\"",
  "https://jdinneen.github.io/mbal-mission-control/sample-triage-og.png",
];
for (const needle of requiredCopy) assert.ok(html.includes(needle), `required page copy missing: ${needle}`);

const requiredControls = [
  'id="sample-form"', 'id="sample-label"', 'id="sample-date"', 'id="location"', 'id="pn-del"',
  'id="pn-ser"', 'id="sst"', 'id="chlorophyll"', 'id="assess-button"', 'id="capacity"',
  'id="queue-body"', 'id="download-queue"',
];
for (const needle of requiredControls) assert.ok(html.includes(needle), `required tool control missing: ${needle}`);
assert.match(html, /id="sst"[^>]*required/);
assert.doesNotMatch(html, /id="chlorophyll"[^>]*required/);
assert.ok(!html.includes("high risk"), "historical bands must not be presented as calibrated risk");
assert.ok(!html.includes("safe to skip"), "the page must not imply safety from a queue rank");
assert.ok(!html.includes("forecast a harmful algal bloom"), "the tool must not be framed as the competing bloom forecast discussed in the transcript");

console.log("PASS sample-triage: input contract, count bands, monthly replay, visible claims, and fail-closed behavior verified");
