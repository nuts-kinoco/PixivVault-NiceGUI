$ErrorActionPreference = 'Stop'
$evidence = $PSScriptRoot
$workspace = (Resolve-Path (Join-Path $evidence '..\..')).Path
$markers = @($workspace, 'S:\Work\_tmp\PV\d6cg',
    'S:\Work\_tmp\PV\d6real\cardgrid-fake-01',
    'S:\Work\_tmp\PV\d6real\cardgrid-live-01')
$receipts = Get-ChildItem -LiteralPath $evidence -Recurse -File |
    Where-Object { $_.Name -in @('owned-server.json', 'owned-servers.json') }
$pidRecords = @{}
foreach ($receipt in $receipts) {
    $data = Get-Content -LiteralPath $receipt.FullName -Raw | ConvertFrom-Json
    $records = if ($null -ne $data.pid) { @($data) } else { $data.PSObject.Properties.Value }
    foreach ($record in $records) {
        if ($null -ne $record.pid) { $pidRecords[[int]$record.pid] = $receipt.LastWriteTime }
    }
}

$stillRunning = @(Get-CimInstance Win32_Process | Where-Object {
    $item = $_
    if ($item.Name -notmatch '^(python|node|chrome|chromium|msedge|headless_shell|flet).*\.exe$') { return $false }
    if ($pidRecords.ContainsKey([int]$item.ProcessId) -and
        $item.CreationDate -le $pidRecords[[int]$item.ProcessId]) { return $true }
    $command = [string]$item.CommandLine -replace '/', '\'
    $ownedCommand = '(?i)(?:-m\s+pytest|(?:rank_measure|ranking_server|prepare_live|measure)\.py|playwright_chromiumdev_profile|--user-data-dir=)'
    if ($command -notmatch $ownedCommand) { return $false }
    foreach ($marker in $markers) {
        if ($command.IndexOf($marker, [StringComparison]::OrdinalIgnoreCase) -ge 0) { return $true }
    }
    return $false
} | Select-Object ProcessId, Name)
$ports = @(22764, 22830, 22840)
$properties = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties()
$listening = @($properties.GetActiveTcpListeners() | Where-Object { $_.Port -in $ports } |
    Select-Object -ExpandProperty Port -Unique)
$result = [ordered]@{
    tracked_pids = $pidRecords.Count
    still_running = $stillRunning
    listening = $listening
}
$result | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $evidence 'process-check.json') -Encoding ASCII
if ($stillRunning.Count -or $listening.Count) { exit 1 }
