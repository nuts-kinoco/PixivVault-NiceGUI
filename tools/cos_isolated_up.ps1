[CmdletBinding()]
param([string]$Run = 'handoff')
. (Join-Path $PSScriptRoot 'cos_isolated_common.ps1')
$root = Get-CosPath $Run
$source = Split-Path -Parent $PSScriptRoot
$entry = Join-Path $PSScriptRoot 'cos_isolated_fixture.py'
$init = Join-Path $PSScriptRoot 'launch_test_owner.py'
if (Test-Path -LiteralPath $root) { throw 'Run already exists. Use a fresh run name.' }
foreach ($port in $CosPorts) {
    if (Test-CosPort $port) { throw "Port already in use: $port" }
}
New-Item -ItemType Directory -Path (Join-Path $root 'launcher-temp') | Out-Null
$old = @{}
foreach ($name in @('TEMP','TMP','APPDATA','LOCALAPPDATA','PYTHONPATH','PYTHONUTF8',
                    'PYTHONDONTWRITEBYTECODE','NICEGUI_STORAGE_PATH','PV_UI_TONE_CAPTURE',
                    'PV_UI_TONE_PERSONAL_FIXTURE','PV_UIUX_STATE','PV_UI_IMAGE_AREA')) {
    $old[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
try {
    $env:TEMP = Join-Path $root 'launcher-temp'
    $env:TMP = $env:TEMP
    $env:PYTHONPATH = "$source\src;$source\tests"
    $env:PYTHONUTF8 = '1'
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $env:PV_UI_TONE_CAPTURE = $null
    $env:PV_UI_TONE_PERSONAL_FIXTURE = $null
    $env:PV_UIUX_STATE = $null
    $env:PV_UI_IMAGE_AREA = $null
    foreach ($port in $CosPorts) {
        $folder = Join-Path $root $port
        $env:TEMP = Join-Path $root 'launcher-temp'
        $env:TMP = $env:TEMP
        $env:APPDATA = $env:TEMP
        $env:LOCALAPPDATA = $env:TEMP
        & $CosPython -B $init --init-workspace $folder
        if ($LASTEXITCODE) { throw "Workspace initialization failed: $port" }
        $env:TEMP = Join-Path $folder 'tmp'
        $env:TMP = $env:TEMP
        $env:APPDATA = Join-Path $folder 'roaming'
        $env:LOCALAPPDATA = Join-Path $folder 'local'
        $env:NICEGUI_STORAGE_PATH = Join-Path $folder '.nicegui'
        New-Item -ItemType Directory -Path $env:TEMP,$env:APPDATA,$env:LOCALAPPDATA | Out-Null
        [IO.File]::WriteAllText((Join-Path $folder 'control.txt'), [guid]::NewGuid().ToString('N'))
        $process = Start-Process -FilePath $CosPython -ArgumentList "-B -u `"$entry`" $port `"$folder`"" `
            -WorkingDirectory $folder -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $folder 'stdout.log') `
            -RedirectStandardError (Join-Path $folder 'stderr.log')
        try {
            @{id=$process.Id; started=$process.StartTime.ToUniversalTime().Ticks.ToString(); port=$port} |
                ConvertTo-Json -Compress | Set-Content -Encoding ASCII -LiteralPath (Join-Path $folder 'owner.json')
        } catch {
            if (!$process.HasExited) { $process.Kill(); $process.WaitForExit() }
            throw
        }
    }
    foreach ($port in $CosPorts) {
        $folder = Join-Path $root $port
        $record = Get-Content -Raw -LiteralPath (Join-Path $folder 'owner.json') | ConvertFrom-Json
        $limit = [DateTime]::UtcNow.AddSeconds(50)
        $url = "http://127.0.0.1:$port"
        $ready = $false
        do {
            if (!(Get-CosOwnedProcess $record)) { throw "Fixture exited: $port (see stderr.log)" }
            try {
                $probe = Invoke-RestMethod "$url/_smoke/network" -TimeoutSec 3 -Headers (Get-CosHeaders $folder)
                $pageResponse = Invoke-WebRequest "$url/" -UseBasicParsing -TimeoutSec 12
                $ready = $pageResponse.StatusCode -eq 200 -and $pageResponse.RawContentLength -gt 0 -and $probe.denied -eq 0
            } catch {
                $probeError = $_.Exception.GetType().Name
                Start-Sleep -Milliseconds 300
            }
        } while (!$ready -and [DateTime]::UtcNow -lt $limit)
        if (!$ready) { throw "Fixture readiness failed: $port HTTP=$($pageResponse.StatusCode) denied=$($probe.denied) lastError=$probeError" }
        Write-Output "$url/ HTTP 200; external requests admitted 0"
    }
} catch {
    & (Join-Path $PSScriptRoot 'cos_isolated_down.ps1') -Run $Run
    throw
} finally {
    foreach ($name in $old.Keys) {
        [Environment]::SetEnvironmentVariable($name, $old[$name], 'Process')
    }
}
