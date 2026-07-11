import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

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

console.log("checking publication guard...");
const publisherCode = read("publish.sh")
  .split(/\r?\n/)
  .filter((line) => !line.trimStart().startsWith("#"))
  .join("\n");
const addLines = publisherCode.split("\n").map((line) => line.trim()).filter((line) => /^git\s+add\b/.test(line));
const allowedAdd = `git add -- "\${PATHSPECS[@]}" ':(literal)version.json'`;
if (addLines.length !== 1 || addLines[0] !== allowedAdd) {
  fail("publish.sh must contain exactly one Git add command: the literal selected-path staging command.");
}
for (const required of [
  "git fetch --quiet origin main",
  "LOCAL_HEAD",
  "CANONICAL_URL_BASE",
  'git add -- "${PATHSPECS[@]}"',
  "verify_live_assets",
  "EXPECTED_TREE",
  "--dry-run",
]) {
  if (!publisherCode.includes(required)) fail(`publish.sh safety guard missing: ${required}`);
}
const stageAt = publisherCode.indexOf('git add -- "${PATHSPECS[@]}"');
const checks = [...publisherCode.matchAll(/node scripts\/check\.mjs/g)].map((m) => m.index ?? -1);
if (stageAt < 0 || !checks.some((i) => i < stageAt) || !checks.some((i) => i > stageAt)) {
  fail("publish.sh must check selected content both before and after staging.");
}
console.log("publication guard: PASS");

console.log("checking selected release files for privacy and secrets...");
const selected = process.argv.slice(2).filter((arg) => arg !== "--");
const textExtensions = new Set([
  ".css", ".csv", ".geojson", ".html", ".js", ".json", ".md", ".mjs", ".sh", ".srt", ".toml", ".tsv", ".txt", ".vtt", ".xml", ".yaml", ".yml",
]);
const localHostWord = ["local", "host"].join("");
const findings = [
  { name: "Windows user path", pattern: /[A-Za-z]:[\\/]Users[\\/][^\\/\s"']+/i },
  { name: "Unix home path", pattern: /\/(?:Users|home)\/[^/\s"']+/i },
  { name: "email address", pattern: /\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/i },
  { name: "private key", pattern: /-----BEGIN [A-Z ]*PRIVATE KEY-----/ },
  { name: "OpenAI-style secret", pattern: /\bsk-[A-Za-z0-9_-]{20,}\b/ },
  { name: "Google-style secret", pattern: /\bAIza[A-Za-z0-9_-]{20,}\b/ },
  { name: "GitHub-style secret", pattern: /\bgh[pousr]_[A-Za-z0-9]{20,}\b/ },
  { name: "assigned secret value", pattern: /(?:API[_-]?KEY|SECRET|TOKEN|PASSWORD)\s*[:=]\s*["']?[A-Za-z0-9_./+-]{16,}/i },
  {
    name: "local-only address",
    pattern: new RegExp(`(?:${localHostWord}|127\\.0\\.0\\.1|file:\\/\\/)`, "i"),
    webOnly: true,
  },
];
for (const file of selected) {
  if (!fs.existsSync(file) || !fs.statSync(file).isFile()) continue;
  const extension = path.extname(file).toLowerCase();
  const bytes = fs.readFileSync(file);
  const sample = bytes.subarray(0, Math.min(bytes.length, 65_536));
  const hasNull = sample.includes(0);
  const controls = [...sample].filter((byte) => byte < 9 || (byte > 13 && byte < 32)).length;
  if (!textExtensions.has(extension) && (hasNull || controls > Math.max(2, sample.length * 0.01))) continue;
  const text = bytes.toString("utf8");
  const decodedStrings = [];
  if (extension === ".json" || extension === ".geojson") {
    let parsed;
    try {
      parsed = JSON.parse(text);
    } catch {
      fail(`${file} is selected for publication but is not valid JSON.`);
    }
    const visit = (value) => {
      if (typeof value === "string") decodedStrings.push(value);
      else if (Array.isArray(value)) value.forEach(visit);
      else if (value && typeof value === "object") {
        for (const [key, child] of Object.entries(value)) {
          decodedStrings.push(key);
          visit(child);
        }
      }
    };
    visit(parsed);
  }
  const scanText = `${text}\n${decodedStrings.join("\n")}`;
  for (const finding of findings) {
    if (finding.webOnly && !new Set([".css", ".geojson", ".html", ".js", ".json", ".srt", ".vtt", ".xml"]).has(extension)) continue;
    if (finding.pattern.test(scanText)) fail(`${file} contains a possible ${finding.name}; review before publication.`);
  }
}
console.log(`selected release privacy scan: PASS (${selected.length} selected path(s))`);

console.log("checking public proxy posture...");
const indexHtml = read("index.html");
const proxyMatch = indexHtml.match(/const\s+PROXY_URL\s*=\s*"([^"]*)"/);
if (!proxyMatch) fail("index.html should define const PROXY_URL.");
const proxyUrl = proxyMatch[1];
if (proxyUrl !== "") fail("Public PROXY_URL must stay empty unless the proxy posture is reviewed intentionally.");
const liveProxyPattern = /https:\/\/mbal-search-\d+\.us-central1\.run\.app/;
for (const [name, doc] of [["README.md", readme], ["AGENTS.md", agents], ["index.html", indexHtml]]) {
  if (liveProxyPattern.test(doc)) fail(`${name} still references the retired live Cloud Run proxy.`);
}
if (/tokens \(512\)/.test(readme)) fail("README.md still documents the stale 512 output-token cap.");
console.log("public proxy posture: PASS");

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
