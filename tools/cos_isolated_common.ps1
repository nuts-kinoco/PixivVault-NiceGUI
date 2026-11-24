$ErrorActionPreference = 'Stop'
$CosRoot = 'S:\Work\_tmp\PV\cosprep-20261124'
$CosPython = 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe'
$CosPorts = 22701..22709

function Get-CosPath([string]$Run) {
    if ($Run -cnotmatch '^[a-z0-9][a-z0-9-]{0,47}$') { throw 'Invalid run name.' }
    $path = Join-Path $CosRoot $Run
    for ($folder = $path; $folder; $folder = Split-Path -Parent $folder) {
        if (Test-Path -LiteralPath $folder) {
            if ((Get-Item -LiteralPath $folder -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw 'Fixture roots cannot contain reparse points.'
            }
        }
    }
    return $path
}

function Get-CosOwnedProcess($Record) {
    $process = Get-Process -Id ([int]$Record.id) -ErrorAction SilentlyContinue
    if (!$process) { return }
    if ($process.StartTime.ToUniversalTime().Ticks.ToString() -cne $Record.started -or
        $process.Path -ine $CosPython) { return }
    return $process
}

function Get-CosHeaders([string]$Folder) {
    return @{'x-acceptance' = [IO.File]::ReadAllText((Join-Path $Folder 'control.txt')).Trim()}
}

function Test-CosPort([int]$Port) {
    $client = [Net.Sockets.TcpClient]::new()
    try { $client.Connect('127.0.0.1', $Port); return $true }
    catch [Net.Sockets.SocketException] { return $false }
    finally { $client.Dispose() }
}
