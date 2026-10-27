# ASCII only. Allowlisted Notepad operator (Windows 11 Notepad, WindowsApps package).
# Target = the Notepad tab whose name equals $FileName (a file JARVIS created). Nothing else.
# -Inspect : read-only. Reads the document only while OUR tab is selected; prints length+sha256, never text.
# -SetText : interactive console only. Local challenge -> revalidate -> foreground -> ValuePattern.SetValue
#            -> read back + compare -> revalidate selection. Never saves, never sends keystrokes.
param(
  [Parameter(Mandatory=$true)][string]$FileName,
  [switch]$Inspect,
  [switch]$SetText,
  [switch]$SelfTest,
  [string]$TextBase64 = '',
  [string]$ExpectedBeforeSha256 = '',
  [string]$Transcript = ''
)
$ErrorActionPreference = 'Stop'
if (-not [Environment]::UserInteractive) { throw 'DENIED: interactive Windows session required' }
if ($FileName -notmatch '^jarvis-[0-9a-f]{12}\.txt$') { throw 'DENIED: not a JARVIS target file name' }
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -Namespace JarvisLocal -Name User32 -MemberDefinition @'
[DllImport("user32.dll")] public static extern System.IntPtr GetForegroundWindow();
[DllImport("user32.dll")] public static extern bool SetForegroundWindow(System.IntPtr hWnd);
'@
$AE = [System.Windows.Automation.AutomationElement]
$Scope = [System.Windows.Automation.TreeScope]
$CT = [System.Windows.Automation.ControlType]

function Get-Sha([string]$s) {
  $n = $s -replace "`r`n", "`n" -replace "`r", "`n"
  $sha = [System.Security.Cryptography.SHA256]::Create()
  return ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($n))) -replace '-', '').ToLowerInvariant()
}

function Get-Target {
  $cls = New-Object System.Windows.Automation.PropertyCondition($AE::ClassNameProperty, 'Notepad')
  $found = @()
  foreach ($w in $AE::RootElement.FindAll($Scope::Children, $cls)) {
    $proc = Get-Process -Id $w.Current.ProcessId -ErrorAction SilentlyContinue
    if (-not $proc -or $proc.ProcessName -ne 'Notepad' -or $proc.Path -notlike '*\WindowsApps\Microsoft.WindowsNotepad_*') { continue }
    $tabs = $w.FindAll($Scope::Descendants, (New-Object System.Windows.Automation.PropertyCondition($AE::ControlTypeProperty, $CT::TabItem)))
    foreach ($t in $tabs) {
      # Tab name = file name, optionally followed by a localized state, e.g. "jarvis-x.txt. <not modified>."
      $tn = $t.Current.Name
      if ($tn -ne $FileName -and -not $tn.StartsWith($FileName + '. ') -and -not $tn.StartsWith($FileName + ' ')) { continue }
      $sel = $null
      $selected = $t.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern, [ref]$sel) -and ([System.Windows.Automation.SelectionItemPattern]$sel).Current.IsSelected
      $found += [pscustomobject]@{ Window = $w; Tab = $t; Selected = $selected; Pid = $proc.Id; Hwnd = $w.Current.NativeWindowHandle; TabCount = $tabs.Count }
    }
  }
  if ($found.Count -ne 1) { throw "DENIED: expected exactly one JARVIS tab; found $($found.Count)" }
  $x = $found[0]
  if (-not $x.Selected) { throw 'DENIED: JARVIS tab is not the selected tab; select it and retry' }
  $docs = $x.Window.FindAll($Scope::Descendants, (New-Object System.Windows.Automation.PropertyCondition($AE::ControlTypeProperty, $CT::Document)))
  if ($docs.Count -ne 1 -or $docs.Item(0).Current.ClassName -ne 'RichEditD2DPT') { throw 'DENIED: editor control not unique' }
  $vp = $null
  if (-not $docs.Item(0).TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$vp)) { throw 'DENIED: no ValuePattern' }
  $vp = [System.Windows.Automation.ValuePattern]$vp
  if ($vp.Current.IsReadOnly) { throw 'DENIED: editor is read-only' }
  $x | Add-Member -NotePropertyName Value -NotePropertyValue $vp
  return $x
}

