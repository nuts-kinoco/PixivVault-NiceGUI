[CmdletBinding()]
param([string]$Run = 'handoff')
. (Join-Path $PSScriptRoot 'cos_isolated_common.ps1')
$root = Get-CosPath $Run
$issues = @()
foreach ($port in $CosPorts) {
    $folder = Join-Path $root $port
    $manifest = Join-Path $folder 'owner.json'
    if (!(Test-Path -LiteralPath $manifest -PathType Leaf)) { continue }
    try {
        foreach ($path in @($folder,$manifest,(Join-Path $folder 'control.txt'))) {
            if ((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw 'Fixture ownership files cannot be links.'
            }
        }
        $record = Get-Content -Raw -LiteralPath $manifest | ConvertFrom-Json
        if ($record.port -ne $port -or [int]$record.id -le 0) { throw 'Invalid ownership record.' }
        $process = Get-CosOwnedProcess $record
        if ($process) {
            try {
                Invoke-RestMethod "http://127.0.0.1:$port/_smoke/shutdown" -Method Post -TimeoutSec 3 `
                    -Headers (Get-CosHeaders $folder) | Out-Null
            } catch {
                Write-Output "Port $port graceful shutdown unavailable; checking owned PID."
            }
            if (!$process.WaitForExit(12000)) {
                $process.Kill()
                if (!$process.WaitForExit(5000)) { throw 'Owned PID did not exit.' }
            }
        }
        if (Get-CosOwnedProcess $record) { throw 'Owned PID still running.' }
        if (Test-CosPort $port) { throw 'Port still listening; check its owner.' }
        Write-Output "Port $port stopped; owned PID absent; loopback listener absent."
    } catch {
        $issues += "Port ${port}: $_"
    }
}
if ($issues) { throw ($issues -join [Environment]::NewLine) }
