# ASCII only. Starts the iPhone side of JARVIS in two minimized consoles:
#   1) phone gateway (127.0.0.1:10790, reached via "tailscale serve", read + queue only)
#   2) phone executor (runs ONLY volume / mute quick commands; volume <= 40)
# Paired phones stay paired across restarts. Stop: close the two consoles.
# -NoVoiceLoop: skip "Hey JARVIS" here (the desktop JARVIS app answers it itself).
param([string]$TailnetHost = '', [switch]$NoVoiceLoop)
$ErrorActionPreference = 'Stop'
$dev = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
# The private Tailscale name lives only on this PC: <DEV>\.jarvis-local.json {"tailnet_host": "..."}
$cfg = Join-Path $dev '.jarvis-local.json'
if (-not $TailnetHost -and (Test-Path -LiteralPath $cfg)) { $TailnetHost = (Get-Content -LiteralPath $cfg -Raw | ConvertFrom-Json).tailnet_host }
if (-not $TailnetHost) { throw "Set the Tailscale name: -TailnetHost <name>.ts.net or $cfg" }
$py = Join-Path $dev '.venv-integrations\Scripts\python.exe'
$ws = Join-Path $env:USERPROFILE 'Documents\Codex\UFO-Research\FunctionalWorkspace'
$ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
if (-not (Test-Path -LiteralPath $py)) { throw "DEV python missing: $py" }
$common = "Set-Location -LiteralPath '$dev'; `$env:PYTHONIOENCODING='utf-8'; `$env:PYTHONUNBUFFERED='1'; "
# JARVIS Mobile (GitHub Pages) may call the gateway for files: <DEV>\.jarvis-local.json {"mobile_origin": "https://name.github.io"}
$origin = ''
if (Test-Path -LiteralPath $cfg) { $origin = [string](Get-Content -LiteralPath $cfg -Raw | ConvertFrom-Json).mobile_origin }
$gw = $common + "& '$py' -B -m developer_core.jarvis_local.pwa_gateway --workspace '$ws' --port 10790 --allow-host '$TailnetHost'"
if ($origin -match '^https://[a-z0-9.-]+$') { $gw += " --allow-origin '$origin'" }
$ex = $common + "& '$py' -B -m developer_core.jarvis_local.phone_executor --workspace '$ws'"
Start-Process -FilePath $ps -ArgumentList @('-NoProfile','-NoExit','-Command',$gw) -WindowStyle Minimized
Start-Process -FilePath $ps -ArgumentList @('-NoProfile','-NoExit','-Command',$ex) -WindowStyle Minimized
# 2b) GigaAM speech recognition on the GPU (127.0.0.1:10792), if the speech venv is installed;
#     the gateway and the voice loop fall back to Whisper on the CPU without it
$spy = Join-Path $env:USERPROFILE 'Documents\Codex\JARVIS-addons\.venv-speech\Scripts\python.exe'
if (Test-Path -LiteralPath $spy) {
    $gs = "`$env:PYTHONIOENCODING='utf-8'; `$env:PYTHONUNBUFFERED='1'; & '$spy' -I '$dev\developer_core\jarvis_local\gigaam_server.py' --port 10792"
    Start-Process -FilePath $ps -ArgumentList @('-NoProfile','-NoExit','-Command',$gs) -WindowStyle Minimized
    Write-Host 'GigaAM speech recognition starting (GPU).'
}
# 3) voice server: the user's own cloned voice (stark-metal-2) on 127.0.0.1:10791, if installed
$voice = Join-Path $env:USERPROFILE 'Documents\Codex\JARVIS-my-voice'
$vpy = Join-Path $voice '.venv-clone\Scripts\python.exe'
$ff = Get-ChildItem (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages') -Recurse -Filter 'avcodec-*.dll' -ErrorAction SilentlyContinue | Select-Object -First 1
if (Test-Path -LiteralPath (Join-Path $dev '.gemini-dev\gemini-key.bin')) {
    # chosen JARVIS voice: Gemini TTS "Schedar" + robot-3 effect (needs internet; free tier 3 new phrases/min)
    $vs = $common + "& '$py' -B -m developer_core.jarvis_local.gemini_voice serve --port 10791"
    Start-Process -FilePath $ps -ArgumentList @('-NoProfile','-NoExit','-Command',$vs) -WindowStyle Minimized
    Write-Host 'Voice server starting (Gemini voice).'
} elseif ((Test-Path -LiteralPath $vpy) -and (Test-Path -LiteralPath (Join-Path $voice 'prepared\consent.json'))) {
    $vs = "Set-Location -LiteralPath '$voice'; `$env:JARVIS_FFMPEG_BIN='$($ff.DirectoryName)'; `$env:TTS_HOME='$voice\models'; `$env:PYTHONIOENCODING='utf-8'; `$env:PYTHONUNBUFFERED='1'; & '$vpy' -I '$dev\developer_core\jarvis_local\voice_server.py' '$voice' --port 10791"
    Start-Process -FilePath $ps -ArgumentList @('-NoProfile','-NoExit','-Command',$vs) -WindowStyle Minimized
    Write-Host 'Voice server starting (model load ~30 s).'
}
# 4) "Hey JARVIS" voice loop at the PC (feature 8), if its packages are installed
$probe = if ($NoVoiceLoop) { 'False' } else { & $py -c "import importlib.util as u; print(all(u.find_spec(m) for m in ('openwakeword','sounddevice')))" 2>$null }
if ($probe -eq 'True') {
    $vl = $common + "& '$py' -B -m developer_core.jarvis_local.voice_loop --workspace '$ws'"
    Start-Process -FilePath $ps -ArgumentList @('-NoProfile','-NoExit','-Command',$vl) -WindowStyle Minimized
    Write-Host 'Voice loop starting: say "Hey JARVIS".'
}
$everything = 'C:\Program Files\Everything\Everything.exe'
if ((Test-Path -LiteralPath $everything) -and -not (Get-Process Everything -ErrorAction SilentlyContinue | Where-Object { $_.SessionId -eq (Get-Process -Id $PID).SessionId })) {
    Start-Process -FilePath $everything -ArgumentList '-startup'
}
$ts = 'C:\Program Files\Tailscale\tailscale.exe'
if (Test-Path -LiteralPath $ts) { & $ts serve --bg --yes http://127.0.0.1:10790 | Out-Null }
Write-Host "JARVIS phone: https://$TailnetHost  (gateway + executor started minimized)"
Write-Host 'A new pairing code is shown in the gateway console (only needed for a new phone).'
