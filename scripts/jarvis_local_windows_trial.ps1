# ASCII only. Opens a SEPARATE interactive PowerShell console for the one real UI action:
# clicking the button of the dedicated V2 test window. Two codes are typed by the user:
# 1) JARVIS plan approval, 2) the V2 Operator challenge. Nothing else is controlled.
$ErrorActionPreference = 'Stop'
$dev = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$py = Join-Path $dev '.venv-integrations\Scripts\python.exe'
$ws = Join-Path $env:USERPROFILE 'Documents\Codex\UFO-Research\FunctionalWorkspace'
if (-not (Test-Path -LiteralPath $py)) { throw "DEV python missing: $py" }
if (-not (Test-Path -LiteralPath $ws)) { throw "Workspace missing: $ws" }
$inner = @"
Set-Location -LiteralPath '$dev'
& '$py' -B -m developer_core.jarvis_local.cli --workspace '$ws' windows-test-window
Start-Sleep -Seconds 3
& '$py' -B -m developer_core.jarvis_local.cli --workspace '$ws' windows-invoke-test
Write-Host "JARVIS_WINDOWS_TRIAL_EXIT=`$LASTEXITCODE"
& '$py' -B -m developer_core.jarvis_local.cli --workspace '$ws' windows-close-test-window
Read-Host 'Press Enter to close this console'
"@
Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') -ArgumentList @('-NoProfile','-STA','-ExecutionPolicy','Bypass','-Command',$inner)
Write-Host 'Separate console started. Approve there; result is written to the workspace audit journal.'
