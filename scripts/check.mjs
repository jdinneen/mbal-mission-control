import { spawnSync } from "node:child_process";
import fs from "node:fs";

function read(file) {
  return fs.readFileSync(file, "utf8");
}

function fail(message) {
  throw new Error(message);
}

console.log("checking mirrored agent guides...");
const agents = read("AGENTS.md");
const claude = read("CLAUDE.md");
const agent = read("AGENT.md");
if (agents !== claude) fail("AGENTS.md and CLAUDE.md have drifted.");
if (agents !== agent) fail("AGENTS.md and AGENT.md have drifted.");
console.log("agent guide sync: PASS");

console.log("checking Claude Code settings...");
const claudeSettings = JSON.parse(read(".claude/settings.json"));
const allowRules = claudeSettings.permissions?.allow ?? [];
const denyRules = claudeSettings.permissions?.deny ?? [];
for (const rule of [
  "Bash(node qa_eval.mjs)",
  "Bash(node scripts/agent-context.mjs)",
  "Bash(node scripts/check.mjs)",
]) {
  if (!allowRules.includes(rule)) fail(`.claude/settings.json missing allow rule: ${rule}`);
}
for (const rule of [
  "Read(./.env)",
  "Read(./.dev.vars)",
  "Read(./.wrangler/**)",
  "Read(./**/*.pem)",
  "Read(./**/*.key)",
]) {
  if (!denyRules.includes(rule)) fail(`.claude/settings.json missing deny rule: ${rule}`);
}
console.log("Claude settings: PASS");

console.log("checking README credential wording...");
const readme = read("README.md");
if (/Anthropic key/.test(readme)) fail("README.md still refers to an Anthropic key.");
if (!/GOOGLE_AI_KEY/.test(readme)) fail("README.md should document GOOGLE_AI_KEY.");
console.log("README credential wording: PASS");

console.log("checking live proxy documentation...");
const indexHtml = read("index.html");
const proxyMatch = indexHtml.match(/const\s+PROXY_URL\s*=\s*"([^"]*)"/);
if (!proxyMatch) fail("index.html should define const PROXY_URL.");
const proxyUrl = proxyMatch[1];
if (!/^https:\/\/mbal-search-\d+\.us-central1\.run\.app$/.test(proxyUrl)) {
  fail("PROXY_URL is no longer the documented Cloud Run endpoint. Update README.md and agent guides intentionally.");
}
for (const doc of [readme, agents]) {
  if (!doc.includes(proxyUrl)) fail(`README.md and AGENTS.md must mention the current PROXY_URL: ${proxyUrl}`);
}
if (/tokens \(512\)/.test(readme)) fail("README.md still documents the stale 512 output-token cap.");
console.log("live proxy docs: PASS");

console.log("checking raw-download policy...");
const downloads = JSON.parse(read("downloads.json"));
if (downloads.downloads_enabled !== false) {
  fail("downloads.json must keep downloads_enabled=false unless the publication policy changes.");
}
if ((downloads.items ?? []).length !== 0) {
  fail("downloads.json items must stay empty unless the publication policy changes.");
}
console.log("downloads policy: PASS");

console.log("checking heavy-artifact ignore guidance...");
const gitignore = read(".gitignore");
if (!gitignore.includes("gpu_fold_run/out/")) fail(".gitignore should ignore gpu_fold_run/out/.");
console.log("heavy-artifact guidance: PASS");

console.log("running qa_eval.mjs...");
const qa = spawnSync(process.execPath, ["qa_eval.mjs"], { stdio: "inherit" });
if (qa.status !== 0) fail("qa_eval.mjs failed.");

console.log("check: PASS");
