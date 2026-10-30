# P3 211の3失敗: 合成fixture/API契約修正（未実行）

候補: S:/Projects/PixivVault-NiceGUI-p3-compat-fixtures-20261027
branch: phase11/p3-compat-fixtures-20261027
HEAD: 2cf4a7a7286dd728e8df8125094498a3622babba
固定基線211: S:/Projects/PixivVault-NiceGUI-p3-acceptance-compat-20261027
基線SHA: 2be602b70795ced707780deb72ebe898a8f7d72909fd194a10c00882f2125289

開始時に211のwrite-end、全211ファイルhash、branch/HEAD、新候補不存在、対象writer不在を照合。別候補へ製品/回帰差分だけをコピーし、旧検証artifact/TEMPを混ぜない。試験/import/構文検査/build/GUI/製品起動はCodex未実行。製品変更はゼロ。

## 原票・現物の照合

基線211のCoS結果はfocused214 PASS / 1 SKIP / 3 FAIL、全pytest2846 PASS / 4 SKIP / 3 FAIL、保存GUI4/4・作者GUI4/4。これは基線結果で、新fixtureの合格へ自動継承しない。

元root artifacts/cos-p3-211-20261027/logs/ の次の原票を実読しSHA照合した。

- acceptance-compat.xml: 598a060df9d804ebbf104f6fb7545b5f586ce038456d7c593904bef71577e5a3
- full-p3-211.xml: 849cbc341635a560cd4993a4b1a68b3e875ed3797c098a8dbc42ef07fc5a329b

最終summary/欠陥MDがあるとは仮定せず、参照はinput-evidence.jsonへ記録。保持されたfocusedの合成static.sqlite3をmode=ro&immutable=1で読み、最初の301がerror / runner_failed / attempt1 / progress0であることも照合した。旧証跡や合成DBに書込なし。

## 原因と修正（tests/test_export_acceptance_compat.pyだけ）

1. **作者新世代2条件**: 元の共通fixtureは301を通常保存outputs/へ置く。一方ManagedAuthorIntent updatedの正規expected_relative_receiptはgenerations/で、_intent_specの検査に不一致だった。さらに元fixtureのハードコード更新日2026-10-27はSyntheticOriginals.version=2の2026-10-22と不一致で、そのままでは次のmetadata gateにも到達しない。これは製品契約を変える根拠にはしない。

   作者ケース専用の小さなfixtureを同ファイルに追加。既存SyntheticOriginals/ManagedPixivDownloads/Job/Contextを使い、空の専用managed rootで301をManagedAuthorIntent newにより実保存し、そのgenerations/ receiptを初期masterにする。302/303は元通り通常保存。更新日を合成providerの実get_artwork_update_revisionから得て、正規のold SHA/pathをupdatedへ指定する。raw path改変や既存outputs→generationsの移動、pointer書換え、callbackの素通しを行わない。

   元の未編集/編集済み2条件、旧投影保全、同作者別作品の保存、世代保留表示、job増殖防止、再起動のassertは維持。初期/更新とも5項目receipt・generations/、別path/SHA、update_date/accepted_fingerprint、新旧実page bytes差も確認するassertを追加し、実際の新世代取得が成立してから後段へ進むようにした。

2. **static50件窓外**: verified_saved_receiptは(relative, receipt, sha256, update_date, accepted_fingerprint)の5項目。saved_receiptsは3項目の互換projectionであり、両APIを混同していた。合成runnerで5項目へ明示展開し、実receiptのpage_countでprogressを通知して同じrelativeを返す。製品helperを差替えず、JobServiceも実物のまま。SHA形状確認を追加。

   対象301＋51 filler、50件表示窓から301消失、空remote catalog、保存2件done、対象ID301/303、DB再起動後の同じjob IDという既存条件は保持。期待をerrorへ変えず、待機時間も変更しない。

この3件は元のfixture/API前提の不備と判断。今回の読取り結果だけから追加製品欠陥を確定しない。製品src/およびtools/の全169ファイルは211と同一で、final/product-equivalence.jsonに旧新の全path/SHAと集合hashを記録する。変更はテスト1・本handoff・PROGRESSの3ファイル。

## CoS限定再試験（Codex未実行）

最初にfinal/manifest.json、branch/HEAD、旧211保全とproduct-equivalenceを照合。新しいS専用TEMP/TMP、深いbasetemp、logを使い、既存venv/依存のまま実施。旧root/process/portへ非干渉。rootを短くして以前のdeep path条件を外さない。

```powershell
$env:PYTHONPATH='<candidate>/src'
$env:PYTHONUTF8='1'
$env:PYTHONDONTWRITEBYTECODE='1'
& S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -B -m pytest tests/test_export_acceptance_compat.py::test_author_new_generation_does_not_overwrite_or_hold_other_bookmarks tests/test_export_acceptance_compat.py::test_static_done_outside_fifty_window_survives_catalog_and_process_restart --basetemp <fresh-deep-S-temp> --junitxml <fresh-S-log>/compat-fixtures-three.xml
```

対象はparameter展開3ケース。まず正規初回作者generation→実更新が通り、既存の後段assertまで到達すること、static実JobServiceがdoneで52受付→50表示窓→再起動を通ることを原票で確認。その後同ファイル全体8ケースを別basetemp/logで実行すれば他の独立作者/媒体回帰への影響も確認できる。新しい全pytest/GUI実施・製品同一hashによる211受入証拠の継承可否は検証担当/親が判断し、Codexが自動合格にはしない。

前後hash・候補所有process正常終了を記録。Windows symlink権限のSKIPは変更しない。既存未採用の同一作品投影更新方式、欠損旧identity、診断総容量方針は基線のまま。独立監査/native/live/exact-head CI/総合UX/P3全閉鎖/P4判断は今回宣言しない。書込み終了・最終SHA/manifest/3ファイル最小diff・旧候補不変は専用finalを参照。
