# P3-383 — D08学習傾向パネル実GUI限定受入・元Flet比較のデータ境界

日時：2026-10-31 JST。ベースコミット f420769c5ea4eaae7bcf27873555d2d712309ef5。新規実測は正式原ID D08 の v8-D08-01 に対応する限定NiceGUI受入。原152操作・479不変参照の正本と既存不足一覧は再生成・改変していない。元Flet、原DB/画像/ZIP/Cookie/アカウントは操作していない。外部HTTP禁止、生成専用rootとloopbackのみ。

## 実行と新証拠

新規テストファイル：tools/check_stage5_d08_trend_browser.py。他担当コード変更なし。既存 tools/check_stage5_l13_cross_tabs_browser.py の生成fixtureサーバーのみ利用し、過去のL13サイズ操作は再試験しない。作成済みrootを上書きせず、新規 artifacts/p3-383-d08-trend-gui-r1 を使用。

~~~powershell
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
& 'S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe' -X utf8 -B tools/check_stage5_d08_trend_browser.py artifacts/p3-383-d08-trend-gui-r1
# {"success": true, "steps": 4, "failure": null, "owned_remaining": 0, "formal_pass_added": 0}
# D08_TREND_GUI_EXIT=0
~~~

実NiceGUIの学習タブをクリックし aria-current=page を確認。傾向初期状態が閉じていること、実クリックで aria-expanded=true / パネル可視 / 「傾向を閉じる」となること、次の実クリックで aria-expanded=false / パネル非表示 / 「傾向を見る」へ戻ること、さらに実クリックで再度開けることを確認。Playwright 1280x720 / DPR1、実ブラウザ画面PNG2枚を保存した。

- artifacts/p3-383-d08-trend-gui-r1/result.json SHA256 **480F078C173CA9FE966977A929565EBA0B5BFA3508F0AA67C19ACBE6C5B7E286**
- d08-trend-open.png SHA256 **E249C452B7AEB9DCE08F7B859783A354F6D3CEF78251492B9A03B8DF14056CEA**
- d08-trend-reopened.png SHA256 **4C70AC02AA55B7BE3B70C5672C9FF59EA94BA703FD3C88B76646FE01AE1BD1A8**
- external_requests=0、page_errors=0、source_changed=false、harness_changed=false。
- app.normal_exit=true、app.forced_cleanup=false、tree.remaining_count=0、app.port_released=true。関連PID再列挙でも今回関連PIDなし。

**判定：D08の実NiceGUI開閉・再開に限り担当内PASS。** 元Flet本物GUIに同一初期状態でD08操作を実施した比較、判定20件以上での実傾向内容、保存・再起動、独立レビュー、実データは未検証。formal_pass_added=0 を維持。

## 元Fletとの共通データ不足を実コードで切り分け

既存P3-368/369/370証拠を再実行せず、保護コピーと固定結果を読み取りで照合した。

- artifacts/p3-368-real-flet-source-preflight-r1/launch_true_gui.py は元 gui.py 実main_windowを呼ぶが、gui.main_window(safe_page,db=FakeDB(),scheduler=None)。FakeDB は builder_probe.py で get_library_works/get_following_users/get_failed_jobs/get_discovery_view_history を空集合に、get_work を None にし、未定義DB操作を拒否する。元ワーカーと外部DB/Cookieは遮断される。
- 同Fletは固定120合成作品の旧fixtureを使う一方、NiceGUI既存の check_stage5_l13_cross_tabs_browser.py は生成管理画像701を1件seedし、別の推薦作品201–213とランキング合成データを生成する。DB能力/入力件数・画像実体が一致していない。
- P3-370の画面寸法1768x882/DPI96/DPR1は一致するが、元FletはPrintWindow、NiceGUIはCDPの画像である。MAEは診断値のみ。同一撮影エンジン・同一データの完全視覚判定は未実施のまま保持する。
- 元Fletの本物の gui.py と全8タブ起動・クリック成功は既存P3-369の正規成果として維持する。プレースホルダーや純関数だけの比較を繰り返さず、未達の実データ接続と同一撮影へ進む。原本保護・所有PID/誕生時刻・原依存33件SHA固定の安全境界が満たされるまではFletを再起動しない。

次工程：生成fixtureの作品ID、対象タブごとのDB戻り値型、初期ユーザー設定と能力フラグ、同一ネイティブGDI撮影、クリック開始・終了条件を1対1で揃えられる専用新rootを設計する。元Flet実コードのみ使用し、保護されたコピーの本番ロジックを変えない。安全な同一データが不可能なタブだけ未検証として残し、並行して不足票の別IDを安全に実GUI受入する。

全体ゲートは引き続き **formal 0 PASS / 152 INCOMPLETE、479 refs不変、Stage5 4 FAIL / 12 UNVERIFIED、release_ready=false**。P2独立解消判定、本人受入、元Flet同条件フル比較、実Cookie・MP4環境、性能p95、全CI・Windows配布も未了。独立worker/モデル起動、push、main統合、他担当dirty変更は行わない。
