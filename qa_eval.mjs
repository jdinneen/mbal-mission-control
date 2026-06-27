import fs from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";

const html = fs.readFileSync("index.html", "utf8");
const snap = JSON.parse(fs.readFileSync("data.json", "utf8"));

assert(snap.state, "data.json should expose the published snapshot state");
assert(Array.isArray(snap.state.findings), "snapshot should include findings");
assert(Array.isArray(snap.state.models), "snapshot should include models");

/* ---- Start page: overview first, no question form on the homepage ---- */
const required = [
  "Audited coastal AI: public data, hard baselines, source-backed findings.",
  "What we are doing",
  "Biggest findings",
  "The pipeline",
  "Featured explainer",
  "function renderFullReport",
  "function renderLeaderboard",
  "function renderFrontiers",
];
for (const needle of required) {
  assert(html.includes(needle), `homepage wiring missing: ${needle}`);
}
assert(!html.includes('id="asklab"'), "homepage should not include the Ask-the-lab form");
assert(!html.includes('id="askq"'), "homepage should not include the Ask input");

// curated leaderboard: must be seeded so it is never empty on launch
const lb = JSON.parse(fs.readFileSync("leaderboard.json", "utf8"));
assert(Array.isArray(lb.lanes) && lb.lanes.length === 3,
  "leaderboard.json should have exactly 3 contribution lanes");
for (const lane of lb.lanes) {
  assert(Array.isArray(lane.entries) && lane.entries.length >= 1,
    `leaderboard lane '${lane.id}' must be seeded with >=1 entry`);
}

const scripts = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
assert(scripts.length > 0, "index.html should include an app script");

for (const [idx, script] of scripts.entries()) {
  new vm.Script(script, { filename: `index.html<script ${idx}>` });
}

console.log("qa_eval: PASS");
