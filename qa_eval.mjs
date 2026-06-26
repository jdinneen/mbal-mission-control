import fs from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";

const html = fs.readFileSync("index.html", "utf8");
const snap = JSON.parse(fs.readFileSync("data.json", "utf8"));

assert(snap.state, "data.json should expose the published snapshot state");
assert(Array.isArray(snap.state.findings), "snapshot should include findings");
assert(Array.isArray(snap.state.models), "snapshot should include models");

/* ---- Ask the lab: the engagement loop must stay wired and grounded ----
   This used to be a guard that FORBADE the Ask UI (it was removed once). It now
   guards the opposite: the curated-answer + grounded-assistant + research-request
   loop must be present, and the instant-answer side must stay backed by a
   human-approved answered.json (never an ungrounded client guess). */
const required = [
  'id="askq"',
  "function askLab",
  "function matchAnswered",   // instant answers come from curated answered.json, not raw fuzzy match
  "function renderLeaderboard",
  "function renderFrontiers",
  "Ask the lab",
  "const PROXY_URL",          // grounded worker is opt-in via config, not hardcoded
];
for (const needle of required) {
  assert(html.includes(needle), `Ask-the-lab wiring missing: ${needle}`);
}

// curated answered-questions log: the instant-answer source of truth
const answered = JSON.parse(fs.readFileSync("answered.json", "utf8"));
assert(Array.isArray(answered.items) && answered.items.length >= 5,
  "answered.json should hold the seeded curated answers (>=5)");
for (const it of answered.items) {
  assert(it.q && it.a && it.grade, "each answered item needs q, a, grade");
  assert(["Supported", "Caveated", "Null", "Exploratory"].includes(it.grade),
    `answered item has an unknown grade: ${it.grade}`);
}

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
