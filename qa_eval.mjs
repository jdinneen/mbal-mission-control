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

const previousReportLinks = [
  "reports/bacteria-findings/",
  "reports/pi-bacteria/",
  "reports/pi-memory-hybrid/",
];
const featuredReportLinks = [
  "reports/bacteria-station-memory/",
  "reports/clean-to-dirty-onset/",
  "reports/forward-2026-holdout/",
];
const featuredFindingIds = [
  "claim_bacteria_station_memory_supported",
  "claim_clean_to_dirty_onset",
  "claim_forward_2026_holdout_pass",
];

assert(html.includes("FFILTER='featured'"), "Findings should land on the Featured filter by default");
assert(html.includes("['featured','Featured',featuredCount]"), "Findings filter should expose a data-backed Featured category");
assert(html.includes("FEATURED_FINDING_IDS"), "Featured should be driven by current finding ids");
assert(html.includes("FEATURED_FINDING_REPORTS"), "Featured findings should map to full report URLs");
assert(!html.includes("FEATURED_REPORTS"), "Featured should not be hardcoded report placeholder cards");
assert(!html.includes('id="featuredFindings"'), "Featured should render normal data.json finding cards");
assert(html.includes("not 100% certain truths"), "Findings page should explain evidence-backed uncertainty in plain language");
assert(html.includes("How to read the three Featured findings"), "Findings page should explain the three Featured cards for readers with no context");

const featuredArrayMatch = html.match(/const FEATURED_FINDING_IDS=\[([\s\S]*?)\];/);
assert(featuredArrayMatch, "FEATURED_FINDING_IDS array should be present");
const actualFeaturedFindingIds = [...featuredArrayMatch[1].matchAll(/'([^']+)'/g)].map((m) => m[1]);
assert.deepEqual(actualFeaturedFindingIds, featuredFindingIds, "Featured finding ids should be exactly the current top three");

for (const link of featuredReportLinks) {
  assert(html.includes(link), `Featured card report link missing from index.html: ${link}`);
  assert(fs.existsSync(`${link}index.html`), `Featured full report page missing: ${link}index.html`);
}

const featuredReportExpectations = {
  "reports/bacteria-station-memory/": [
    "Plain-English Read First",
    "not a 100% accurate bacteria detector",
    "Model And Baseline Comparison",
    "Where It Works And Where It Does Not",
    "Parameter Sets And Operating Points",
    "What About Pi / Interlingua?",
    "Gate Check",
  ],
  "reports/clean-to-dirty-onset/": [
    "Plain-English Read First",
    "not a guarantee that every clean-to-dirty event will be caught",
    "Clean-to-dirty onset",
    "onset prev clean",
    "Forecast Horizon",
    "No onset-specific confusion matrix",
    "Pi / Interlingua Context",
  ],
  "reports/forward-2026-holdout/": [
    "Plain-English Read First",
    "not 100% accurate",
    "2026-01-05 to 2026-03-05",
    "not a pristine never-scored post-freeze lockbox",
    "Relationship To The Frozen Operational Headline",
    "County-By-County Context",
    "Pi / Interlingua Relevance",
  ],
};

for (const [link, needles] of Object.entries(featuredReportExpectations)) {
  const reportHtml = fs.readFileSync(`${link}index.html`, "utf8");
  assert(reportHtml.length > 15000, `Featured report is too thin: ${link}`);
  for (const needle of needles) {
    assert(reportHtml.includes(needle), `Featured report missing required content: ${link} :: ${needle}`);
  }
}

for (const link of previousReportLinks) {
  assert(fs.existsSync(`${link}index.html`), `Previously published report page missing: ${link}index.html`);
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
