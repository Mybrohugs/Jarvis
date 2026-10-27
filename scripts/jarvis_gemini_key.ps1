# ASCII only. Saves the Gemini API key for the JARVIS voice (Windows user DPAPI, hidden input).
# Get a key at https://aistudio.google.com/apikey . The key is never printed or logged.
$dev = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$py = Join-Path $dev '.venv-integrations\Scripts\python.exe'
Set-Location -LiteralPath $dev
& $py -B -m developer_core.jarvis_local.gemini_voice set-key
Write-Host 'Done. You can close this window.'
