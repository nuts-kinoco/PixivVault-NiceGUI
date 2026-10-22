param(
    [string]$Python = ".venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pythonPath = if ([IO.Path]::IsPathRooted($Python)) { $Python } else { Join-Path $repo $Python }
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) { throw "Python がありません: $pythonPath" }
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { throw "Windows 専用ビルドです" }

Push-Location $repo
try {
    # 必須ランタイムがない場合は不完全な実行形式を作らない。
    & $pythonPath -c "import importlib.metadata as m; import clr, webview, nicegui, PyInstaller; [m.version(n) for n in ('nicegui','pywebview','pythonnet','clr_loader','pixivvault-nicegui')]"
    if ($LASTEXITCODE -ne 0) { throw "ビルド依存関係が不足しています" }

    $base = Join-Path $repo "artifacts\windows-packaging"
    $dist = Join-Path $base "dist"
    $work = Join-Path $base "build"
    $spec = Join-Path $base "spec"
    New-Item -ItemType Directory -Force -Path $dist, $work, $spec | Out-Null
    $packageArgs = @(
        '-m', 'PyInstaller',
        '--onedir', '--noconsole', '--noconfirm',
        '--name', 'PixivVault-NiceGUI',
        '--distpath', $dist, '--workpath', $work, '--specpath', $spec,
        '--paths', (Join-Path $repo 'src'),
        '--collect-all', 'nicegui',
        '--collect-all', 'webview',
        '--collect-all', 'clr_loader',
        '--collect-all', 'pythonnet',
        '--collect-data', 'pixivvault_nicegui',
        '--collect-submodules', 'webview.platforms',
        '--collect-submodules', 'socketio',
        '--collect-submodules', 'engineio',
        '--copy-metadata', 'pixivvault-nicegui',
        '--copy-metadata', 'nicegui',
        '--copy-metadata', 'pywebview',
        '--copy-metadata', 'pythonnet',
        '--copy-metadata', 'clr_loader',
        (Join-Path $repo 'tools\native_entry.py')
    )
    & $pythonPath @packageArgs
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller は exit $LASTEXITCODE で失敗しました" }
    $exe = Join-Path $dist 'PixivVault-NiceGUI\PixivVault-NiceGUI.exe'
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw "exe が見つかりません: $exe" }
    Write-Host "ビルド完了: $exe"
    Write-Host '次に --check を実行し、native GUI と終了後のプロセスを確認してください。'
}
finally { Pop-Location }
