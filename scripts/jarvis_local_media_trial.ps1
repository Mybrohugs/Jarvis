# ASCII only. Opens a SEPARATE interactive console for the TV / soundbar trial.
# Each change asks for a JARVIS code; every step reads the TV state back.
# Steps: status -> volume -2 -> volume +2 (restore) -> bass +2 (LISTEN!) -> bass -2 (restore) -> status
$ErrorActionPreference = 'Stop'
$dev = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$py = Join-Path $dev '.venv-integrations\Scripts\python.exe'
$ws = Join-Path $env:USERPROFILE 'Documents\Codex\UFO-Research\FunctionalWorkspace'
if (-not (Test-Path -LiteralPath $py)) { throw "DEV python missing: $py" }
$inner = @"
Set-Location -LiteralPath '$dev'
`$env:PYTHONIOENCODING = 'utf-8'
function J { & '$py' -B -m developer_core.jarvis_local.cli --workspace '$ws' @args }
Write-Host '== 1. Status ==' -ForegroundColor Cyan; J media-status
Write-Host '== 2. Volume -2 (listen: quieter?) ==' -ForegroundColor Cyan; J media-volume --delta -2
Write-Host '== 3. Volume +2 (restore) ==' -ForegroundColor Cyan; J media-volume --delta 2
Write-Host '== 4. Bass +2: LISTEN to the soundbar now. Did the bass change? ==' -ForegroundColor Yellow; J media-bass --delta 2
Read-Host 'Listen for a few seconds, then press Enter'
Write-Host '== 5. Bass -2 (restore) ==' -ForegroundColor Cyan; J media-bass --delta -2
Write-Host '== 6. Final status ==' -ForegroundColor Cyan; J media-status
Read-Host 'Done. Press Enter to close this console'
"@
Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-Command',$inner)
Write-Host 'Separate console started. Approve each step there with the shown code.'
