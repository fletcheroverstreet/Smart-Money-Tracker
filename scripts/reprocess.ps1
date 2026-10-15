<#
.SYNOPSIS
  Re-parse stored EDGAR filings with the current parsers. No requests go to the SEC:
  the raw documents were stored before they were first parsed.

.EXAMPLE
  .\scripts\reprocess.ps1                         # filings parsed by an older parser version
  .\scripts\reprocess.ps1 -All                    # every stored filing
  .\scripts\reprocess.ps1 -Form 4 -Limit 500
  .\scripts\reprocess.ps1 -Accession 0001193125-26-396718
#>
param([switch]$All, [string]$Form, [string]$Accession, [int]$Limit)
. "$PSScriptRoot\_env.ps1"
& "$PSScriptRoot\db.ps1" start
Push-Location (Join-Path $RepoRoot "backend")
try {
    $cmdArgs = @("run", "smt", "reprocess")
    if ($All) { $cmdArgs += "--all" }
    if ($Form) { $cmdArgs += @("--form", $Form) }
    if ($Accession) { $cmdArgs += @("--accession", $Accession) }
    if ($Limit) { $cmdArgs += @("--limit", "$Limit") }
    uv @cmdArgs
} finally { Pop-Location }
