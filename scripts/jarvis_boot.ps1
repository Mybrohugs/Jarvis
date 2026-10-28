# ASCII only. One logon entry point for all of JARVIS, with a watchdog.
#   1) waits (up to -WaitSeconds) for DNS + Tailscale, because at logon both are often not ready yet
#   2) starts the phone side (jarvis_phone_start.ps1 -NoVoiceLoop) unless the gateway already answers
#   3) starts the desktop JARVIS (JARVIS-copy\main.py) unless it already runs
#   4) every 30 s: restarts whatever died (at most 5 restarts per part per 10 min, then gives up and logs it)
# Log: %LOCALAPPDATA%\JARVIS\boot.log (desktop app output: desktop.log / desktop.err.log)
# Stop: close this hidden process (Task Manager: powershell with jarvis_boot.ps1) or run with -Once.
param([int]$WaitSeconds = 180, [switch]$Once, [switch]$NoDesktop)
$ErrorActionPreference = 'Continue'
$dev = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$root = Split-Path -Parent (Split-Path -Parent $dev)  # coding-agent -> jarvis-coding-agent-dev -> p6-recovery
$app = Join-Path $root 'JARVIS-copy'
$pyw = Join-Path $root 'clean-runtime\Scripts\python.exe'
$phone = Join-Path $dev 'scripts\jarvis_phone_start.ps1'
$logDir = Join-Path $env:LOCALAPPDATA 'JARVIS'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir 'boot.log'

function Log([string]$m) {
    if ((Test-Path -LiteralPath $log) -and (Get-Item -LiteralPath $log).Length -gt 2MB) { Move-Item -Force $log "$log.1" }
    Add-Content -LiteralPath $log -Value ("{0:yyyy-MM-dd HH:mm:ss} {1}" -f (Get-Date), $m) -Encoding ASCII
}

# one watchdog at a time
$mutex = New-Object System.Threading.Mutex($false, 'Local\JARVIS_BOOT_WATCHDOG')
if (-not $mutex.WaitOne(0)) { Log 'another jarvis_boot is already running; exit'; exit 0 }

function Test-Port([int]$p) {
    try { $c = New-Object Net.Sockets.TcpClient; $ok = $c.ConnectAsync('127.0.0.1', $p).Wait(1500); $c.Close(); $ok } catch { $false }
}
function Get-Desktop { Get-CimInstance Win32_Process -Filter "Name like 'python%'" | Where-Object { $_.CommandLine -match 'JARVIS-copy\\main\.py' } }
# python workers plus the minimized "-NoExit" consoles that host them (else every restart leaves a console behind)
function Get-PhonePids { Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(python|pythonw|powershell)\.exe$' -and $_.ProcessId -ne $PID -and $_.CommandLine -match 'jarvis_local\.(pwa_gateway|phone_executor|gemini_voice)|voice_server\.py' } }

function Start-Phone {
    # kill half-alive leftovers so a restart never runs two executors
    Get-PhonePids | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    $out = & (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') -NoProfile -ExecutionPolicy Bypass -File $phone -NoVoiceLoop 2>&1 | Out-String
    Log ("phone start: " + ($out -replace '\s+', ' ').Trim())
}
function Start-Desktop {
    if (-not (Test-Path -LiteralPath $pyw)) { Log "desktop python missing: $pyw"; return }
    $p = Start-Process -FilePath $pyw -ArgumentList @('-X', 'utf8', '-u', (Join-Path $app 'main.py')) -WorkingDirectory $app `
        -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir 'desktop.log') -RedirectStandardError (Join-Path $logDir 'desktop.err.log') -PassThru
    Log "desktop started pid=$($p.Id)"
}

# 1) wait for the network to exist
Log "boot: wait up to $WaitSeconds s for network"
$ts = 'C:\Program Files\Tailscale\tailscale.exe'
$deadline = (Get-Date).AddSeconds($WaitSeconds)
while ((Get-Date) -lt $deadline) {
    $dns = $false; try { [void][Net.Dns]::GetHostAddresses('generativelanguage.googleapis.com'); $dns = $true } catch {}
    $tsOk = $true; if (Test-Path -LiteralPath $ts) { & $ts status *> $null; $tsOk = ($LASTEXITCODE -eq 0) }
    if ($dns -and $tsOk) { break }
    Start-Sleep 5
}
Log ("network: dns={0} tailscale={1}" -f $dns, $tsOk)

# 2-4) start + watch
$restarts = @{ phone = @(); desktop = @() }
function Allow([string]$k) {
    $now = Get-Date
    $restarts[$k] = @($restarts[$k] | Where-Object { $_ -gt $now.AddMinutes(-10) })
    if ($restarts[$k].Count -ge 5) { return $false }
    $restarts[$k] += $now; $true
}
$gaveUp = @{}
while ($true) {
    if (-not (Test-Port 10790)) {
        if (Allow 'phone') { Log 'gateway down -> start phone side'; Start-Phone } elseif (-not $gaveUp.phone) { Log 'phone side keeps failing: gave up for 10 min'; $gaveUp.phone = 1 }
    } else { $gaveUp.phone = 0 }
    if (-not $NoDesktop -and -not (Get-Desktop)) {
        if (Allow 'desktop') { Log 'desktop app not running -> start'; Start-Desktop } elseif (-not $gaveUp.desktop) { Log 'desktop app keeps crashing: gave up for 10 min, see desktop.err.log'; $gaveUp.desktop = 1 }
    } else { $gaveUp.desktop = 0 }
    if ($Once) { break }
    Start-Sleep 30
}
