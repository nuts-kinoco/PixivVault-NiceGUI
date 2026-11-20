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
if (-not $LogRoot) { $LogRoot = Join-Path $CodeRoot 'logs' }
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

$commandLine = '-B -u -m pixivvault_nicegui'
if ($IsolatedWorkspace) {
    $IsolatedWorkspace = Get-SPath $IsolatedWorkspace
    $prefix = $IsolatedWorkspace.TrimEnd('\') + '\'
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
    $commandLine = '-B -u "{0}" --test-workspace "{1}" --' -f $testEntry, $IsolatedWorkspace.TrimEnd('\')
} else {
    $local = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
    if ($local -notmatch '^[sS]:[\\/]') {
        throw 'Windows LocalAppData is outside S:. The shared acquisition stop state requires an approved S: deployment before normal launch.'
    }
    $null = Get-SPath (Join-Path $local 'PixivVault-NiceGUI\managed-pixiv-get-stop-v1')
}
$commandLine += ' --native --host 127.0.0.1 --port {0} --profile-root "{1}"' -f $Port, $ProfileRoot.TrimEnd('\')

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
    PIXIVVAULT_CRASH_LOG_PATH = "$logPrefix.crash.log"
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
from pathlib import Path
from pixivvault_nicegui.native_profile import NativeProfileStore

try:
    profile = Path(sys.argv[1])
    if profile.parent.resolve().drive.upper() != 'S:':
        raise ValueError
    snapshot = NativeProfileStore.peek(profile)
    if sys.argv[2] == 'isolated' and snapshot.cookie_file is not None:
        raise ValueError
    for path in (snapshot.cookie_file, snapshot.save_parent, snapshot.library_readonly_db,
                 snapshot.library_image_root, snapshot.similarity_model_file):
        if path is not None and path.resolve().drive.upper() != 'S:':
            raise ValueError
except Exception:
    sys.exit(2)
'@
    $mode = if ($IsolatedWorkspace) { 'isolated' } else { 'product' }
    $preflight | & $python -B - $ProfileRoot $mode
    if ($LASTEXITCODE -ne 0) {
        throw 'Profile preflight failed. Check that configured paths are on S: and the isolated profile has no credentials.'
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
