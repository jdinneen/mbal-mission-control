import fs from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";

const html = fs.readFileSync("index.html", "utf8");
const snap = JSON.parse(fs.readFileSync("data.json", "utf8"));

assert(snap.state, "data.json should expose the published snapshot state");
assert(Array.isArray(snap.state.findings), "snapshot should include findings");
assert(Array.isArray(snap.state.models), "snapshot should include models");

const forbidden = [
  "Ask the evidence",
  "id=\"askq\"",
  "id=\"askbtn\"",
  "id=\"qachips\"",
  "id=\"qaout\"",
  "Send a suggestion",
  "id=\"suggestbox\"",
  "id=\"suggestbtn\"",
  "formsubmit.co",
  "mbal-search-957730565502",
  "PROXY_URL",
  "function askData",
  "function submitSuggestion",
  "function deterministicEvidenceAnswer",
  "function localEvidenceAnswer",
  "function rankedDocs",
  "function askDocs",
  "function showAns",
  "answered locally",
];

for (const needle of forbidden) {
  assert(!html.includes(needle), `Ask functionality should not be present: ${needle}`);
}

const featuredReportLinks = [
  "reports/bacteria-findings/",
  "reports/pi-bacteria/",
  "reports/pi-memory-hybrid/",
];
const featuredFindingIds = [
  "claim_bacteria_station_memory_supported",
  "claim_clean_to_dirty_onset",
  "claim_forward_2026_holdout_pass",
];

assert(html.includes("FFILTER='featured'"), "Findings should land on the Featured filter by default");
assert(html.includes("['featured','Featured',featuredCount]"), "Findings filter should expose a data-backed Featured category");
assert(html.includes("FEATURED_FINDING_IDS"), "Featured should be driven by current finding ids");
assert(!html.includes("FEATURED_REPORTS"), "Featured should not be hardcoded report placeholder cards");
assert(!html.includes('id="featuredFindings"'), "Featured should render normal data.json finding cards");

const featuredArrayMatch = html.match(/const FEATURED_FINDING_IDS=\[([\s\S]*?)\];/);
assert(featuredArrayMatch, "FEATURED_FINDING_IDS array should be present");
const actualFeaturedFindingIds = [...featuredArrayMatch[1].matchAll(/'([^']+)'/g)].map((m) => m[1]);
assert.deepEqual(actualFeaturedFindingIds, featuredFindingIds, "Featured finding ids should be exactly the current top three");

for (const link of featuredReportLinks) {
  assert(fs.existsSync(`${link}index.html`), `Featured report page missing: ${link}index.html`);
}

for (const id of featuredFindingIds) {
  assert(html.includes(`'${id}'`), `Featured finding id missing from index.html: ${id}`);
  const finding = snap.state.findings.find((f) => f.id === id);
  assert(finding, `Featured finding id missing from data.json: ${id}`);
  assert(!String(finding.kind || "").toLowerCase().includes("null"), `Featured finding must not be null: ${id}`);
}

const scripts = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
assert(scripts.length > 0, "index.html should include an app script");

for (const [idx, script] of scripts.entries()) {
  new vm.Script(script, { filename: `index.html<script ${idx}>` });
}

console.log("qa_eval: PASS");
