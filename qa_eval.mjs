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

const scripts = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
assert(scripts.length > 0, "index.html should include an app script");

for (const [idx, script] of scripts.entries()) {
  new vm.Script(script, { filename: `index.html<script ${idx}>` });
}

console.log("qa_eval: PASS");
