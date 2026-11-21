param(
    [Parameter(Mandatory)][ValidateSet('timings', 'regression', 'pytest')][string]$Mode,
    [ValidatePattern('^[a-z0-9-]*$')][string]$Tag = ''
)

$ErrorActionPreference = 'Stop'
$tree = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$prior = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $tree
$env:TEMP = 'S:\Work\_tmp\PV\d6e'
$env:TMP = $env:TEMP
$env:PYTHONUTF8 = '1'
$env:PYTHONPATH = "$tree\src;$tree\tests"
$python = 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe'
$env:PV_D6_CODE_REV = git rev-parse HEAD
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$suffix = if ($Tag) { "-$Tag" } else { '' }
@{
    pid = $PID
    started = [DateTime]::UtcNow.ToString('o')
    mode = $Mode
    revision = $env:PV_D6_CODE_REV
    workspace = $tree
} | ConvertTo-Json | Set-Content -LiteralPath "$PSScriptRoot\$Mode$suffix-owned.json" -Encoding Ascii

function Invoke-Logged {
    param([string]$Name, [string[]]$Arguments)
    $ErrorActionPreference = 'Continue'
    & $python -B @Arguments *>&1 | Out-File -LiteralPath "$PSScriptRoot\$Name.log"
    $result = $LASTEXITCODE
    $result | Set-Content -LiteralPath "$PSScriptRoot\$Name.exit" -Encoding Ascii
    if ($result -ne 0) { $script:failed = $true }
    Write-Output "$Name exit $result"
}

$failed = $false
foreach ($var in @('PV_D6_SOURCE', 'PV_D6_SEARCH_PATTERN', 'PV_D6_VARIANT', 'PV_D6_PIXELS',
                   'PV_D6_VIEWER_CONTRACT', 'PV_D6_EXTRA_PAGES')) {
    Remove-Item "Env:$var" -ErrorAction SilentlyContinue
}
$env:PV_P6_SEGMENTS = '0'
$measure = Join-Path $prior 'measure.py'
if ($Mode -eq 'timings') {
    $env:PV_D6_EXTRA_PAGES = '34'
    $env:PV_D6_SEARCH_PATTERN = '1-to-1'
    $env:PV_P6_SEGMENTS = '1'
    foreach ($pixels in @('included', 'detached')) {
        $env:PV_D6_PIXELS = $pixels
        $name = "viewer-learning-$pixels$suffix"
        Invoke-Logged $name @($measure, '--out', "$PSScriptRoot\$name",
            '--only', 'nice', '--repetitions', '30', '--case', 'learning', '--case', 'viewer_next')
    }
    $name = "quiet-viewer-detached$suffix"
    Invoke-Logged $name @($measure, '--out', "$PSScriptRoot\$name", '--only', 'nice',
        '--repetitions', '30', '--case', 'viewer_next')
    $env:PV_D6_EXTRA_PAGES = '24'
    $env:PV_P6_SEGMENTS = '0'
    foreach ($pixels in @('included', 'detached')) {
        $env:PV_D6_PIXELS = $pixels
        $name = "search-$pixels$suffix"
        Invoke-Logged $name @((Join-Path $prior 'r2\search_pixels.py'), '--out',
            "$PSScriptRoot\$name", '--repetitions', '20')
    }
} elseif ($Mode -eq 'regression') {
    Invoke-Logged "viewer-contract$suffix" @($measure, '--out', "$PSScriptRoot\viewer-contract$suffix", '--only', 'nice',
        '--case', 'viewer_contract')
    Invoke-Logged "reconnect$suffix" @((Join-Path $prior 'r2\reconnect_contract.py'), '--out', "$PSScriptRoot\reconnect$suffix")
} else {
    # tmp_path plus the test's 110-character leaf must exceed 180 characters.
    $base = Join-Path $env:TEMP "r3-pytest-full-windows-long-path-candidate-20261121$suffix"
    Invoke-Logged "pytest-full$suffix" @('-m', 'pytest', '-p', 'required_network_guard', '-p', 'no:cacheprovider',
        '-k', 'not dpapi', '--basetemp', $base)
}
exit [int]$failed
