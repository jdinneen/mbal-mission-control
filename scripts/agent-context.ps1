$ErrorActionPreference = "Stop"

$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $root

Write-Output "MBAL agent context"
Write-Output "root: $root"
Write-Output ""

Write-Output "instruction files:"
$instructionFiles = @("AGENTS.md", "CLAUDE.md", "AGENT.md", "README.md", "gpu_fold_run\README.md")
foreach ($file in $instructionFiles) {
  if (Test-Path $file) {
    $item = Get-Item $file
    "  - {0} ({1} bytes)" -f $file, $item.Length
  }
}
Write-Output ""

Write-Output "core files:"
$core = @(
  "index.html",
  "data.json",
  "downloads.json",
  "qa_eval.mjs",
  "worker.js",
  "wrangler.toml",
  "build_snapshot.py",
  "build_geo_downloads.py"
)
foreach ($file in $core) {
  if (Test-Path $file) {
    $item = Get-Item $file
    "  - {0} ({1} bytes, modified {2})" -f $file, $item.Length, $item.LastWriteTime.ToString("yyyy-MM-dd HH:mm")
  } else {
    "  - MISSING: $file"
  }
}
Write-Output ""

Write-Output "git status:"
if (Get-Command git -ErrorAction SilentlyContinue) {
  $status = git status --short
  if ($status) {
    $status
  } else {
    "  clean"
  }
} else {
  "  git not found"
}
Write-Output ""

Write-Output "available checks:"
if (Get-Command node -ErrorAction SilentlyContinue) {
  "  - node qa_eval.mjs"
} else {
  "  - node not found; qa_eval.mjs cannot run here"
}
"  - powershell -ExecutionPolicy Bypass -File scripts/check.ps1"
Write-Output ""

$gpuSummary = Join-Path $root "gpu_fold_run\out\summary.json"
Write-Output "gpu fold summary:"
if (Test-Path $gpuSummary) {
  try {
    $summary = Get-Content -Raw $gpuSummary | ConvertFrom-Json
    "  - summary.json present"
    if ($summary.verdict) { "  - verdict: $($summary.verdict)" }
    if ($summary.main_metric.iptm -ne $null) { "  - iptm: $($summary.main_metric.iptm)" }
    if ($summary.main_metric.ca_rmsd_angstrom -ne $null) { "  - ca_rmsd_angstrom: $($summary.main_metric.ca_rmsd_angstrom)" }
    if ($summary.gpu) { "  - gpu: $($summary.gpu)" }
  } catch {
    "  - summary.json present but not valid JSON"
  }
} else {
  "  - no gpu_fold_run\out\summary.json yet"
}