if ($Inspect -or -not $SetText) {
  $t = Get-Target
  $cur = $t.Value.Current.Value
  Write-Host "NOTEPAD_INSPECT=PASS pid=$($t.Pid) hwnd=$($t.Hwnd) tabs=$($t.TabCount) length=$($cur.Length) sha256=$(Get-Sha $cur)"
  Write-Host 'NOTEPAD_READ_ONLY=PASS'
  exit 0
}
if (-not $Transcript -or (Test-Path -LiteralPath $Transcript)) { throw 'DENIED: new transcript path required' }
Start-Transcript -LiteralPath $Transcript | Out-Null
try {
$t = Get-Target
$cur = $t.Value.Current.Value
Write-Host "NOTEPAD_INSPECT=PASS pid=$($t.Pid) hwnd=$($t.Hwnd) tabs=$($t.TabCount) length=$($cur.Length)"
$text = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($TextBase64))
if ($text.Length -lt 1 -or $text.Length -gt 2000) { throw 'DENIED: text length must be 1..2000' }
if ((Get-Sha $cur) -ne $ExpectedBeforeSha256) { throw 'DENIED: document changed since plan' }
if ($SelfTest) {
  # Owner-authorised self-test: no console challenge, but ONLY a fixed, harmless marker string.
  if ($text -cnotmatch '^JARVIS SELFTEST [0-9a-f]{8}$') { throw 'DENIED: self-test may only write the JARVIS SELFTEST marker' }
  Write-Host 'NOTEPAD_SELFTEST=AUTHORISED_MARKER_ONLY'
} else {
  if ([Console]::IsInputRedirected -or [Console]::IsOutputRedirected -or [Console]::IsErrorRedirected) { throw 'DENIED: redirected console' }
  $challenge = ([guid]::NewGuid().ToString('N').Substring(0, 8)).ToUpperInvariant()
  Write-Host "LOCAL APPROVAL: set text of Notepad tab $FileName (pid=$($t.Pid), $($text.Length) chars). Type $challenge to approve:"
  if ([Console]::ReadLine() -cne $challenge) { throw 'NOTEPAD=CANCELLED' }
}

$t2 = Get-Target
if ($t2.Pid -ne $t.Pid -or $t2.Hwnd -ne $t.Hwnd) { throw 'DENIED: target changed' }
if ((Get-Sha $t2.Value.Current.Value) -ne $ExpectedBeforeSha256) { throw 'DENIED: document changed during approval' }
$h = [IntPtr]$t2.Hwnd
[void][JarvisLocal.User32]::SetForegroundWindow($h)
Start-Sleep -Milliseconds 300
$fg = ([JarvisLocal.User32]::GetForegroundWindow() -eq $h)
Write-Host "NOTEPAD_FOREGROUND=$fg"
# A background self-test may be refused focus by Windows; ValuePattern does not need focus.
if (-not $fg -and -not $SelfTest) { throw 'DENIED: could not verify Notepad as foreground window' }
$t3 = Get-Target
if ($t3.Hwnd -ne $t.Hwnd) { throw 'DENIED: target changed before action' }
try { $t3.Value.SetValue($text) } catch { throw "NOTEPAD=UNKNOWN: SetValue failed; do not retry blindly. $($_.Exception.GetType().Name)" }
Start-Sleep -Milliseconds 300
$t4 = Get-Target
$after = Get-Sha $t4.Value.Current.Value
if ($t4.Hwnd -ne $t.Hwnd -or $after -ne (Get-Sha $text)) { throw "NOTEPAD=UNKNOWN: verification failed (after_sha256=$after)" }
Write-Host "NOTEPAD=PASS pid=$($t.Pid) before_sha256=$ExpectedBeforeSha256 after_sha256=$after length=$($text.Length)"
} catch {
  Write-Host ('NOTEPAD_ERROR=' + $_.Exception.Message)
} finally {
  Stop-Transcript | Out-Null
}
