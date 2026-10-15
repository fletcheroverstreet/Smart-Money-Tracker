# Shared environment for the dev scripts. Dot-source it:  . "$PSScriptRoot\_env.ps1"
# Local tools live in %USERPROFILE%\.smart-money-tracker (same idea as finlake's ~/.finlake):
#   pgsql\   portable PostgreSQL 18      pgdata\  the local database cluster (port 5433)
#   venv\    Python virtualenv           node\    Node.js 24 LTS
#   raw\     raw document store

$ErrorActionPreference = "Stop"
$script:RepoRoot = Split-Path -Parent $PSScriptRoot
$script:SmtHome  = if ($env:SMT_HOME) { $env:SMT_HOME } else { Join-Path $env:USERPROFILE ".smart-money-tracker" }
$script:PgBin    = Join-Path $SmtHome "pgsql\bin"
$script:PgData   = Join-Path $SmtHome "pgdata"
$script:PgPort   = 5433

$env:UV_PROJECT_ENVIRONMENT = Join-Path $SmtHome "venv"
$nodeDir = Join-Path $SmtHome "node"
if ((Test-Path $nodeDir) -and ($env:Path -notlike "*$nodeDir*")) { $env:Path = "$nodeDir;$env:Path" }
if (-not $env:DATABASE_URL) { $env:DATABASE_URL = "postgresql+psycopg://postgres@localhost:$PgPort/smt" }

function Test-SmtTool([string]$Name, [string]$Hint) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name was not found. $Hint"
    }
}
