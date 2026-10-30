# P3 ugoira Retry: diagnostics/input separation（未試験）

候補 S:/Projects/PixivVault-NiceGUI-p3-ugoira-diagnostics-20261027、branch phase11/p3-ugoira-diagnostics-20261027、HEAD 2cf4a7a7286dd728e8df8125094498a3622babba。基線198 / SHA c399d19816e5a43911917f3a378a47d0de0e3d5dafd3554f325ae56e52ab5bde を開始時全hash照合して別候補へ分離。198の永続受付/作者依存修正を全て保持。CoS所有196のprocess/artifactsへ書込みなし。旧検証データはコピーしない。

## 原票と確定原因

旧196のphase-exit-20261027原票を実読・SHA照合:
- full-p3-candidate196.xml: 091406aac69b506cec2e8470ae752ef3fdd87ab84ac5dd0d107e04358826bf73
- gif-failure-isolation.xml: dc676623dec31053c1cfbcd97ab75ae4a01a8ac468c1fd7379d489c17d60045b
- defect-codex-managed-ugoira-retry.md: 865624ebc2f8ce0fd962331161f5f5f52f45b604dcd2f19e886850d48255e874

正確なrootは専用artifact evidence-identity.json。全pytestは旧196で2769PASS/3SKIP/3FAIL、対象再現は3FAIL。失敗名は test_converter_failure_is_error_not_done_and_retry_can_complete / test_failed_gif_retry_reuses_exact_completed_frames_without_saving_again / test_post_proof_frame_failure_retry_reuses_completed_source_and_restores_exact_reference（全てtests/test_managed_ugoira.py）。attempt1の意図的OSError/ValueErrorに続き、attempt2の診断はoperation=load_ugoira_source / ManagedUgoiraError。accepted-input folderへの自前*.io-error.jsonと、_loadの2files限定が矛盾していた。環境推定ではなく原コード・原票・残存JSONで確定。

## 製品修正（managed_ugoira.pyのみ）

今後の診断は専用marked root内のdiagnostics/<job_id>/へ保存し、requests/<digest>/のsource.zip/intent.jsonを変更しない。新namespaceはroot allowlistへ追加し、既存_controlの世代/ファイル/byte quotaへ計上。別の無制限ログ管理サービスは増やさない。新規診断はstage-UUIDへexclusive write/fsyncしてから公開。中断stageは不活性なまま保存/計上し、入力や完了記録として採用しない。既存のattempt診断は上書き/削除しない。診断補助処理の失敗は元のrunner例外を置き換えない。

旧版で既に入力内に残った診断は削除/移動せず、厳密な互換例外として読む。filenameのjob ID/attempt、bounded JSON・重複key禁止、6field厳密schema、固定operation値・error_type識別子・errno/WinError型、同じinput digestを受理した既存永続operation台帳とのjob binding、live jobのattempt上限を全て照合する。source.zip/intent.jsonは引き続き必須でSHA/ZIP安全検査を保持。未知名/余分schema/未受理job/不正attempt/operation/link/reparseは拒否して保存。未知*.json全体を許す変更ではなく、診断がsource/完了の証拠になることもない。

新diagnostics namespaceでも公開JSONは同じ厳密検証を通し、未知JSON/不正実体は書込みbudget検査で拒否。所有stageはregular/nonlink等の安全性とquotaを検査し、部分JSONを完成診断とみなさない。旧診断も残存ファイル/bytesとして従来quotaへ計上される。既存上限数値は変更しない。

render_ugoira_gif / save_gif_generation / save_mp4_generationへoperationを分け、変換失敗をsave_frame_generationと誤記しない。公開ManagedUgoiraError/error_code、JobService operation_id/attempt/CAS、sealed framesの検証/再利用、MP4 codec・GIF遅延/色/画質/尺は変更しない。

同型callsite確認: record_io_failureの他の製品利用はSaveExports root。そちらは受理入力の2files限定namespaceではなく、digest intent/明示manifest/終端bookmark-markerだけを読むため同じ自己汚染条件はない。fs_paths helperは変更しない。

## MP4テストI/O修正（製品codecは無変更）

CoS追加確定のtests/test_bookmark_media_projection.py:52、同型のmanifest読取りとbody存在確認を既存native_io_pathへ統一。通常logical pathで開いていたため277文字条件の製品done/存在済みmanifestを読めなかった。全ての配置/bytes/相対参照/hash/再起動assertは維持。codec-followup-20261027/logs/mp4-only.xmlの既存原票を保持し、短いbasetempで再現条件を回避しない。短pathでの過去4PASSは今回の277文字条件の受入ではない。

## 回帰とCoS手順（Codex全て未実行）

新tests/test_ugoira_diagnostic_recovery.pyの13ケース: GIF変換/保存完了通知喪失→同job attempt2、frames再生成禁止と元bytes不変、診断が入力へ混ざらない/保持、旧診断を残したRetry/再起動、未知名/余分schema/未受理job/不正attempt/operation拒否、診断quota失敗と部分書込み実発火後のRetry/再起動、診断hardlink拒否、未知aux JSONの保全/新書込み拒否。既存3件の失敗期待を弱めていない。

最終manifest/HEAD/branch/旧198保全を照合。既存S venv、PYTHONPATH=<candidate>/src、PYTHONUTF8=1、PYTHONDONTWRITEBYTECODE=1、新しい深いS TEMP/TMP/basetemp/logを使用。まず原失敗3件と新回帰:

```powershell
& S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -B -m pytest tests/test_ugoira_diagnostic_recovery.py tests/test_managed_ugoira.py --basetemp <fresh-deep-S-temp> --junitxml <fresh-S-log>/ugoira-retry.xml
```

3元失敗を個別にPASS確認。source.zip/intent.json不変、診断保全、attempt2 done、sealed framesの同じreceipt/bytes再利用、未知データ拒否と診断失敗の非干渉を維持。次に198の耐久受付/作者依存回帰を必ず含める:

```powershell
& S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -B -m pytest tests/test_durable_export_acceptance.py tests/test_export_admission_stability.py tests/test_author_output_connection.py tests/test_bookmark_media_projection.py --basetemp <fresh-original-277char-equivalent-S-temp> --junitxml <fresh-S-log>/durable-media.xml
```

MP4は既存CPU codec interpreterをPV_TEST_CODEC_PYTHONで指定した条件で元4組（direct/link×folder0/1）を277文字相当以上のpathで実行。未設定SKIPを成功にしない。保存job done/attempt1、nativeでmanifest read、元配置/bytes/参照/再起動まで全て確認。前段後、元M5の全pytestを同じ新候補で1回実行し、独立再レビュー/必要GUIへ引継ぐ。失敗なら原票を保存し期待を緩めず切り分ける。所有process正常終了/port解放/前後hash、8089/PID37168/EMA非干渉。

旧196限定PASS/全pytest失敗結果を変更後へ合算/継承しない。今回pytest/import/構文検査/製品/GUI/native/実通信/codec実行なし。旧ZIP更新/改名/物理隔離の契約は未解除。独立再レビュー/CoS受入/全原分母/native/live/exact-head CI/総合UX/P3閉鎖/P4移行は未達。最終SHA/差分/固定198保全/書込み終了は専用final。
