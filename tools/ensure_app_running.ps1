# Start the real-machine app (port 22386) via launch_work.ps1 if it does not answer, then wait until it does.
# Keep this file ASCII-only: Windows PowerShell 5.1 reads BOM-less UTF-8 as ANSI and garbles other text.
# Do not let COS or another session launch the app (different identity/environment cannot open the shared STOP protection:
# it fell to the startup-settings screen on 2026-11-17 23:29).
param([int]$Port = 22386, [int]$TimeoutSec = 180)
$ErrorActionPreference = 'Stop'
function Test-App {
    try { return ((Invoke-WebRequest -UseBasicParsing -Uri ("http://127.0.0.1:{0}/" -f $Port) -TimeoutSec 3).StatusCode -eq 200) }
    catch { return $false }
}
if (Test-App) { Write-Output ("already running on {0}" -f $Port); exit 0 }
$tag = 'ensure-' + (Get-Date -Format 'HHmmss')
& 'S:\Projects\PV-DEV-20261117\launch_work.ps1' -Tag $tag | Write-Output
$deadline = (Get-Date).AddSeconds($TimeoutSec)
while ((Get-Date) -lt $deadline) {
    if (Test-App) { Write-Output ("up on {0}" -f $Port); exit 0 }
    Start-Sleep -Seconds 2
}
Write-Output ("not up within {0} s" -f $TimeoutSec)
exit 1
