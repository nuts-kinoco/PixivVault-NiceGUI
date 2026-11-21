# D6 トラック B 実 API 測定手順

第3回は隔離検証のみ。実 API の別依頼を受けたら、測定候補 SHA を固定し、本人または実機担当から通常製品の直前の非停止確認時刻と共有 GET の最新通算数を受け取る。同じ共有枠を使う担当と通算を調整する。既存手順の隔離複製を使い、実機ポート22386・本番プロフィールを測定担当は操作しない。 [実測計画](../../../docs/P6_REAL_USE_PLAN_20261121.md) [試験所有者](../../../docs/TEST_OWNER_LAUNCH.md)

両版合計で30 GET以内。認証、ウォームアップ、画像、再試行も数える。共有枠は180 GET。専用 Python を使い、TEMP/TMP/TMPDIR は S: の専用領域に設定する。起動先は `S:/Work/_tmp/PV/d6real` 配下の新規絶対パス。

```powershell
$runRoot = 'S:/Work/_tmp/PV/d6real/d6api-r3-live01'
$prior = [int](Read-Host '共有 GET の最新通算数')
$ownerOpenAt = Read-Host '通常製品非停止の確認時刻 ISO 8601'
& 'S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe' -B verify/d6api-20261121/ranking_server.py $runRoot --prior-attempts $prior --owner-open-at $ownerOpenAt
```

既存ハーネスでランキング10対を取り、各対の Flet/NiceGUI の先行順を交互にする。送信は前回応答終了から875 ms以上空ける。401・403・429、CAPTCHA、認証不成立、通信異常、共有ゲートの停止・成否不明が出た回は中断し、台帳を保全する。ハーネスが通信異常を記録した後は次の送信を遮断するため、製品の Keep-Alive 競合回復は隔離 TLS の証拠で確認する。 [30 GET の計測入口](../ranking_server.py) [既存ハーネス](../../d6real-20261121/README.md) [台帳の送信境界](../../d6real-20261121/live_gate.py)

API 通信の中央値・p95・ペア内差・標本数を記録する。ペーシングと画面区間は別に記録する。台帳の新規・通算 GET 数、最短間隔、停止理由、起動したプロセスの終了を照合する。証拠は `ranking-summary.json`、`ranking-outcome.json`、`dispatch.json`、プロセス確認を用い、資格情報やプロフィール DB は複製環境に留める。
