<#
.SYNOPSIS
  Run every check CI runs: backend lint, type-check and tests; web lint, type-check and build.

.EXAMPLE
  .\scripts\test.ps1              # everything
  .\scripts\test.ps1 -Backend     # backend only
#>
param([switch]$Backend, [switch]$Web)
. "$PSScriptRoot\_env.ps1"
$all = -not ($Backend -or $Web)

if ($all -or $Backend) {
    & "$PSScriptRoot\db.ps1" start
    Push-Location (Join-Path $RepoRoot "backend")
    try {
        uv sync --quiet
        uv run ruff check src tests;   if ($LASTEXITCODE) { throw "ruff failed" }
        uv run mypy;                   if ($LASTEXITCODE) { throw "mypy failed" }
        uv run pytest -q;              if ($LASTEXITCODE) { throw "pytest failed" }
    } finally { Pop-Location }
}

if ($all -or $Web) {
    Push-Location (Join-Path $RepoRoot "web")
    try {
        if (-not (Test-Path node_modules)) { npm install }
        npm run lint;       if ($LASTEXITCODE) { throw "eslint failed" }
        npm run typecheck;  if ($LASTEXITCODE) { throw "tsc failed" }
        npm test;           if ($LASTEXITCODE) { throw "web tests failed" }
        npm run build;      if ($LASTEXITCODE) { throw "next build failed" }
    } finally { Pop-Location }
}
Write-Host "All checks passed."
