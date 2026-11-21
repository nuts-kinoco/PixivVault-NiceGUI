$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$tempRoot = 'S:\Work\_tmp\PV\d6cg'
$before = Join-Path $tempRoot 'r3-source-172e7d7\src'
$oneRow = Join-Path $tempRoot 'r3-source-one-eager-row\src'
$after = Join-Path $repo 'src'
$python = 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe'
$measure = Join-Path $repo 'verify\d6cardgrid-20261121\measure.py'
$env:PYTHONUTF8 = '1'
$env:TEMP = $tempRoot
$env:TMP = $tempRoot
$env:TMPDIR = $tempRoot
$env:PLAYWRIGHT_BROWSERS_PATH = "$env:LOCALAPPDATA\ms-playwright"

if (!(Test-Path -LiteralPath $before -PathType Container) -or
    !(Test-Path -LiteralPath $oneRow -PathType Container)) {
    $archive = Join-Path $tempRoot 'r3-source-172e7d7.zip'
    git -C $repo archive --format=zip -o $archive 172e7d7 src
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    if (!(Test-Path -LiteralPath $before -PathType Container)) {
        Expand-Archive -LiteralPath $archive -DestinationPath (Split-Path $before)
    }
    if (!(Test-Path -LiteralPath $oneRow -PathType Container)) {
        Expand-Archive -LiteralPath $archive -DestinationPath (Split-Path $oneRow)
    }
}
$sourceFile = 'pixivvault_nicegui\ui\card_grid.js'
$baseTemplate = [IO.File]::ReadAllText((Join-Path $before $sourceFile))
$three = ':eager="index < columns * 3" :first="index === 0"'
$one = ':eager="index < columns" :first="index === 0"'
if (!$baseTemplate.Contains($three)) { throw 'Unexpected baseline CardGrid template.' }
[IO.File]::WriteAllText((Join-Path $oneRow $sourceFile),$baseTemplate.Replace($three,$one),
                        [Text.UTF8Encoding]::new($false))
$cases = @('before-1', 'one-row-1', 'after-1', 'after-2', 'one-row-2', 'before-2')
foreach ($batch in $cases) {
    $source = $after
    if ($batch -like 'before-*') { $source = $before }
    if ($batch -like 'one-row-*') { $source = $oneRow }
    $env:PYTHONPATH = "$source;$repo"
    $output = Join-Path $PSScriptRoot $batch
    $sandbox = Join-Path $tempRoot "r3-cardgrid-comparison-$batch-20261121"
    & $python -B $measure $sandbox --width 1280 --repetitions 3 --all-screenshots --trace-images
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    New-Item -ItemType Directory -Path $output | Out-Null
    foreach ($name in @('raw.json', 'image-trace.json', 'owned-server.json')) {
        Copy-Item -LiteralPath (Join-Path $sandbox $name) -Destination $output
    }
}
& $python -B (Join-Path $PSScriptRoot 'aggregate.py')
exit $LASTEXITCODE
