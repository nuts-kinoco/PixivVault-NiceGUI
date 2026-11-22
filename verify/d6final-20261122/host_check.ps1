param(
    [Parameter(Mandatory = $true)][string]$EvidencePath,
    [string]$RunRoot,
    [string]$SeenPath
)

$ErrorActionPreference = 'Stop'
$started = [DateTimeOffset]::Now
$before = @{}
Get-Process | ForEach-Object { $before[[int]$_.Id] = $_.CPU }
$snapshot = Get-CimInstance Win32_Process
Start-Sleep -Seconds 3
$after = Get-Process
$seconds = ([DateTimeOffset]::Now - $started).TotalSeconds
$cores = [Environment]::ProcessorCount
$cpu = @{}
foreach ($process in $after) {
    if ($null -ne $process.CPU -and $null -ne $before[[int]$process.Id]) {
        $difference = [Math]::Max(0, $process.CPU - $before[[int]$process.Id])
        $cpu[[int]$process.Id] = [Math]::Round(100 * $difference / ($seconds * $cores), 2)
    }
}

$self = $snapshot | Where-Object ProcessId -eq $PID
$parents = @($PID)
while ($self -and $self.ParentProcessId -notin $parents) {
    $parentId = $self.ParentProcessId
    $parents += $parentId
    $self = $snapshot | Where-Object ProcessId -eq $parentId
}
$workdir = 'd6-final-20261122'
$owned = @{}
foreach ($entry in $snapshot) {
    $command = [string]$entry.CommandLine
    if ($command -match $workdir -and
        $command -match 'rank_measure|ranking_server|run_live|--user-data-dir=|pytest.+paired-ranking') {
        $owned[[int]$entry.ProcessId] = $true
    }
}
do {
    $ownedCount = $owned.Count
    foreach ($entry in $snapshot) {
        if ($owned.ContainsKey([int]$entry.ParentProcessId)) {
            $owned[[int]$entry.ProcessId] = $true
        }
    }
} while ($owned.Count -gt $ownedCount)

$ema = @()
$other = @()
$browsers = @()
$idleAgents = @()
$emaPids = @($snapshot | Where-Object {
    $_.Name -match '^(python|node|llama).*\.exe$' -and
    ([string]$_.CommandLine) -match '(?i)emakino|emakinoco|comfyui|[\\/]ema[\\/]'
} | Select-Object -ExpandProperty ProcessId)
foreach ($entry in $snapshot) {
    $processId = [int]$entry.ProcessId
    if ($processId -in $parents -or $owned.ContainsKey($processId)) { continue }
    $command = [string]$entry.CommandLine
    $used = $cpu[$processId]
    $kind = $null
    if ($entry.Name -match '^(chrome|chromium|chrome-headless-shell|headless_shell|msedge)\.exe$') {
        if ($command -match '--headless|--remote-debugging-pipe|--enable-automation') {
            $kind = 'browser_automation'
        } else {
            $browsers += $used
        }
    } elseif ($processId -in $emaPids) {
        $ema += $used
    } elseif ($entry.Name -match '^(python|node|codex|claude).*\.exe$') {
        if ($command -match '\bpytest\b') {
            $kind = 'pytest'
        } elseif ($command -match '(?i)codex(?:\.exe)?[" ]+exec|sol[-_ ]checker|job_runner') {
            $tree = @($processId)
            do {
                $treeSize = $tree.Count
                $tree += @($snapshot | Where-Object {
                    $_.ParentProcessId -in $tree -and $_.ProcessId -notin $tree
                } | Select-Object -ExpandProperty ProcessId)
            } while ($tree.Count -gt $treeSize)
            $used = [Math]::Round((($tree | Where-Object { $_ -notin $emaPids } |
                ForEach-Object { $cpu[[int]$_] } |
                Measure-Object -Sum).Sum), 2)
            if ($used -ge 1) {
                $kind = 'agent_job'
            } else {
                $idleAgents += [ordered]@{pid = $processId; cpu_pct = $used}
            }
        } elseif ($used -ge 5) {
            $kind = 'other_cpu_worker'
        }
    } elseif ($entry.Name -ne 'Idle' -and $used -ge 5) {
        $kind = 'other_cpu_worker'
    }
    if ($kind) {
        $other += [ordered]@{kind = $kind; pid = $processId; cpu_pct = $used}
    }
}
$emaCpu = [Math]::Round((($ema | Measure-Object -Sum).Sum), 2)
$browserCpu = [Math]::Round((($browsers | Measure-Object -Sum).Sum), 2)
$runProcesses = @()
if ($RunRoot) {
    $receipts = foreach ($fileName in 'owned-servers.json','probe-servers.json') {
        $receipt = Join-Path $RunRoot $fileName
        if (Test-Path -LiteralPath $receipt) {
            (Get-Content -LiteralPath $receipt -Raw | ConvertFrom-Json).PSObject.Properties.Value
        }
    }
    $recorded = @($receipts | ForEach-Object { [int]$_.pid } | Sort-Object -Unique)
    $seen = @{}
    if ($SeenPath) {
        $prior = Get-Content -LiteralPath $SeenPath -Raw | ConvertFrom-Json
        foreach ($entry in $prior.owned_running) {
            $seen[[int]$entry.pid] = [DateTimeOffset]::Parse($entry.started).UtcDateTime
        }
    }
    $runProcesses = @($snapshot | Where-Object {
        $processId = [int]$_.ProcessId
        ($processId -notin $parents) -and
        ($processId -in $recorded -or
         ($seen.ContainsKey($processId) -and $_.CreationDate.ToUniversalTime() -eq $seen[$processId]) -or
         (([string]$_.CommandLine).Replace('/', '\').Contains($RunRoot.Replace('/', '\')) -and
          $_.Name -match '^(python|node|chrome|chromium|headless_shell|msedge).*\.exe$'))
    } | Select-Object @{n='pid';e={$_.ProcessId}}, @{n='name';e={$_.Name}},
        @{n='started';e={$_.CreationDate.ToUniversalTime().ToString('o')}})
}
$ports = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties()
$systemCpu = [Math]::Round((($cpu.GetEnumerator() | Where-Object Key -ne 0 |
    ForEach-Object Value | Measure-Object -Sum).Sum), 2)
# A single logical CPU is low host load on this host. Round the sampled
# core-equivalent to allow for timer quantization at one fully used core.
$emaCores = $emaCpu * $cores / 100
$result = [ordered]@{
    observed_at = [DateTimeOffset]::Now.ToString('o')
    sample_seconds = [Math]::Round($seconds, 2)
    cpu_pct = [Math]::Min(100, $systemCpu)
    cpu_method = 'summed_sampled_process_cpu'
    ema_cpu_pct = $emaCpu
    ema_core_equivalents = [Math]::Round($emaCores, 2)
    ordinary_browser_cpu_pct = $browserCpu
    other_heavy_processes = $other
    idle_agent_processes = $idleAgents
    quiet = ($other.Count -eq 0 -and [Math]::Round($emaCores) -le 1 -and $browserCpu -lt 5)
    owned_running = $runProcesses
    measurement_ports_listening = @($ports.GetActiveTcpListeners() |
        Where-Object { $_.Port -in 22830,22840 } | Select-Object -ExpandProperty Port -Unique)
}
$result | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $EvidencePath -Encoding ASCII
