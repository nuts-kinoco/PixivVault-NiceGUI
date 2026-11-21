$ErrorActionPreference = 'Stop'

$records = @(Get-ChildItem -LiteralPath $PSScriptRoot -Directory |
    ForEach-Object { Get-ChildItem -LiteralPath $_.FullName -File } |
    Where-Object Name -Match '^(nice|original)-(browser-(startup|shutdown)|server-tree|owned)\.json$')
$runners = @(Get-ChildItem -LiteralPath $PSScriptRoot -File |
    Where-Object Name -Match '^(browser|pytest)-.*-owned\.json$')
$seen = @{}
foreach ($record in ($records + $runners)) {
    $json = Get-Content -LiteralPath $record.FullName -Raw | ConvertFrom-Json
    $ids = if ($record.Name -like '*-owned.json') { @($json.pid) } else { @($json) }
    foreach ($processId in $ids) {
        $processId = [int]$processId
        if ($processId -gt 0 -and (-not $seen.ContainsKey($processId) -or
            $record.LastWriteTimeUtc -gt $seen[$processId])) {
            $seen[$processId] = $record.LastWriteTimeUtc
        }
    }
}

$processes = @(Get-CimInstance Win32_Process)
$matched = @($processes | Where-Object { $seen.ContainsKey([int]$_.ProcessId) })
$live = @($matched | Where-Object { $_.CreationDate.ToUniversalTime() -le $seen[[int]$_.ProcessId] })
$reused = @($matched | Where-Object { $_.CreationDate.ToUniversalTime() -gt $seen[[int]$_.ProcessId] })
$byPath = @($processes | Where-Object {
    $_.Name -in @('chrome.exe','chrome-headless-shell.exe','node.exe','python.exe','pythonw.exe') -and
    $_.CommandLine -and $_.CommandLine -match 'd6e-20261121|\\PV\\d6e\\'
})
$ports = @(Get-NetTCPConnection -LocalPort 22730,22731,22732,22740 -State Listen -ErrorAction SilentlyContinue |
    Select-Object LocalAddress,LocalPort,OwningProcess)
$fields = @('Name','ProcessId','ParentProcessId','CreationDate')
$result = [ordered]@{
    checked_utc = [DateTime]::UtcNow.ToString('o')
    captured_pids = @($seen.Keys | Sort-Object)
    recorded_live = @($live | Select-Object $fields)
    reused_pids = @($reused | Select-Object $fields)
    owned_profile_or_command_matches = @($byPath | Select-Object $fields)
    measurement_port_listeners = $ports
    remaining_count = @(($live + $byPath) | Select-Object -ExpandProperty ProcessId -Unique).Count
}
$result | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath "$PSScriptRoot\final-processes.json" -Encoding Ascii
Write-Output "Recorded PIDs: $($seen.Count); remaining: $($result.remaining_count); listening ports: $($ports.Count)"
