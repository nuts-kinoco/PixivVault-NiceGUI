[CmdletBinding()]
param(
    [string]$CodeRoot = 'S:\Projects\PixivVault-NiceGUI-release',
    [string]$PythonRoot,
    [string]$ProfileRoot,
    [ValidateRange(1, 65535)][int]$Port = 8089,
    [string]$LogRoot,
    [string]$TempRoot,
    [string]$IsolatedWorkspace
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Get-SPath([string]$Path) {
    if ($Path -notmatch '^[sS]:[\\/]') {
        throw 'Specify an absolute path on S:.'
    }
    if ($Path -match '["\x00-\x1f]') {
        throw 'Quotes and control characters are unavailable in launcher paths.'
    }
    $full = [IO.Path]::GetFullPath($Path)
    if ($full.Length -gt 3) { $full = $full.TrimEnd('\') }
    for ($item = $full; $item; $item = [IO.Path]::GetDirectoryName($item)) {
        $existing = Get-Item -LiteralPath $item -Force -ErrorAction SilentlyContinue
        if ($existing -and ($existing.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'Reparse points are unavailable for launcher paths.'
        }
    }
    return $full
}

function Join-ProcessArguments([string[]]$Arguments) {
    # Start-Process on Windows PowerShell 5.1 takes one Windows command line.
    # Paths have already rejected quotes. Double final backslashes before the closing quote.
    return (($Arguments | ForEach-Object { '"' + ($_ -replace '(\\+)$', '$1$1') + '"' }) -join ' ')
}

$CodeRoot = Get-SPath $CodeRoot
if (-not (Test-Path -LiteralPath $CodeRoot -PathType Container)) {
    throw 'CodeRoot does not exist. Supply the prepared release directory with -CodeRoot.'
}
$source = Get-SPath (Join-Path $CodeRoot 'src')
$entry = Get-SPath (Join-Path $source 'pixivvault_nicegui\__main__.py')
if (-not (Test-Path -LiteralPath $entry -PathType Leaf)) {
    throw 'CodeRoot does not contain the PixivVault-NiceGUI application.'
}
if (-not $PythonRoot) { $PythonRoot = Join-Path $CodeRoot '.venv' }
if (-not $ProfileRoot) { $ProfileRoot = Join-Path $CodeRoot 'profile' }
$explicitLogRoot = [bool]$LogRoot
if (-not $explicitLogRoot) { $LogRoot = Join-Path $ProfileRoot 'logs' }
if (-not $TempRoot) { $TempRoot = Join-Path $CodeRoot 'temp' }
$python = Get-SPath (Join-Path (Get-SPath $PythonRoot) 'Scripts\python.exe')
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'PythonRoot does not contain Scripts\python.exe. Supply the prepared virtual environment.'
}
$ProfileRoot = Get-SPath $ProfileRoot
$profileParent = [IO.Path]::GetDirectoryName($ProfileRoot)
if (-not $profileParent) {
    throw 'ProfileRoot must be a directory below the S: drive root.'
}
$LogRoot = Get-SPath $LogRoot
$TempRoot = Get-SPath $TempRoot

$launchArgs = @('-B', '-u', '-m', 'pixivvault_nicegui')
if ($IsolatedWorkspace) {
    $IsolatedWorkspace = Get-SPath $IsolatedWorkspace
    if ($IsolatedWorkspace -eq [IO.Path]::GetPathRoot($IsolatedWorkspace)) {
        throw 'IsolatedWorkspace must be a directory below the S: drive root.'
    }
    $prefix = $IsolatedWorkspace + '\'
    foreach ($path in @($ProfileRoot, $LogRoot, $TempRoot)) {
        if (-not $path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Isolated ProfileRoot, LogRoot and TempRoot must be inside IsolatedWorkspace.'
        }
    }
    $marker = Get-SPath (Join-Path $IsolatedWorkspace '.pixivvault-isolated-test-workspace')
    $testEntry = Get-SPath (Join-Path $CodeRoot 'tools\launch_test_owner.py')
    if (-not (Test-Path -LiteralPath $marker -PathType Leaf) -or
        -not (Test-Path -LiteralPath $testEntry -PathType Leaf)) {
        throw 'IsolatedWorkspace requires the existing source-test marker and entry point.'
    }
    $launchArgs = @('-B', '-u', $testEntry, '--test-workspace', $IsolatedWorkspace, '--')
} else {
    $local = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
    if ($local -notmatch '^[sS]:[\\/]') {
        throw 'Windows LocalAppData is outside S:. The shared acquisition stop state requires an approved S: deployment before normal launch.'
    }
    $null = Get-SPath (Join-Path $local 'PixivVault-NiceGUI\managed-pixiv-get-stop-v1')
}
$launchArgs += @('--native', '--host', '127.0.0.1', '--port', [string]$Port, '--profile-root', $ProfileRoot)
$commandLine = Join-ProcessArguments $launchArgs

$listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $Port)
try {
    $listener.ExclusiveAddressUse = $true
    $listener.Start()
} catch {
    throw 'The loopback port is unavailable. Choose another -Port.'
} finally {
    $listener.Stop()
}

$logPrefix = Join-Path $LogRoot ('pixivvault-' + [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N'))
$envChanges = @{
    TEMP = $TempRoot
    TMP = $TempRoot
    TMPDIR = $TempRoot
    PYTHONPATH = $source
    PYTHONUTF8 = '1'
    NICEGUI_STORAGE_PATH = (Get-SPath (Join-Path $TempRoot 'nicegui'))
    WEBVIEW2_USER_DATA_FOLDER = (Get-SPath (Join-Path $TempRoot 'webview2'))
}
if ($explicitLogRoot -and -not $env:PIXIVVAULT_CRASH_LOG_PATH) {
    $envChanges.PIXIVVAULT_CRASH_LOG_PATH = "$logPrefix.crash.log"
}
if ($IsolatedWorkspace) {
    $envChanges['APPDATA'] = Get-SPath (Join-Path $TempRoot 'roaming')
    $envChanges['LOCALAPPDATA'] = Get-SPath (Join-Path $TempRoot 'local')
}
$saved = @{}
try {
    foreach ($name in $envChanges.Keys) {
        $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        [Environment]::SetEnvironmentVariable($name, $envChanges[$name], 'Process')
    }
    [IO.Directory]::CreateDirectory($TempRoot) | Out-Null
    if ($IsolatedWorkspace) {
        [IO.Directory]::CreateDirectory($env:APPDATA) | Out-Null
        [IO.Directory]::CreateDirectory($env:LOCALAPPDATA) | Out-Null
    }
    $preflight = @'
import sys
from contextlib import nullcontext
from pathlib import Path
from pixivvault_nicegui.native_profile import NativeProfileStore
from pixivvault_nicegui.test_owner_runtime import isolated_test_workspace

try:
    profile, logs = map(Path, sys.argv[1:3])
    workspace = Path(sys.argv[3]) if len(sys.argv) > 3 else None
    if profile.parent.resolve().drive.upper() != 'S:':
        raise ValueError
    with (isolated_test_workspace(workspace) if workspace else nullcontext()):
        snapshot = NativeProfileStore.peek(profile)
        if workspace and snapshot.cookie_file is not None:
            raise ValueError
        for path in (snapshot.cookie_file, snapshot.save_parent, snapshot.library_readonly_db,
                     snapshot.library_image_root, snapshot.similarity_model_file):
            if path is not None and path.resolve().drive.upper() != 'S:':
                raise ValueError
        if logs.is_relative_to(profile) and not profile.exists():
            # Create an owned profile before log redirection creates child directories.
            profile.parent.mkdir(parents=True, exist_ok=True)
            NativeProfileStore(profile).close()
except Exception:
    sys.exit(2)
'@
    $preflightArgs = @($ProfileRoot, $LogRoot)
    if ($IsolatedWorkspace) { $preflightArgs += $IsolatedWorkspace }
    $preflight | & $python -B - @preflightArgs
    if ($LASTEXITCODE -ne 0) {
        throw 'Profile preflight failed. Check that configured paths are on S: and isolated paths stay inside IsolatedWorkspace without credentials.'
    }

    [IO.Directory]::CreateDirectory($profileParent) | Out-Null
    [IO.Directory]::CreateDirectory($LogRoot) | Out-Null
    $process = Start-Process -FilePath $python -ArgumentList $commandLine -WorkingDirectory $CodeRoot `
        -WindowStyle Hidden -PassThru -RedirectStandardOutput "$logPrefix.stdout.log" -RedirectStandardError "$logPrefix.stderr.log"
    try {
        # Windows PowerShell must retain the process handle to read ExitCode after exit.
        $null = $process.Handle
        [ordered]@{ pid = $process.Id; started_utc = $process.StartTime.ToUniversalTime().ToString('o'); port = $Port } |
            ConvertTo-Json -Compress | Set-Content -LiteralPath "$logPrefix.pid.json" -Encoding Ascii
        Write-Host "PixivVault PID $($process.Id). Close the app window and confirm Exit to stop."
        Write-Host "Logs and PID record: $logPrefix"
        $process.WaitForExit()
        if ($process.ExitCode -ne 0) {
            throw "PixivVault exited with code $($process.ExitCode). See the launch logs."
        }
    } finally {
        $process.Dispose()
    }
} finally {
    foreach ($name in $saved.Keys) {
        [Environment]::SetEnvironmentVariable($name, $saved[$name], 'Process')
    }
}
