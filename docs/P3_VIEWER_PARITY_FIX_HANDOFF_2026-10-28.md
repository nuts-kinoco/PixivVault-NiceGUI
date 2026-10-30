# PV P3 候補283の10失敗に対する修正引継ぎ

## 状態・対象

コードと回帰コードの納品。pytest、製品import、構文検査、build、GUI、製品起動、実APIを実装担当は実行していない。自己点検は原票・コード・差分の読取りだけで、独立監査や機能受入ではない。台帳の分子・分母を更新しない。

- 固定283: `S:/Projects/PixivVault-NiceGUI-p3-viewer-save-parity-20261028`。HEAD `2cf4a7a7286dd728e8df8125094498a3622babba`、branch `phase11/p3-viewer-save-parity-20261028`、dirty283、集合SHA `1731f663dce0b37be37cb90a8d70f5bdff8567fa709512c891d99fb023dfa09e`。
- 新候補: `S:/Projects/PixivVault-NiceGUI-p3-viewer-parity-fix-20261028`。同じHEAD、branch `phase11/p3-viewer-parity-fix-20261028`。固定283の終了記録と全283ファイルのSHA一致を確認し、製品基線とmanifest記載差分のみを別checkoutへ複製。CoSのartifacts・合成DB・実データは複製していない。
- 今回の完全dirty集合、src/tools全体、283との差分、固定候補保全、最終終了記録は `artifacts/p3-viewer-parity-fix-20261028/` の `manifest.json`、`source-manifest.json`、`changes-vs-283.json` / `.diff`、`preservation.json`、`WRITE_END.json` を正とする。hashの循環を避け、ここには最終集合値を埋め込まない。

## 原票と10件の対応

固定283の `artifacts/p3-viewer-save-parity-20261028/cos-owned-r1/impact.xml` SHA `48f8efa6112de311bafe4315a3d8c40b7c8f17a25a9f059a48aab9b2f7d2ee49`、`pytest-console.log` SHA `897d81d5d1cd9c4fed3e9083cf04b3829141ad0737e17014ddc00f956625813a`（UTF-16LE）を実読。結果は17モジュール218ケース、208 PASS / 10 FAIL、31.36秒、exit1。新候補にPASSを継承しない。

| 原失敗 | 分離した原因・変更 | 維持する受入 |
|---|---|---|
| 原配置・再起動2件、完全receiptからの単ページ再利用1件 | 試験がApplicationServices配下のmanaged owner.runを直接呼び、正規runnerが設定する受付済recordを迂回していた。resolve_record=Noneに対する製品拒否は正しい。fixtureをsave_explicit_work→永続受付→実JobService runner→完了snapshotの経路へ変更。 | 3ページ全体receipt、部分保存が全体保存済みにならないこと、配置ON/OFF、元bytes、同ページ再取得なし、catalog不変。製品のrecord拒否は不変更。 |
| 作者履歴の保存先を開くhandler1件 | SaveExports.rootが拡張Windows表記である一方、saved_folderは論理表記。DesktopIntegrationの許可root比較が両表記を混在させていた。比較はresolve後のlogical_pathへ統一し、存在・種類・リンク/reparse検査はnative_io_pathで行う。 | 実UI handler、作者単位集約、同じ所有root内のみ、close後操作拒否。範囲外・不正target拒否を緩和せず、launcherは試験用記録のみ。 |
| 285文字超DB再起動native/resolveの2件 | 283は不正URI authorityを避けるため拡張prefixを削除していた。修正はnative filename全体をURIのfilename部分にpercent-encodeし、prefixを保持。JobService._verifyの接続URIだけを置換。 | mode=ro / immutable=1、application ID、v1/v2厳密table集合、operations/history、lock、同operationの再受付・再dispatchなし。短root/OS設定変更なし。 |
| duplicate_key 1件 | receiptはv2だが試験が文字列version:1を置換し、注入が発火していなかった。現versionのtokenを指定し、置換前1個→置換後2個とbytes変化をassert。 | 元のraises、未知データ・bytes保全、再取得なしを保持。元の全8損傷条件をv1/v2双方で検査する。 |
| 共通カードサイズ3入口の3件 | DemoShell.__new__ fixtureに正規constructorのbackup_view属性が欠けた。実callbackの前提を補い、履歴viewへの伝播もassert。 | 元の3入口の共通永続値と3画面更新assertを保持。shell製品は不変更。 |

7件は試験前提、3件は製品パス経路の修正対象。上表は実装上の対応で、修正後PASSの報告ではない。

### 長パスについての確定範囲と未確定

原票では初回job完了後、再起動の_Store._verifyがsqlite3.Errorを公開JobStorageErrorへ変換したことを確認できる。内側のsqlite error codeは原票には残っていない。拡張prefixを落とすコードは静的に確定したが、修正したURIが現在のWindows SQLite VFSで正常に開くことはCoSの実試験待ち。最終原因の完全実証や解消済みとはしない。

新回帰はURI文字列を実装式で再計算して一致させるだけにせず、authorityなし・ro/immutableのみ・decode後filename一致を検査し、そのURIで実DBをread-only接続する。読み取ったapplication ID/versionを確認し、CREATE TABLE拒否・元DB SHA不変・未存在DB非作成をassert。空白、#、%、日本語の合成filenameも含む。失敗時はpytest user_propertyへ例外型・sqlite_errorname/codeだけを記録し、raw利用者pathや秘密値を追加出力しない。接続は明示closeする。

## 最小変更と非影響

製品変更は `fs_paths.sqlite_readonly_uri`、`job_service._Store._verify`、`desktop_integration.DesktopIntegration` の3ファイル。既存helperのnative_io_path/logical_path本体は不変更。

