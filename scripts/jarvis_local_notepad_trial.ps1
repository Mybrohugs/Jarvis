# ASCII only. Opens a SEPARATE interactive console to set text in the JARVIS Notepad tab.
# Two codes are typed by the user: 1) JARVIS plan approval, 2) Notepad operator challenge.
param([string]$Text = 'Hello from JARVIS: allowlisted Notepad trial.')
$ErrorActionPreference = 'Stop'
$dev = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$py = Join-Path $dev '.venv-integrations\Scripts\python.exe'
$ws = Join-Path $env:USERPROFILE 'Documents\Codex\UFO-Research\FunctionalWorkspace'
if (-not (Test-Path -LiteralPath $py)) { throw "DEV python missing: $py" }
if ($Text -match "'") { throw 'Text may not contain single quotes in this launcher' }
$inner = @"
Set-Location -LiteralPath '$dev'
& '$py' -B -m developer_core.jarvis_local.cli --workspace '$ws' notepad-inspect
if (`$LASTEXITCODE -ne 0) { & '$py' -B -m developer_core.jarvis_local.cli --workspace '$ws' notepad-open }
& '$py' -B -m developer_core.jarvis_local.cli --workspace '$ws' notepad-set-text '$Text'
Write-Host "JARVIS_NOTEPAD_TRIAL_EXIT=`$LASTEXITCODE"
Read-Host 'Press Enter to close this console'
"@
Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') -ArgumentList @('-NoProfile','-STA','-ExecutionPolicy','Bypass','-Command',$inner)
Write-Host 'Separate console started. Keep the JARVIS tab selected in Notepad and approve there.'
