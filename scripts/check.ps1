$ErrorActionPreference = "Stop"

$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $root

node scripts/check.mjs
if ($LASTEXITCODE -ne 0) {
  throw "node scripts/check.mjs failed"
}
