# D6 トラック B の実 API 再測定

第2回の実 API 送信は0件。実測の別依頼を受け、本人または実機担当が通常製品のヘッダーで直前の非停止を確認して、タイムゾーン付き時刻と共有 GET の最新通算数を渡してから開始する。上限は両版・起動認証・ウォームアップ・画像・再試行を合わせて30 GET。d6real 全体の上限は180 GET。通常製品ポート22386・本番プロフィールからの読み取りや起動は行わず、既存手順の隔離複製を使う。 [実測計画](../../../docs/P6_REAL_USE_PLAN_20261121.md) [試験所有者](../../../docs/TEST_OWNER_LAUNCH.md)

実行前に対象候補の SHA を固定する。専用 Python を用い、TEMP、TMP、TMPDIR は S: の専用領域に設定する。同じ共有枠を使う他担当の実測と通算を調整し、30 GETを使えることを確認する。実行先は `S:/Work/_tmp/PV/d6real` 配下の新規絶対パス。以下の値は本人から受け取った実値に置き換える。

```powershell
$runRoot = 'S:/Work/_tmp/PV/d6real/d6api-r2-live01'
$prior = [int](Read-Host '共有 GET の最新通算数')
$ownerOpenAt = Read-Host '通常製品非停止の確認時刻 ISO 8601'
& 'S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe' -B verify/d6api-20261121/ranking_server.py $runRoot --prior-attempts $prior --owner-open-at $ownerOpenAt
```

既存の d6real ハーネスでランキング10対を取り、各対の Flet/NiceGUI の先行順を交互にする。実送信は前回応答終了から875 ms以上空ける。401・403・429、CAPTCHA、認証不成立、共有ゲートの停止・成否不明を検知した場合は中断して台帳を保全する。標本数を満たさない回は記録を残す。 [計測入口](../ranking_server.py) [ハーネス](../../d6real-20261121/README.md)

各版の API 区間の中央値と p95、ペア内差、比較件数を記録する。ペーシングと画面表示の区間は別に記録する。台帳から今回の GET 数、通算、最短送信間隔、停止理由、起動したサーバー・ブラウザーの終了を確認する。証拠は数値と固定分類を含む `ranking-summary.json`、`ranking-outcome.json`、`dispatch.json`、プロセス確認を用い、資格情報やプロフィール DB を証拠へ移さない。
