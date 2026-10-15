<#
.SYNOPSIS
  Run the whole app locally: database, migrations, API, worker and web, each in its own window.

.EXAMPLE
  .\scripts\dev.ps1            # native: portable Postgres + Python + Node from %USERPROFILE%\.smart-money-tracker
  .\scripts\dev.ps1 -Docker    # everything in Docker Compose (requires Docker Desktop)

Then open http://localhost:3000  (API docs: http://localhost:8000/api/docs)
#>
param([switch]$Docker, [switch]$NoWorker)
. "$PSScriptRoot\_env.ps1"

if ($Docker) {
    Test-SmtTool "docker" "Install Docker Desktop (docs/plan/SETUP.md)."
    docker compose -f (Join-Path $RepoRoot "infra\docker-compose.yml") up --build
    return
}

Test-SmtTool "uv" "Install uv: https://docs.astral.sh/uv/"
Test-SmtTool "npm" "Node.js 24 is expected in $SmtHome\node (docs/plan/SETUP.md)."

& "$PSScriptRoot\db.ps1" start
Push-Location (Join-Path $RepoRoot "backend")
uv sync --quiet
uv run python -m smt.cli migrate
Pop-Location

$backend = Join-Path $RepoRoot "backend"
$web = Join-Path $RepoRoot "web"
$envLine = ". '$PSScriptRoot\_env.ps1';"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "$envLine Set-Location '$backend'; `$host.UI.RawUI.WindowTitle='smt api'; uv run python -m smt.cli api"
if (-not $NoWorker) {
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "$envLine Set-Location '$backend'; `$host.UI.RawUI.WindowTitle='smt worker'; uv run python -m smt.cli worker"
}
Start-Process powershell -ArgumentList "-NoExit", "-Command", "$envLine Set-Location '$web'; `$host.UI.RawUI.WindowTitle='smt web'; if (-not (Test-Path node_modules)) { npm install }; npm run dev"

Write-Host ""
Write-Host "Started. Web: http://localhost:3000   API docs: http://localhost:8000/api/docs"
