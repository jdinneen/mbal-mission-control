import fs from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";

const html = fs.readFileSync("index.html", "utf8");
const snap = JSON.parse(fs.readFileSync("data.json", "utf8"));

assert(snap.state, "data.json should expose the published snapshot state");
assert(Array.isArray(snap.state.findings), "snapshot should include findings");
assert(Array.isArray(snap.state.models), "snapshot should include models");
assert(Array.isArray(snap.state.candidate_targets) && snap.state.candidate_targets.length === 41,
  "snapshot should include the 41 ranked target candidates");

const featuredBlock = html.match(/const FEATURED_QUICK_READS=\{([\s\S]*?)\n\};/);
assert(featuredBlock, "homepage should define the featured finding fallback set");
const featuredIds = [...featuredBlock[1].matchAll(/\n\s+([A-Za-z0-9_]+):\{/g)].map((m) => m[1]);
const findingIds = new Set(snap.state.findings.map((f) => f.id));
const resolvedFeatured = snap.state.findings.filter((f) => f.featured || featuredIds.includes(f.id));
assert(html.includes("function isFeaturedFinding"), "Findings view should derive featured status safely");
assert(resolvedFeatured.length > 0, "Featured findings filter should resolve at least one current finding");

/* ---- Start page: overview first, no question form on the homepage ---- */
const required = [
  "Audited coastal AI: public data, hard baselines, source-backed findings.",
  "What we are doing",
  "Biggest findings",
  "The pipeline",
  "Featured explainer",
  "function renderFullReport",
  "function targetVocabControls",
  "function candidateTargetDrawer",
];
for (const needle of required) {
  assert(html.includes(needle), `homepage wiring missing: ${needle}`);
}
assert(!html.includes('id="asklab"'), "homepage should not include the Ask-the-lab form");
assert(!html.includes('id="askq"'), "homepage should not include the Ask input");
assert(!html.includes("Open frontiers — help us crack these"), "homepage should not include Open frontiers");
assert(!html.includes("<h2>Kept on the record</h2>"), "homepage should not include Kept on the record");
assert(!html.includes("<h2>Leaderboard</h2>"), "homepage should not include Leaderboard");
assert(!html.includes("id=\"frontierlist\""), "homepage should not include frontier grid");
assert(!html.includes("id=\"nulllist\""), "homepage should not include null grid");
assert(!html.includes("id=\"leaderboard\""), "homepage should not include leaderboard grid");
assert(!html.includes("Monterey Bay Today"), "nav should not include Monterey Bay Today");

const scripts = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
assert(scripts.length > 0, "index.html should include an app script");

for (const [idx, script] of scripts.entries()) {
  new vm.Script(script, { filename: `index.html<script ${idx}>` });
}

console.log("qa_eval: PASS");
