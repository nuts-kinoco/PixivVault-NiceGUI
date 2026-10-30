param(
    [string]$Python = ".venv\Scripts\python.exe",
    [string]$OutputDirectory = "",
    [ValidateSet('none','cpu','cuda')][string]$InferenceBackend = 'none'
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pythonPath = if ([IO.Path]::IsPathRooted($Python)) { $Python } else { Join-Path $repo $Python }
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) { throw "Python がありません: $pythonPath" }
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { throw "Windows 専用ビルドです" }

Push-Location $repo
try {
    # 必須ランタイムがない場合は不完全な実行形式を作らない。
    & $pythonPath -c "import importlib.metadata as m; import clr, webview, nicegui, PyInstaller, cryptography, pystray, win11toast, cv2, numpy; [m.version(n) for n in ('nicegui','pywebview','pythonnet','clr_loader','pixivvault-nicegui','cryptography','pystray','win11toast','opencv-python','numpy')]"
    if ($LASTEXITCODE -ne 0) { throw "ビルド依存関係が不足しています" }
    # Optional backend must be chosen explicitly. A copied GPU runtime must
    # retain the exact NVIDIA CUDA/cuDNN layout used by ORT preload_dlls().
    $ortArgs = @()
    if ($InferenceBackend -ne 'none') {
        $expected = if ($InferenceBackend -eq 'cuda') { 'onnxruntime-gpu' } else { 'onnxruntime' }
        & $pythonPath -B -c "import importlib.metadata as m,onnxruntime as o; assert m.version('$expected')=='1.28.0'; assert '$($InferenceBackend)' != 'cuda' or 'CUDAExecutionProvider' in o.get_available_providers()" 
        if ($LASTEXITCODE -ne 0) { throw "推論ランタイムが要求と一致しません" }
        $ortArgs = @('--collect-all', 'onnxruntime', '--copy-metadata', $expected)
        if ($InferenceBackend -eq 'cuda') {
            & $pythonPath -B -c "import onnxruntime as o;o.preload_dlls(directory='')"
            if ($LASTEXITCODE -ne 0) { throw "CUDA DLLの読み込みに失敗しました" }
            $pureLib = (& $pythonPath -B -c "import sysconfig;print(sysconfig.get_paths()['purelib'])").Trim()
            if ($LASTEXITCODE -ne 0) { throw "Python site-packagesを確認できません" }
            $cuda = Join-Path $pureLib 'nvidia\cu13\bin\x86_64'
            $cudnn = Join-Path $pureLib 'nvidia\cudnn\bin'
            foreach ($tuple in @(@($cuda,'nvidia/cu13/bin/x86_64'),@($cudnn,'nvidia/cudnn/bin'))) {
                $path = $tuple[0]
                if (-not (Test-Path -LiteralPath $path -PathType Container)) {
                    throw "CUDA依存フォルダが不足しています: $path"
                }
                $dlls = @(Get-ChildItem -LiteralPath $path -File -Filter '*.dll')
                if ($dlls.Count -lt 4) { throw "CUDA依存DLLが不足しています: $path" }
                foreach ($dll in $dlls) {
                    $ortArgs += @('--add-binary', ($dll.FullName + ';' + $tuple[1]))
                }
            }
        }
    }

    $base = if ($OutputDirectory) {
        if ([IO.Path]::IsPathRooted($OutputDirectory)) { [IO.Path]::GetFullPath($OutputDirectory) }
        else { [IO.Path]::GetFullPath((Join-Path $repo $OutputDirectory)) }
    } else {
        Join-Path $repo ("artifacts\windows-packaging-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
    }
    if (Test-Path -LiteralPath $base) { throw "既存のビルド証跡を上書きしません: $base" }
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
        '--collect-all', 'cryptography',
        '--collect-all', 'cv2',
        '--collect-all', 'numpy',
        '--collect-all', 'win11toast',
        '--hidden-import', 'pystray._win32',
        '--collect-data', 'pixivvault_nicegui',
        '--add-data', ((Join-Path $repo 'src\pixivvault_nicegui\ui\verified_image.js') + ';pixivvault_nicegui/ui'),
        '--add-data', ((Join-Path $repo 'src\pixivvault_nicegui\ugoira_mp4_worker.py') + ';pixivvault_nicegui'),
        '--collect-submodules', 'webview.platforms',
        '--collect-submodules', 'socketio',
        '--collect-submodules', 'engineio',
        '--copy-metadata', 'pixivvault-nicegui',
        '--copy-metadata', 'nicegui',
        '--copy-metadata', 'pywebview',
        '--copy-metadata', 'pythonnet',
        '--copy-metadata', 'clr_loader',
        '--copy-metadata', 'cryptography',
        '--copy-metadata', 'pystray',
        '--copy-metadata', 'win11toast',
        '--copy-metadata', 'opencv-python',
        '--copy-metadata', 'numpy'
    )
    $packageArgs += $ortArgs
    $packageArgs += (Join-Path $repo 'tools\native_entry.py')
    $savedPreference = $ErrorActionPreference
    $savedPythonPath = $env:PYTHONPATH
    try {
        # PowerShell 5.1 treats native stderr warnings as errors under Stop.
        # Preserve the full log and judge the actual process exit code.
        $ErrorActionPreference = "Continue"
        $env:PYTHONPATH = Join-Path $repo 'src'
        & $pythonPath @packageArgs *> (Join-Path $base 'build.log')
        $buildExit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $savedPreference
        $env:PYTHONPATH = $savedPythonPath
    }
    if ($buildExit -ne 0) { throw "PyInstaller は exit $buildExit で失敗しました。build.logを確認してください。" }
    $exe = Join-Path $dist 'PixivVault-NiceGUI\PixivVault-NiceGUI.exe'
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw "exe が見つかりません: $exe" }
    Write-Host "ビルド完了: $exe"
    Write-Host '次に --check を実行し、native GUI と終了後のプロセスを確認してください。'
}
finally { Pop-Location }
