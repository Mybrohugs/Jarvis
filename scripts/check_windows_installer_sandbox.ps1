# Runs only in a disposable Windows Sandbox. Never map host account data.
$ErrorActionPreference='Stop'
if ($env:USERNAME -ne 'WDAGUtilityAccount') { throw 'Windows Sandbox only' }
$report='C:\JARVIS-Test'
$result=@{success=$false;clean_windows=$true;network_disabled=$true;checks=@()}
function Progress([string]$phase) { @{phase=$phase;utc=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json | Set-Content (Join-Path $report 'progress.json') -Encoding utf8 }
function RunWait([string]$file,[string[]]$arguments,[string]$label,[bool]$mustPass=$true) {
    Progress $label
    $process=Start-Process -FilePath $file -ArgumentList $arguments -WindowStyle Hidden -Wait -PassThru
    $result.checks+=@{name=$label;exit=$process.ExitCode}
    if ($mustPass -and $process.ExitCode -ne 0) { throw ($label+' failed: '+$process.ExitCode) }
    return $process.ExitCode
}
function ReadJson([string]$file) { Get-Content -LiteralPath $file -Raw -Encoding utf8 | ConvertFrom-Json }
function Check([bool]$condition,[string]$label) { if (-not $condition) { throw $label };$result[$label]=$true }
function RunApp([string]$exe,[string]$mode,[string]$label) {
    $output=Join-Path $report ($label+'.json')
    $null=RunWait $exe @('--'+$mode,'--quiet','--output',('"'+$output+'"')) $label
    return ReadJson $output
}
function AutorunSnapshot {
    $values=@()
    foreach ($path in @('HKCU:\Software\Microsoft\Windows\CurrentVersion\Run','HKLM:\Software\Microsoft\Windows\CurrentVersion\Run')) {
        if (Test-Path $path) { $values+=((Get-ItemProperty $path | Select-Object * -ExcludeProperty PS*) | ConvertTo-Json -Compress) }
    }
    return ($values -join '|')
}
try {
    $null=RunWait 'C:\JARVIS-Build\compiler.exe' @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-','/CURRENTUSER','/NOICONS','/DIR=C:\Inno') 'compiler-install'
    Progress 'compiling'
    New-Item -ItemType Directory -Path (Join-Path $report 'output') -Force | Out-Null
    $compiler=Start-Process -FilePath 'C:\Inno\ISCC.exe' -ArgumentList @('/Qp','/DBundleDir=C:\JARVIS-Payload','/DOutputDir=C:\JARVIS-Test\output','C:\JARVIS-Build\jarvis-dev.iss') -WindowStyle Hidden -Wait -PassThru -RedirectStandardOutput (Join-Path $report 'compile.log') -RedirectStandardError (Join-Path $report 'compile-errors.log')
    Check ($compiler.ExitCode -eq 0) 'compiler_passed'
    $setup=Join-Path $report 'output\JARVIS-Setup-v1.4.3-windows-x64.exe'
    Check (Test-Path -LiteralPath $setup) 'setup_created'
    $root=Join-Path $env:LOCALAPPDATA 'JARVIS-DEV-Station'
    $data=Join-Path $root 'data';New-Item -ItemType Directory -Path $data -Force | Out-Null
    $marker=Join-Path $data 'preserve-settings.test';Set-Content $marker 'preserve user settings' -Encoding ascii
    $shared=Join-Path $env:APPDATA 'JarvisChatGPTPlan';New-Item -ItemType Directory -Path $shared -Force | Out-Null
    $account=Join-Path $shared 'preserve-account.test';Set-Content $account 'preserve account store' -Encoding ascii
    $markerHash=(Get-FileHash $marker).Hash;$accountHash=(Get-FileHash $account).Hash
    $autorun=AutorunSnapshot
    $setupArgs=@('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-','/LANG=russian')
    $null=RunWait $setup ($setupArgs+@('/LOG=C:\JARVIS-Test\install.log')) 'fresh-install'
    $current=ReadJson (Join-Path $root 'current.json')
    Check ($current.version -eq '1.4.3') 'fresh_version_selected'
    $exe=Join-Path $root 'versions\1.4.3\JARVIS-DEV.exe'
    $manifest=ReadJson (Join-Path $root 'versions\1.4.3\package-manifest.json')
    Progress 'hash-verification'
    foreach ($entry in $manifest.files.PSObject.Properties) {
        $path=Join-Path (Join-Path $root 'versions\1.4.3') $entry.Name
        if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry.Value) { throw 'Installed payload checksum mismatch' }
    }
    Check (@($manifest.files.PSObject.Properties).Count -gt 1700) 'payload_hashes_verified'
    $doctor=RunApp $exe 'doctor' 'doctor'
    Check ($doctor.frozen -and $doctor.ffmpeg -and $doctor.browser -and -not $doctor.chatgpt_configured) 'offline_dependencies_ready'
    $gui=RunApp $exe 'smoke-gui' 'installed-gui'
    Check ($gui.gui_smoke -eq 'PASS') 'gui_ready'
    $self=RunApp $exe 'self-test' 'installed-self-test'
    Check ($self.self_test -eq 'PASS') 'browser_ffmpeg_executed'
    $shortcut=Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\JARVIS DEV Station\JARVIS DEV Station.lnk'
    Check (Test-Path $shortcut) 'start_menu_shortcut'
    $key='HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{D29153E1-6143-439A-9F9C-C32C68982D13}_is1'
    Check (Test-Path $key) 'per_user_uninstall_registered'
    Check (-not (Test-Path ($key.Replace('HKCU:','HKLM:')))) 'no_machine_install_registration'
    $null=RunWait $setup $setupArgs 'repeat-install'
    Check ((ReadJson (Join-Path $root 'current.json')).version -eq '1.4.3') 'repeat_install_selected_same_version'
    $lock=Join-Path $data 'desktop-instance.lock'
    $held=[IO.File]::Open($lock,[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::ReadWrite)
    try {
        $exit=RunWait $setup $setupArgs 'running_station_guard' $false
        Check ($exit -ne 0) 'running_station_rejected_without_kill'
    } finally { $held.Dispose() }
    $wrong='C:\Not-JARVIS-DEV'
    $exit=RunWait $setup ($setupArgs+@('/DIR='+$wrong)) 'wrong_target_guard' $false
    Check ($exit -ne 0 -and -not (Test-Path $wrong)) 'outside_target_rejected'
    $uninstall=Join-Path $root 'uninstall\unins000.exe'
    $null=RunWait $uninstall @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART') 'uninstall'
    Check (-not (Test-Path $exe)) 'installed_payload_removed'
    Check (-not (Test-Path $shortcut)) 'shortcut_removed'
    Check (-not (Test-Path $key)) 'uninstall_registration_removed'
    Check ((Get-FileHash $marker).Hash -eq $markerHash) 'user_settings_preserved'
    Check ((Get-FileHash $account).Hash -eq $accountHash) 'shared_account_preserved'
    Check ((AutorunSnapshot) -eq $autorun) 'autorun_unchanged'
    if (Test-Path 'C:\JARVIS-Previous\JARVIS-DEV.exe') {
        $null=RunApp 'C:\JARVIS-Previous\JARVIS-DEV.exe' 'install' 'previous-install'
        $null=RunWait $setup $setupArgs 'upgrade-install'
        $current=ReadJson (Join-Path $root 'current.json')
        Check ($current.version -eq '1.4.3' -and $current.previous -eq '1.4.2') 'previous_version_retained'
        $null=RunApp $exe 'rollback' 'rollback'
        $current=ReadJson (Join-Path $root 'current.json')
        Check ($current.version -eq '1.4.2' -and $current.previous -eq '1.4.3') 'rollback_selected_previous'
        $null=RunApp (Join-Path $root 'versions\1.4.2\JARVIS-DEV.exe') 'smoke-gui' 'previous-gui'
        $null=RunApp $exe 'rollback' 'restore-current'
        Check ((ReadJson (Join-Path $root 'current.json')).version -eq '1.4.3') 'restore_current_passed'
        $null=RunWait $uninstall @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART') 'upgraded-uninstall'
        Check (Test-Path (Join-Path $root 'versions\1.4.2\JARVIS-DEV.exe')) 'older_portable_version_preserved'
        Check ((Get-FileHash $marker).Hash -eq $markerHash) 'settings_preserved_after_upgrade_removal'
    }
    $result.setup_sha256=(Get-FileHash $setup -Algorithm SHA256).Hash.ToLowerInvariant()
    $result.success=$true;Progress 'complete'
} catch { $result.error=$_.Exception.Message;Progress 'failed' }
$result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $report 'result.json') -Encoding utf8
Start-Sleep -Seconds 2
& "$env:SystemRoot\System32\shutdown.exe" /s /t 0
