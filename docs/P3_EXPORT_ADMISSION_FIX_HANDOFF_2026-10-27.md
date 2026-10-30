# P3 export admission / scan isolation — independent findings remediation

専用候補 S:/Projects/PixivVault-NiceGUI-p3-export-admission-fix-20261027、branch phase11/p3-export-admission-fix-20261027、HEAD 2cf4a7a7286dd728e8df8125094498a3622babba。固定author-output193 / SHA a64183e6cd63f20da6dae38415407083104e783c8648212b35913ab6fb105064を開始前に193/193照合し別候補へ分離。193で3指摘は未解消と現物確認。Claude検証中193、固定191/189の製品/証拠/processを変更しない。manifest対象だけコピーし、検証temp/logは含めない。

## 指摘1: 最大候補選択・警告・項目隔離

原core.py2403–2447を実読。最大選択は再帰容量ではなく_safe_file_sizeのstatサイズ。bookmark_projection._largest_storageは安全検証済み候補から同じstatサイズで選び、同サイズ時はbasenameをtie-breakにして列挙順に依存させない。複数folder/ZIP候補ではWARNINGで個数と選択basenameを通知。author_folder/legacy_membershipの複数folder即拒否を撤去したが、link/reparse/unsafe path拒否は保持。旧ZIPは最大を認識/警告しても未採用の更新契約を解除せず拒否する。

SaveExportsは作者admission行、静止画行、remote bookmark行、保存済みmedia markerの単位でExceptionを隔離し、次の正常項目/媒体へ進む。scan_errorsには項目識別子と例外型、last_errorにはpost_save_item_failedを残し、logger WARNINGも出す。raw pathや元例外文字列はログへ足さない。失敗を成功と扱わず、loopの成功時空文字代入で項目エラーを消さない。root/DB等の全体障害と行の障害は分ける。

## 指摘2/3: 初回受付固定と旧intentのupgrade再受付

enqueueの最初から既存RLockで保護し、物理folder判定前に既存JobServiceの確定jobを調べる。snapshotは実コード上newest-firstなのでreverseし、保存ordinal順の最初の論理要求を再利用。logical比較はauthor_folderだけを除外し、mode/format/世代proof/account/reference_layout/作品folder設定等は残す。旧静止画intentに作品folder fieldがない場合は旧runの意味（link=True、direct=False）を正規化。正規の条件変更や新世代は異なる要求のまま。

同一論理要求は古いtarget_id/job ID/manifest/operation IDs/attemptを返し、enqueueを再呼出ししてaliasを増やさない。author_folderなし旧intentも同じ比較により元の配置を保つ。受付済みjobのJSONはSHA一致と既存bounded/safe読取りで確認し、所有が証明できないファイルを採用/書換えしない。壊れた別job intentは型付き警告で隔離し、正常要求を巻き込まない。過去ビルドが既に作ったduplicate job/manifestを削除する操作は追加せず、最初の確定jobを採用して追加増殖を止める。未確定intentだけを受理済みjobと推定しない。

## 回帰コード

新tests/test_export_admission_stability.pyの7件: repeated scan→master出現→再scan→再起動で初回pin/job/operation IDs固定、旧unpinned intent→upgrade scan/再受付/再起動と正規mode変更、media marker再scan/enqueue/restartのpin固定、unsafe作者23の後に正常作者24/媒体stage継続、壊れたmarker後に正常novel/ugoira出力、原stat最大/同サイズ決定/警告、過去duplicate pinsが既存でも最初の確定ordinalを再利用。旧intent/manifest bytes・未知ファイルは保存。既存2試験の「複数なら拒否」期待を、原版の選択+警告および未解除ZIP拒否へ修正した。

TXT direct heavy参照の実装には本候補で変更なし。既存tests/test_direct_projection_references.pyとtest_author_projection_layout.pyを検証対象に含め、前候補の参照成立を未実行のまま継承しない。

## 検証担当へ（全てCodex未実行）

final/manifest.jsonの全SHA/HEAD/branchと固定193対応を照合。既存S venv、PYTHONPATH=<candidate>/src、PYTHONUTF8=1、PYTHONDONTWRITEBYTECODE=1、新しい深いS TEMP/TMP/basetemp/log。原期待や発火assertを弱めず実行:

```powershell
& S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -B -m pytest tests/test_export_admission_stability.py tests/test_author_projection_layout.py tests/test_author_output_connection.py tests/test_save_exports.py tests/test_bookmark_media_projection.py tests/test_bookmark_media_deep_path.py tests/test_direct_projection_references.py tests/test_save_exports_gui_fixture.py --basetemp <fresh-deep-S-temp> --junitxml <fresh-S-log>/admission.xml
```

初回受付/繰返しscan/再起動でjob数とoperation IDs不変、旧manifest/旧JSON不変、正規mode/format/世代変更だけ新受付、複数folderは最大+警告、安全拒否された項目の後も正常作者/媒体がdoneになること。codec/symlink skipは個別に記録し成功扱いしない。前段合格後に既存GUI4ケースと作者全媒体出力の確認を検証担当で判断。所有process通常終了/port解放/前後hash、8089/PID37168/EMA非干渉。Codexは試験/import/構文検査/GUIを実行しない。

今回3指摘へのコード修正と回帰作成は終了、実行結果・独立再レビューは未回収。ZIP更新/改名/物理隔離の安全契約は未解除。全原操作分母/独立監査/native/live/exact-head CI/総合UX/P3閉鎖/P4移行は未達。最終SHA/差分/旧193保全/書込み終了は専用final。旧191/189も終端でhash照合する。
