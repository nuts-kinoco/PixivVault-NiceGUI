$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$tempRoot = 'S:\Work\_tmp\PV\d6cg'
$baselineSrc = Join-Path $tempRoot 'd9b8a27-r2-baseline\src'
$python = 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe'
$measure = (Join-Path $repo 'verify\d6cardgrid-20261121\measure.py')
if (!(Test-Path -LiteralPath $baselineSrc -PathType Container)) {
    throw 'The round-one source archive must be extracted before running the comparison.'
}
$env:PYTHONUTF8 = '1'
$env:TEMP = $tempRoot
$env:TMP = $tempRoot
$env:TMPDIR = $tempRoot
$env:PLAYWRIGHT_BROWSERS_PATH = "$env:LOCALAPPDATA\ms-playwright"
foreach ($batch in @('before-1', 'after-1', 'after-2', 'before-2')) {
    $source = Join-Path $repo 'src'
    if ($batch -like 'before-*') { $source = $baselineSrc }
    $env:PYTHONPATH = "$source;$repo"
    $destination = Join-Path $PSScriptRoot "paired-$batch"
    $sandbox = Join-Path $tempRoot "r2-paired-cardgrid-$batch-20261121"
    & $python -B $measure $sandbox --width 1280 --repetitions 3 --all-screenshots
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    New-Item -ItemType Directory -Path $destination | Out-Null
    foreach ($name in @('raw.json', 'owned-server.json')) {
        Copy-Item -LiteralPath (Join-Path $sandbox $name) -Destination $destination
    }
}
& $python -B (Join-Path $PSScriptRoot 'aggregate_pair.py')
exit $LASTEXITCODE
