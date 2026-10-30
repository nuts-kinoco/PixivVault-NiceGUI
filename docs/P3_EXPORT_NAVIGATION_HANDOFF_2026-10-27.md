# P3 export GUI checker: existing navigation contract

専用候補 S:/Projects/PixivVault-NiceGUI-p3-export-navigation-20261027、branch phase11/p3-export-navigation-20261027、HEAD 2cf4a7a7286dd728e8df8125094498a3622babba。基線p3-export-ui-fix / dirty184 / SHA38b0456b92020bfd89efa6c444bfd38f06e814a7f84cd3224ebc4e9f8f0ff29dを全hash照合し固定。CoS原票 result.json SHA FD26001E023924043573C3674C914C2BDE9A8EDFAA69D17299A737D5D24A3DDCを現物照合。旧候補のCoS証拠/一時rootはコピーしていない。新候補不存在/writerなしを確認して分離。

原因はcheckerの誤前提2点。job_views.PAGE_SIZE=8、現在ページだけDOM化する正規仕様に対し10jobを1ページで探していた。通常queueにはpause/resume/stop/delete、failed_onlyにはretry/deleteという既存仕様に対しqueueでRetryを探していた。今回のRetry timeoutをpointer interceptionと扱わない。製品変更なし。

変更はtools/check_p3_save_exports_browser.py、pagination回帰tests/test_export_checker_navigation.py、本書/PROGRESS。visible_jobはqueue-view/failed-view内へlocatorを限定、既存previousで先頭へ戻り、既存nextとpage表示の応答を待って有限ページ数内の同一IDを探す。成功行はdone/完了、失敗行はerror/エラーを正確に確認。キュー両表示の後、通常tab-failedへ移動し同一IDを探して通常Retry。snapshotの同一ID/attempt2/errorとfailed DOM/error表示を維持。全件DOM化/フィルター解除/強制click/JS dispatch/API retry/固定sleep/待機延長なし。

後半の静的照合: projection manifestの完了jobから許可ファイルだけを列挙しnonce付きGET、TXT内の相対画像を同じprojection directoryで解決、manifest/source hash一致とブラウザnative TXT/画像decodeを確認する既存経路を保持。CAS library閲覧とは別証拠。inventory manifest読取りだけ既存native_io_pathを適用し、深いrootでの表記漏れをなくした。extra HTTP headersはcontextに保持され、reader page.goto後のsnapshot/restartでも同じ認証付き読取り。再起動2ケースは新process/同データでpreferencesと全job ID/state/attempt一致・reader callsゼロ、projection reader再利用を保持。ブラウザを実行していないためこの静的整合はGUI成功を証明しない。

CoS: final/manifest.jsonと旧184対応を照合。既存S venv、PYTHONPATH=<candidate>/src、PYTHONUTF8=1、PYTHONDONTWRITEBYTECODE=1、新S TEMP/TMP/basetemp/log。

```powershell
& S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -B -m pytest tests/test_export_checker_navigation.py tests/test_save_exports_gui_fixture.py --basetemp <fresh-S-temp> --junitxml <fresh-S-log>/cpu.xml
& S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -B tools/check_p3_save_exports_browser.py --channel msedge --output <candidate>/artifacts/<fresh-owned-run>
```

CPU後、元条件の1280/1920初回+再起動4ケースを実施。設定BOTH/direct/use_work_folder0/autoZIP、公開501/非公開502、静止画701/702、source/projection保存成功、通常キューページ移動で対象ZIP成功/エラーを可視化、失敗リスト同一ID Retry/attempt2/error、projection TXT/相対画像hash/native reader、再起動設定/jobs一致まで全部確認。スクリーンショット/DOM/結果/正常所有終了/port解放/外部request0/前後hash不変を保持。途中失敗・未到達を合格にしない。8089/他processに非干渉、成否不明を再送しない。

試験/構文検査/GUIはCodex未実行。旧CPU27PASSは基線の結果であり本候補へ加算しない。製品全ソース不変、回帰fakeはページ移動/欠落検出の検査で実DOM受入の代替ではない。Retry/projection reader/再起動、独立監査/native/live/exact-head CI/総合UX/P3全完/P4判断は未達。最終SHA/差分/旧184保全/書込み終了は専用artifacts finalを正とする。
