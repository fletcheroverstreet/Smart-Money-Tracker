<#
.SYNOPSIS
  Start, stop or check the local PostgreSQL 18 database (no Docker needed).

.EXAMPLE
  .\scripts\db.ps1 start
  .\scripts\db.ps1 status
  .\scripts\db.ps1 stop
  .\scripts\db.ps1 init      # first time only: create the cluster and the smt / smt_test databases
#>
param([ValidateSet("start", "stop", "status", "init", "psql")][string]$Action = "status")
. "$PSScriptRoot\_env.ps1"

$pgCtl = Join-Path $PgBin "pg_ctl.exe"
if (-not (Test-Path $pgCtl)) {
    throw "PostgreSQL binaries not found in $PgBin. See docs/plan/SETUP.md (portable PostgreSQL 18)."
}

switch ($Action) {
    "init" {
        if (Test-Path (Join-Path $PgData "PG_VERSION")) { Write-Host "Cluster already exists at $PgData"; break }
        & (Join-Path $PgBin "initdb.exe") -D $PgData -U postgres -A trust -E UTF8 --locale=C
        Add-Content (Join-Path $PgData "postgresql.conf") "listen_addresses = 'localhost'`nport = $PgPort"
        & $pgCtl -D $PgData -l (Join-Path $PgData "server.log") -w start
        & (Join-Path $PgBin "psql.exe") -h localhost -p $PgPort -U postgres -c "create database smt;" -c "create database smt_test;"
    }
    "start"  { & $pgCtl -D $PgData -l (Join-Path $PgData "server.log") -w start }
    "stop"   { & $pgCtl -D $PgData -m fast stop }
    "status" { & $pgCtl -D $PgData status }
    "psql"   { & (Join-Path $PgBin "psql.exe") -h localhost -p $PgPort -U postgres -d smt }
}
