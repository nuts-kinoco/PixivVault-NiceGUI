$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$tempRoot = 'S:\Work\_tmp\PV\d6cg'
$before = Join-Path $tempRoot 'r4-source-9d5afcc\src'
$after = Join-Path $repo 'src'
$python = 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe'
$measure = Join-Path $repo 'verify\d6cardgrid-20261121\measure.py'
$env:PYTHONUTF8 = '1'
$env:TEMP = $tempRoot
$env:TMP = $tempRoot
$env:TMPDIR = $tempRoot
$env:PLAYWRIGHT_BROWSERS_PATH = "$env:LOCALAPPDATA\ms-playwright"
if (!(Test-Path -LiteralPath $before -PathType Container)) {
    $archive = Join-Path $tempRoot 'r4-source-9d5afcc.zip'
    git -C $repo archive --format=zip -o $archive 9d5afcc src
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Expand-Archive -LiteralPath $archive -DestinationPath (Split-Path $before)
}
foreach ($batch in @('before-1', 'after-1', 'after-2', 'before-2')) {
    $source = $after
    if ($batch -like 'before-*') { $source = $before }
    $env:PYTHONPATH = "$source;$repo"
    $sandbox = Join-Path $tempRoot "r4-cardgrid-speed-$batch-20261121"
    $output = Join-Path $PSScriptRoot $batch
    & $python -B $measure $sandbox --width 1280 --repetitions 3 --all-screenshots --trace-images --partial-overlap
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    New-Item -ItemType Directory -Path $output | Out-Null
    foreach ($name in @('raw.json', 'image-trace.json', 'owned-server.json')) {
        Copy-Item -LiteralPath (Join-Path $sandbox $name) -Destination $output
    }
}
& $python -B (Join-Path $PSScriptRoot 'aggregate.py')
exit $LASTEXITCODE
