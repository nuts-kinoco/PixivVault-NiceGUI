# 実機アプリ（ポート 22386）が応答していなければ launch_work.ps1 で起動し、応答するまで待つ。
# COS などの別セッションに起動させない（別の実行権限・環境だと共有 STOP 保護を開けず、起動設定の確認画面に落ちた: 2026-11-17 23:29）。
param([int]$Port = 22386, [int]$TimeoutSec = 120)
$ErrorActionPreference = 'Stop'
function Test-App { try { (Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/" -TimeoutSec 3).StatusCode -eq 200 } catch { $false } }
if (Test-App) { Write-Output "already running on $Port"; exit 0 }
$tag = 'ensure-' + (Get-Date -Format 'HHmmss')
& 'S:\Projects\PV-DEV-20261117\launch_work.ps1' -Tag $tag | Write-Output
$deadline = (Get-Date).AddSeconds($TimeoutSec)
while ((Get-Date) -lt $deadline) { if (Test-App) { Write-Output "up on $Port"; exit 0 }; Start-Sleep -Seconds 2 }
Write-Error "not up within $TimeoutSec s"; exit 1
