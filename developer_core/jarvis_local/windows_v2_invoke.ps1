# ASCII only. Runs the reviewed V2 Operator.ps1 in THIS console (not redirected) so the
# user types the V2 challenge locally; the transcript lets JARVIS verify the outcome.
param([Parameter(Mandatory=$true)][string]$Operator, [Parameter(Mandatory=$true)][string]$Transcript)
$ErrorActionPreference = 'Stop'
if (Test-Path -LiteralPath $Transcript) { throw 'DENIED: transcript already exists' }
Start-Transcript -LiteralPath $Transcript | Out-Null
try {
    & $Operator
    Write-Host "UFO_V2_WRAPPER_EXIT=$LASTEXITCODE"
} catch {
    Write-Host ("UFO_V2_WRAPPER_ERROR=" + $_.Exception.Message)
} finally {
    Stop-Transcript | Out-Null
}
