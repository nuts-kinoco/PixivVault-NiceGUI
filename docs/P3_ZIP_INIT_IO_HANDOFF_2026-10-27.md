# P3 legacy ZIP initialization / native IO 修正候補

専用候補 S:/Projects/PixivVault-NiceGUI-p3-zip-init-io-20261027、branch phase11/p3-zip-init-io-20261027、HEAD 2cf4a7a7286dd728e8df8125094498a3622babba。固定基線p3-export-navigation-20261027 / dirty186 / SHA ffc5ffc0bf6177f0adba4fb354f2e0c7a0c3eba0d617a347126ce3f5d08a9154。作成前に候補不存在/writerなし、基線186/186の個別SHA一致を確認。基線manifest対象だけコピー、CoS logs/fixture/tempはコピーしない。

## 原票と対応

旧候補 artifacts/p3-export-navigation-20261027/cos-owned-20261027-0616-closure-wiring-r1/focused.xml SHA 96AC03A605243B06272A352786DA82A53E13C8E2580B2983AF5B4324D5A1247F を現物照合・実読。test_server_factory_has_isolated_export_root_and_owned_scanner_hook の factory→configure_save_exports→SaveExports→LegacyZipExporter(archives)→marker _write→path.open(xb) がFileNotFoundError/errno2。discovery singleflight/drainは同原票でPASS。root mkdirの後に長いmarker名を論理pathのままopenしていたこと、既存native helper未適用が現物で確認できた。修正後の原因閉鎖はチャット実行待ちであり、長さだけで全失敗原因を断定しない。

製品変更はlegacy_zip_export.pyのみ。_export_pathの所有/リンク検査とresolve、marker作成/読取り、root/incomplete作成/走査、staging open/ZIP再検証、既存同作者ZIP走査/存在検査、os.linkの両端、fsync、finally unlinkと再起動回収を同じnative IO表記へ揃えた。公開self.root/self.incomplete/返却Pathは論理表記を維持し、managed.rootも論理表記へ正規化して比較する。rootがmanagedの内側/外側に重なる拒否は維持。ZIP member名/圧縮/サイズ条件/内容世代検証/lock/公開例外文字列は変更しない。

atomic性: 既存os.linkによる排他的公開を維持。未完成stagingを公開せず、既存ZIPに追記/上書き/改名しない。公開前失敗ではZIPなし、公開後fsync失敗では既にZIPが存在し得るため未保存と断定しない。自分のstaging unlinkが失敗した場合は既存の限定回復（zip-UUID.tmp/regular/non-reparse/nlink1or2）だけを再起動時に回収。未知ファイルは拒否して保存。marker初回作成が途中失敗したrootを新たに所有扱いする仕様は追加しない。Windowsのdirectory fsyncは既存helperどおりno-op、電源断耐性の実証ではない。

新tests/test_legacy_zip_deep_io.pyの7ケース: 実論理root270以上でmarker/ZIP作成・CRC・再open、同作者別名ZIP拒否と元receipt/画像利用の不変、publish前/postpublish fsync/cleanup障害の実発火assert、残存hardlinkの再起動回復、marker書込み前/部分書込み中断の所有拒否・未知データ保全、未知incomplete拒否、managedとの重複root拒否。元receiptは既存合成writerで作成し実Pixivを呼ばない。Windowsではnative表記を境界でassert。テストコード作成のみで実行していない。

export/discoveryのfactory/startup呼出し、scanner条件、ジョブ受付、GUI checker、保存形式は変更しない。本候補はlegacy ZIP exporterの全I/O表記修正であり、製品全モジュールの深いpath対応が受入済みとの主張ではない。隣接SaveExports/JobServiceの保存契約やSQLite URIは変更していない。チャット再試験で他境界の失敗があれば別の実例外として扱い、短rootやOS設定で回避しない。

## チャット担当の限定再試験

final/manifest.jsonの個別/集合hash、HEAD/branch、base-preservation.jsonの旧186照合。既存S venv、PYTHONPATH=<candidate>/src、PYTHONUTF8=1、PYTHONDONTWRITEBYTECODE=1、前回同等以上に深い新S専用TEMP/TMP/basetempと新ログを使用。元ログは保全。まず原票の同じ2件:

```powershell
& S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -B -m pytest tests/test_save_exports_gui_fixture.py::test_server_factory_has_isolated_export_root_and_owned_scanner_hook tests/test_p3_closure_wiring.py::test_application_startup_is_nonblocking_once_and_drains_before_resource_close --basetemp <fresh-deep-S-temp> --junitxml <fresh-S-log>/wiring.xml
```

export factoryのowner/archives/projection markerとjobs初期化、scanner lifecycle登録、discovery singleflight/drainを元の期待どおり確認。次に同じ新候補で:

```powershell
& S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -B -m pytest tests/test_legacy_zip_deep_io.py tests/test_legacy_zip_export.py tests/test_save_exports.py tests/test_save_exports_gui_fixture.py --basetemp <another-fresh-deep-S-temp> --junitxml <fresh-S-log>/exports.xml
```

全発火assert/CRC/同作者別名拒否/元receipt不変/未知データ保全を維持。失敗は元例外型・errno/WinError・境界を区別し、秘密値や利用者raw pathを公開しない。新候補の未実行を旧PASSで代用しない。GUI/nativeはこの担当では実行せず、後続GUI4ケースの判断は親へ戻す。通常所有終了/port解放と前後hashを確認し、8089/PID37168/25010へ非干渉。

書込み終了・全SHA・最小patch・旧186保全は専用artifacts final。pytest/import/構文検査/製品起動/GUIは未実行。独立監査/native/live/exact-head CI/P3全完/P4移行は未達。