試験変更は `test_viewer_save_parity.py`、`test_viewer_restart_boundaries.py`、`test_viewer_page_save.py`、`test_p3_card_size_shared.py`、新 `test_viewer_path_compat.py`。新path回帰はroot/targetの論理・拡張表記4組、285文字超、範囲外/missing/非対応拡張子、root/parent/target reparse属性の実注入発火を検査する。実OSリンク作成や権限変更は要求しない。

283のservices、viewer_exports、viewer_gif、managed_viewer_page、全UI、容量cache/lease、L12/D15/X04/S21/L13製品とGUI checkerはbyte同一。src/tools全体対応はsource-manifestで記録する。ただしJobServiceとDesktopIntegrationは共通部品なので、旧GUI成功だけで新候補の全体合格とはしない。固定283/271/267/265/259の個別・集合SHA再照合はpreservation.json。元データ、CoSの進行中process/artifactへ書込み・停止操作なし。

## CoSの一括CPU確認（実装担当は未実行）

既存S側venv、候補cwd、既存pytest設定を使用する。新しいcos-owned-r1のみへ出力し、既存原票/tmpを再利用しない。親が示した追加267/L12監査資料の拒否操作・原文回収はこの手順に含めない。

```powershell
Set-Location S:/Projects/PixivVault-NiceGUI-p3-viewer-parity-fix-20261028
$env:PYTHONUTF8='1'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONPATH='S:/Projects/PixivVault-NiceGUI-p3-viewer-parity-fix-20261028/src'
& S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_viewer_save_parity.py tests/test_viewer_restart_boundaries.py tests/test_viewer_page_save.py tests/test_viewer_save.py tests/test_verified_image.py tests/test_live_bookmark_integration.py tests/test_library_media_registry.py tests/test_ugoira_gif.py tests/test_job_service.py tests/test_native_job_history_compat.py tests/test_native_operation_bindings.py tests/test_capacity_reactivation.py tests/test_p3_viewer_author.py tests/test_artwork_viewer.py tests/test_gallery_pages.py tests/test_library_view.py tests/test_p3_card_size_shared.py tests/test_viewer_path_compat.py tests/test_desktop_integration.py --basetemp=artifacts/p3-viewer-parity-fix-20261028/cos-owned-r1/tmp --junitxml=artifacts/p3-viewer-parity-fix-20261028/cos-owned-r1/impact.xml
```

元17モジュール＋変更したDesktopIntegrationの既存/新規回帰。元10失敗が後続assertまで到達し、保存・再利用・拒否・restart・handlerを満たすことが期待。新v1/v2損傷とURI/desktop回帰により件数は218から増えるが、collect未実行なので総件数を推測してPASS数へしない。DBのnative/resolved両ケースは285文字超を維持する。失敗時は元assertと原票を保全し、timeout延長・callback no-op・short root・schema検査解除で通さない。

## CoS GUIの引継ぎ

283で進行中の検証の結果は別記録とする。checkerコピーによる参照root不一致で製品操作前に止まった4フェーズを、製品FAILへ混ぜない。**新候補のtools原本をその位置から実行する**。両scriptが__file__から候補rootを決めるため、cos-ownedへコピーして実行しない。両scriptの許可artifact名は引き継いだ `artifacts/p3-viewer-save-parity-20261028` のまま（必ず新checkout内）。所有境界を変えるための製品/tool修正はしていない。

以下はCoSのみ。空きport/既存PIDを先に確認し、8089や他担当processへ触らない。専用port例52387、root/outputは次の新候補内だけ。

```powershell
& S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -B tools/serve_viewer_save_parity_fixture.py --port 52387 --root S:/Projects/PixivVault-NiceGUI-p3-viewer-parity-fix-20261028/artifacts/p3-viewer-save-parity-20261028/cos-owned-r1/gui-1280/data
# 所有serverの起動完了確認後、別のCoS所有shellから
& S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -B tools/check_viewer_save_parity_browser.py --url http://127.0.0.1:52387 --output S:/Projects/PixivVault-NiceGUI-p3-viewer-parity-fix-20261028/artifacts/p3-viewer-save-parity-20261028/cos-owned-r1/gui-1280/initial --width 1280 --height 720
```

初回2/3ページだけの保存→再押下で原画像再取得なし→設定から作品folder ON→3/3保存→表示GIFの同bytes保存→作者履歴と「保存先を開く」実handler（fake launcher）まで通常操作する。原GIF delay/色/尺と保存bytesはCPUとGUIの役割を区別し、品質確認範囲を記録する。

所有server通常終了後、PID/port解放を確認し、**同じdata root**を再起動する。checkerへ `--restart`、output `.../gui-1280/restart`。履歴の復元、原画像再取得0、manifest/bytes不変を確認する。1920×1080/lightは新 `gui-1920/data` で初回・再起動（checker `--light`）を同条件で実施。以上2サイズ×初回/再起動4ケース。固定待ち・強制click・API保存代行を追加しない。console/page error/外部request、画面、失敗DOM、所有PID/port、前後manifestの確認を残す。

## 終了条件と残件

元10失敗と関連回帰・GUIは未実行。実Explorer/Honeyview、配布native、live、CI、独立監査、全P3受入は別未達。283が明示した全作者exportと個別原filenameの完全な同名再利用、全媒体/過去全件の直近履歴、旧保存先・ZIP追記/改名/物理隔離の未採用契約はこの不具合バッチで採用していない。L12追加監査資料の局所保留は引き続き保留。

書込み終了時のmanifest/差分/固定候補保全/所有process照合をWRITE_END.jsonに記録する。独立レビュー用の正確なソース範囲はreview-ranges.json。実装者の静的照合を独立レビューに数えない。
