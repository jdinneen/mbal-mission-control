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

assert(html.includes("FFILTER='featured'"), "Findings should land on the Featured filter by default");
assert(html.includes("['featured','Featured',FEATURED_REPORTS.length]"), "Findings filter should expose a Featured category");
assert(html.includes('id="featuredFindings"'), "Findings page should have a dedicated Featured report container");

for (const link of featuredReportLinks) {
  assert(html.includes(`href:'${link}'`), `Featured report link missing from index.html: ${link}`);
  assert(fs.existsSync(`${link}index.html`), `Featured report page missing: ${link}index.html`);
}

const scripts = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
assert(scripts.length > 0, "index.html should include an app script");

for (const [idx, script] of scripts.entries()) {
  new vm.Script(script, { filename: `index.html<script ${idx}>` });
}

console.log("qa_eval: PASS");
