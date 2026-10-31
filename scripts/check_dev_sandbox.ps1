$ErrorActionPreference='Stop'
if($env:USERNAME -ne 'WDAGUtilityAccount'){throw 'This script runs only inside Windows Sandbox'}
$report='C:\JARVIS-Test'
$result=@{clean_windows=$true;network_disabled=$true;host_credentials_mapped=$false;checks=@();success=$false}
try {
    foreach($mode in @('doctor','smoke-gui','self-test','install')){
        $output=Join-Path $report ($mode+'.json')
        $process=Start-Process -FilePath 'C:\JARVIS-DEV\JARVIS-DEV.exe' -ArgumentList @('--'+$mode,'--quiet','--output',$output) -WindowStyle Hidden -Wait -PassThru
        if($process.ExitCode -ne 0){throw ('Mode failed: '+$mode)}
        $data=Get-Content -LiteralPath $output -Raw -Encoding utf8 | ConvertFrom-Json
        if($mode -eq 'doctor' -and ($data.chatgpt_configured -or -not $data.ffmpeg -or -not $data.browser -or -not $data.frozen)){throw 'Unexpected clean Windows dependency status'}
        $result.checks+=@{mode=$mode;exit=0}
    }
    $installed=Join-Path $env:LOCALAPPDATA 'JARVIS-DEV-Station'
    $current=Get-Content -LiteralPath (Join-Path $installed 'current.json') -Raw | ConvertFrom-Json
    $exe=Join-Path $installed ('versions\'+$current.version+'\JARVIS-DEV.exe')
    $process=Start-Process -FilePath $exe -ArgumentList @('--smoke-gui','--quiet','--output',(Join-Path $report 'installed-gui.json')) -WindowStyle Hidden -Wait -PassThru
    if($process.ExitCode -ne 0){throw 'Installed GUI failed'}
    $result.installed_gui=$true;$result.success=$true
} catch {$result.error=$_.Exception.Message}
$result | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $report 'result.json') -Encoding utf8
Start-Sleep -Seconds 2
& "$env:SystemRoot\System32\shutdown.exe" /s /t 0
