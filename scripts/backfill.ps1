<#
.SYNOPSIS
  Load historical Forms 3/4/5 from the SEC's quarterly Insider Transactions data sets.

.DESCRIPTION
  Resumable: quarters already loaded are skipped (-Force loads them again). Loading is
  idempotent, so running it twice never duplicates rows. Progress prints as it goes and
  is recorded on /status.

.EXAMPLE
  .\scripts\backfill.ps1                      # the latest two quarters
  .\scripts\backfill.ps1 -Since 2006q1        # full history (about 80 quarters)
  .\scripts\backfill.ps1 -Quarters 2025q4,2026q1
  .\scripts\backfill.ps1 -Force               # load again even if already loaded
#>
param([string]$Since, [string[]]$Quarters, [switch]$Force)
. "$PSScriptRoot\_env.ps1"
& "$PSScriptRoot\db.ps1" start
Push-Location (Join-Path $RepoRoot "backend")
try {
    # The module form: Windows Smart App Control blocks the venv's smt.exe launcher (2026-09-29).
    $cmdArgs = @("run", "python", "-m", "smt.cli", "backfill")
    if ($Since) { $cmdArgs += @("--since", $Since) }
    if ($Quarters) { $cmdArgs += @("--quarters") + $Quarters }
    if ($Force) { $cmdArgs += "--force" }
    uv @cmdArgs
} finally { Pop-Location }
