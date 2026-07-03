import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
process.chdir(root);

function statLine(file) {
  if (!fs.existsSync(file)) return `  - MISSING: ${file}`;
  const stat = fs.statSync(file);
  const modified = stat.mtime.toISOString().slice(0, 16).replace("T", " ");
  return `  - ${file} (${stat.size} bytes, modified ${modified})`;
}

console.log("MBAL agent context");
console.log(`root: ${root}`);
console.log("");

console.log("instruction files:");
for (const file of ["AGENTS.md", "CLAUDE.md", "AGENT.md", "README.md", "gpu_fold_run/README.md"]) {
  if (fs.existsSync(file)) {
    const stat = fs.statSync(file);
    console.log(`  - ${file} (${stat.size} bytes)`);
  }
}
console.log("");

console.log("core files:");
for (const file of [
  "index.html",
  "data.json",
  "downloads.json",
  "qa_eval.mjs",
  "worker.js",
  "wrangler.toml",
  "build_snapshot.py",
  "build_geo_downloads.py",
]) {
  console.log(statLine(file));
}
console.log("");

console.log("git status:");
try {
  const status = execFileSync("git", ["status", "--short"], { encoding: "utf8" }).trim();
  console.log(status || "  clean");
} catch {
  console.log("  git not found or status unavailable");
}
console.log("");

console.log("available checks:");
console.log("  - node scripts/check.mjs");
console.log("  - node qa_eval.mjs");
console.log("  - powershell -ExecutionPolicy Bypass -File scripts/check.ps1");
console.log("");

console.log("gpu fold summary:");
const gpuSummary = path.join(root, "gpu_fold_run", "out", "summary.json");
if (fs.existsSync(gpuSummary)) {
  try {
    const summary = JSON.parse(fs.readFileSync(gpuSummary, "utf8"));
    console.log("  - summary.json present");
    if (summary.verdict) console.log(`  - verdict: ${summary.verdict}`);
    if (summary.main_metric?.iptm != null) console.log(`  - iptm: ${summary.main_metric.iptm}`);
    if (summary.main_metric?.ca_rmsd_angstrom != null) {
      console.log(`  - ca_rmsd_angstrom: ${summary.main_metric.ca_rmsd_angstrom}`);
    }
    if (summary.gpu) console.log(`  - gpu: ${summary.gpu}`);
  } catch {
    console.log("  - summary.json present but not valid JSON");
  }
} else {
  console.log("  - no gpu_fold_run/out/summary.json yet");
}
