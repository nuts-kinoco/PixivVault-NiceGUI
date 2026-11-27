param(
    [string]$WorkRoot = 'S:\Projects\COS-PV433-STARTUP-20261116-r1',
    [int]$Port = 22386,
    [string]$Tag = (Get-Date -Format 'yyyyMMdd-HHmm')
)
$ErrorActionPreference = 'Stop'
$python = 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe'
$logDir = 'S:\Projects\PV-DEV-20261117\logs'
New-Item -ItemType Directory -Force $logDir, (Join-Path $WorkRoot 'temp') | Out-Null
$env:PYTHONPATH = 'S:\Projects\PV-DEV-20261117\work\src'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'
$env:TEMP = Join-Path $WorkRoot 'temp'
$env:TMP = $env:TEMP
$arguments = @('-B',(Join-Path $PSScriptRoot 'launch_side_monitor.py'),'--host','127.0.0.1','--port',[string]$Port,'--native','--profile-root',(Join-Path $WorkRoot 'profile-native'))
$quoted = @($arguments | ForEach-Object { '"' + $_ + '"' })
$out = Join-Path $logDir ("work-$Tag-stdout.log")
$err = Join-Path $logDir ("work-$Tag-stderr.log")
foreach ($f in @($out, $err)) { if (Test-Path $f) { throw "refusing to overwrite $f" } }
$process = Start-Process -FilePath $python -ArgumentList $quoted -WorkingDirectory $WorkRoot -PassThru -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err
Write-Output ('Owned PID: ' + $process.Id + '  Port: ' + $Port + '  logs: ' + $out)
