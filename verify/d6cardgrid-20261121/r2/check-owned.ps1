$ErrorActionPreference = 'Stop'
$out = $PSScriptRoot
$markers = @($out, 'S:\Work\_tmp\PV\d6cg\r2-', 'S:\Work\_tmp\PV\d6cg\d9b8a27-r2-baseline')
$records = @{}
$ports = @()
foreach ($receipt in Get-ChildItem -LiteralPath $out -Recurse -File -Filter '*owned-server.json') {
    $data = Get-Content -LiteralPath $receipt.FullName -Raw | ConvertFrom-Json
    $records[[int]$data.pid] = $receipt.LastWriteTime
    if ($data.port) { $ports += [int]$data.port } else { $ports += 22764 }
}
$observed = @{}
$fullReceipt = Join-Path $out 'full-owned-processes.json'
$observedProcesses = Get-Content -LiteralPath $fullReceipt -Raw | ConvertFrom-Json
foreach ($process in $observedProcesses) {
    $observed[[int]$process.pid] = ([DateTimeOffset]$process.started).UtcDateTime
}
$active = @(Get-CimInstance Win32_Process | Where-Object {
    $process = $_
    if ($process.Name -notmatch '^(python|node|chrome|chromium|headless_shell|msedge).*\.exe$') { return $false }
    $pidKey = [int]$process.ProcessId
    if ($observed.ContainsKey($pidKey) -and $process.CreationDate.ToUniversalTime() -eq $observed[$pidKey]) { return $true }
    if ($records.ContainsKey($pidKey) -and $process.CreationDate -le $records[$pidKey]) { return $true }
    $command = [string]$process.CommandLine -replace '/', '\'
    if ($command -notmatch '-m\s+pytest|probe\.py|measure\.py|--user-data-dir=') { return $false }
    foreach ($marker in $markers) {
        if ($command.IndexOf($marker, [StringComparison]::OrdinalIgnoreCase) -ge 0) { return $true }
    }
    return $false
} | Select-Object ProcessId, Name)
$properties = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties()
$listening = @($properties.GetActiveTcpListeners() | Where-Object { $_.Port -in $ports } |
               Select-Object -ExpandProperty Port -Unique)
$result = [ordered]@{
    tracked_server_pids = $records.Count
    tracked_pytest_pids = $observed.Count
    still_running = $active
    listening = $listening
}
$result | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $out 'process-check.json') -Encoding ASCII
if ($active.Count -or $listening.Count) { exit 1 }
