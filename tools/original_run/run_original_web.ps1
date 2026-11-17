# 元 PixivVault (Flet) を「実データ・実 Cookie に触れず」隔離起動し、Web 表示で別ポートに出す。
# 前提: S:\Projects\PV-DEV-20261117\original_copy\ に元リポのソース一式（*.py・tools\*.py・assets）のコピーと、
#       下記 4 点のパッチ済み main.py / pixiv_client.py（PATCHES.md 参照）がある。
# 使い方: powershell -File run_original_web.ps1 [-WebPort 28550]  -> 起動した PID を tmp_o\pid.txt に書く。
# 止めるとき: Stop-Process -Id (Get-Content S:\Projects\PV-DEV-20261117\tmp_o\pid.txt)   # 自分の PID だけ。
param([int]$WebPort = 28550)
$base = 'S:\Projects\PV-DEV-20261117'; $copy = "$base\original_copy"; $tmp = "$base\tmp_o"
New-Item -ItemType Directory -Force "$copy\data", $tmp | Out-Null
$env:TEMP = $tmp; $env:TMP = $tmp; $env:PYTHONIOENCODING = 'utf-8'
$env:PIXIVVAULT_DB_PATH = "$copy\data\pv_iso.db"   # 空 DB（本番判定は M:\ のパスのみ）
$env:PV_WEB_PORT = "$WebPort"; $env:PV_LOCK_PORT = "$($WebPort+1)"; $env:PV_SERVER_PORT = "$($WebPort+2)"
$env:PV_NO_TRAY = '1'; $env:PV_NO_BROWSER_COOKIES = '1'   # トレイ無し / ブラウザ Cookie 自動抽出を無効化
# cwd = original_copy なので cookies.txt・Images・cache・各ログはすべてコピー側（cookies.txt は作らない＝未認証）
$p = Start-Process 'C:\Users\nat\miniconda3\envs\stenv\python.exe' -ArgumentList '-B','main.py' -WorkingDirectory $copy `
  -RedirectStandardOutput "$tmp\out.log" -RedirectStandardError "$tmp\err.log" -PassThru -WindowStyle Hidden
$p.Id | Set-Content "$tmp\pid.txt"; "PID $($p.Id)  http://127.0.0.1:$WebPort/"
