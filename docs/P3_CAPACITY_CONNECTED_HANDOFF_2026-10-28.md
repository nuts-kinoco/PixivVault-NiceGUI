# L12 旧保存容量：明示注入候補（CoS検証待ち）

固定245 `e4bfaefad720e12c72b8a4c51b0792225c8c50e07ad18d8fadef245395b64ed9`、固定240 `23c067d731db48fd02cd0e34fa3ce2f2cddc30b637cfe7ef0629300bbdf5a692` を保全し別候補へ分離。前候補のcapacity_cache / capacity_selectionを利用。旧creator ID接尾辞、ZIP優先・同種最大・同値先頭の原版選択。明示rootのfolder全ファイルかZIP本体をread-onlyで計測、未発見0、例外None。managed容量は別表示のまま。

`LegacyCapacityReader(root)` は呼出側が明示的に指定したrootだけに作用、通常起動で旧rootを自動走査しない。リンク/reparse/不正fileを拒否し、失敗は作者単位None。`CapacityCache` に明示接続を注入しinitialize、`LegacyCapacityService` はrootをhashで結び付け、別rootへの再使用を拒否。利用者/旧DBへ適用していない。接続をUI非同期workerから使うため合成SQLiteは `sqlite3.connect(path, check_same_thread=False)` で作成し、所有元がcloseする。

`services.configure_legacy_capacity(reader,cache)` をLibraryView生成前に呼んだ合成fixtureで旧容量ボタンを有効化。先頭50作者の計測、成功/確定0のみrevision CASでcache保存し、失敗・CAS衝突は旧値保持。再生成時はcacheを非同期で読みLibrarySnapshot容量と通常ソートへ反映。操作中の検索・filter・sort・作者選択・subview・非active・reload・closeはleaseを失効させ、失効後のpublishを拒否。キャンセルとSQLite publishはlockで直列化するため、SQLite書込み中のcancelは終了待ちになる可能性がある。UI応答時間と競合時の線形化はCoS/独立監査で確認が必要。未接続時は旧容量ボタンを無効にする。

本候補は依存を明示注入した合成領域での製品接続コードであり、通常起動の構成経路や実利用データ適用は未実装・未承認。旧保存先が変化中の走査に対する強い瞬間snapshotは実証していない。L12原全体の完成や正式受入として扱わない。

CoS: 候補と固定245/240のmanifest、所有processを照合。S専用tmp下にsynthetic旧rootとcache SQLiteを作成し `CapacityCache(db).initialize()`、`LegacyCapacityReader(root)`、`services.configure_legacy_capacity(reader,cache)` を通常のfixture LibraryView生成前に注入。 `python -B -m pytest tests/test_capacity_cache_injected.py tests/test_capacity_selection.py tests/test_legacy_capacity_connected.py tests/test_managed_capacity.py tests/test_capacity_display.py tests/test_library_view.py tests/test_library_filters.py -q` を実行。追加確認は複数ZIP/フォルダーと同値順、全filesの実サイズ、未発見0・権限/不正リンク/途中失敗None、作者別旧値保持、50超・再起動/ソート、別root混同拒否、検索/タブ/close/refreshと遅延workerおよびcache CAS競合、managed別表示を含む。2解像度・両テーマで通常操作とGUI応答を確認。symlink権限不足はskipとして保持しOS設定変更しない。固定240/245と元データの不変、owned process正常終了・port解放を記録。

Codexはpytest/import/構文/build/GUI/実APIを実行していない。独立監査、CoS実測、native/live/exact CI、P3全体と台帳受入は未完。旧245部分実装文書の未接続状態は本候補の上記コードについて更新する。
