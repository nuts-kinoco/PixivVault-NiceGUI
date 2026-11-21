param(
    [ValidateSet('browser', 'pytest')]
    [string]$Mode,
    [string]$Tag = 'final'
)

$ErrorActionPreference = 'Stop'
$tree = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location -LiteralPath $tree
$env:TEMP = 'S:\Work\_tmp\PV\d6e'
$env:TMP = $env:TEMP
$env:PYTHONUTF8 = '1'
$env:PYTHONPATH = "$tree\tests;$tree\src"
$python = 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe'
$revision = git rev-parse HEAD
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$env:PV_D6_CODE_REV = $revision
$script:runFailed = $false
$receipt = [ordered]@{
    pid = $PID
    started = [DateTime]::UtcNow.ToString('o')
    revision = $revision
    mode = $Mode
    workspace = $tree
}
$receipt | ConvertTo-Json | Set-Content -LiteralPath "$PSScriptRoot\$Mode-$Tag-owned.json" -Encoding Ascii

function Invoke-Logged {
    param([string]$Name, [string[]]$Arguments)
    $ErrorActionPreference = 'Continue'
    $log = Join-Path $PSScriptRoot "$Name.log"
    & $python -B @Arguments *>&1 | Out-File -LiteralPath $log
    $result = $LASTEXITCODE
    $result | Set-Content -LiteralPath "$PSScriptRoot\$Name.exit" -Encoding Ascii
    if ($result -ne 0) { $script:runFailed = $true }
    Write-Output "$Name exit $result"
}

if ($Mode -eq 'pytest') {
    $base = Join-Path $env:TEMP "pytest-candidate-full-20261121-$Tag"
    Invoke-Logged -Name "pytest-$Tag" -Arguments @(
        '-m', 'pytest', '-p', 'required_network_guard', '-p', 'no:cacheprovider',
        '-k', 'not dpapi', '--basetemp', $base
    )
} else {
    $measure = Join-Path $PSScriptRoot 'measure.py'
    foreach ($var in @('PV_D6_SOURCE', 'PV_D6_SEARCH_PATTERN', 'PV_D6_VARIANT', 'PV_D6_PIXELS', 'PV_D6_EXTRA_PAGES')) {
        Remove-Item "Env:$var" -ErrorAction SilentlyContinue
    }
    $env:PV_P6_SEGMENTS = '0'
    $name = "viewer-contract-$Tag"
    Invoke-Logged -Name $name -Arguments @(
        $measure, '--out', (Join-Path $PSScriptRoot $name), '--only', 'nice', '--case', 'viewer_contract')
    $env:PV_P6_SEGMENTS = '1'
    $name = "viewer-segments-$Tag"
    Invoke-Logged -Name $name -Arguments @(
        $measure, '--out', (Join-Path $PSScriptRoot $name), '--only', 'nice', '--case', 'viewer_next')
    $env:PV_P6_SEGMENTS = '0'
    $env:PV_D6_EXTRA_PAGES = '34'
    foreach ($pixels in @('included', 'detached')) {
        $env:PV_D6_PIXELS = $pixels
        $name = "paired-$pixels-$Tag"
        Invoke-Logged -Name $name -Arguments @(
            $measure, '--out', (Join-Path $PSScriptRoot $name), '--repetitions', '30',
            '--case', 'learning', '--case', 'library_search', '--case', 'viewer_next')
    }
}
exit [int]$script:runFailed
