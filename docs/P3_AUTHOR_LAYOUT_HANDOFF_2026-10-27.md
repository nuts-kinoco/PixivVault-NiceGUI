# P3 原版heavy/light配置候補・保存契約の保留

専用候補 S:/Projects/PixivVault-NiceGUI-p3-author-layout-20261027、branch phase11/p3-author-layout-20261027、HEAD 2cf4a7a7286dd728e8df8125094498a3622babba。固定zip-init-io189 / SHA f359a0fbde727df273e6392a95e448e786f2c7e4f2f5345b39c5da54842e878a はClaude検証中のため一切編集/停止しない。開始時189/189を照合し、別候補へmanifest対象だけコピー。検証artifacts/専用データはコピーしない。製品/試験/import/構文/GUI未実行。

## 原操作と製品差分

原core.pyの現物2869–2898では登録作者ではなく物理author_entry.folder/zipがheavy/light判定根拠。3043–3111ではheavyは作者実体からbookmarkへmirror、lightはbookmark側だけ、directなら平坦配置。既存候補はlinkで常にマスター側も新設するheavy固定の配置だった。

bookmark_projection.author_folderは専用projection root内のID末尾作者フォルダーを既存_safe_path/native_io_pathで確認し、複数候補なら拒否。CAS、元PV、旧フォルダーを走査・移動しない。作者登録/author_targets/保存CASの存在をheavyの代わりにしない。新規bookmark受付だけでauthor_folder名またはNoneを既存identityへ固定し、そのhashを既存operation_idとする。失敗/再起動後は保存済みidentityを使い、後のフォルダー出現で再分類しない。旧fieldなしintentは旧出力のまま再試行する。

SaveExportsとbookmark_projectionの静止画/小説/うごイラは、light linkならbookmark作者/作品フォルダーだけ、light directならbookmark直下だけ。heavyは既存の作者側フォルダー名を再利用し、bookmark側は現作者表示名を用いる。direct heavyは平坦bookmarkと作者側の両方、link heavyは両側の階層配置。全てprojection所有copy由来のhardlinkで、CASへの書込み可能linkは張らない。フォルダー名は単一basename/作者ID末尾で検証。

小説TXT/frames JSONのdirect heavyは1個の変換済みbodyを全配置に使い回さない。bookmark側は既存の平坦参照書換え、作者側は元の相対参照を保つ。それぞれのbytes hashに対応する所有bodyと既存manifest記録を使用。再変換/再レンダリング/尺やdelay変更はしない。編集済み/別inodeの出力を上書きしない既存拒否を維持。

既存heavy回帰の期待値を削らず、実作者フォルダーが存在する元の前提をfixtureへ明示した。新tests/test_author_projection_layout.pyはnovel TXT/ugoira frames × direct/link × heavy/lightの8例で出力数/配置/CAS不変/相対frames参照/受付後の物理変化/同一job再実行/再起動を検査し、旧intent配置と曖昧作者拒否1例も追加。実行はチャット担当。

## 未達と必要な判断（P3未閉鎖）

1. 原ZIP追記/更新/作者改名はLEGACY_ZIP_EXPORT_2026-10-23.mdの既存ZIP・同作者別名拒否と異なる。今回その拒否は解除しない。推奨は新専用rootでmanifest/SHA所有が証明できるZIPだけ更新対象とし、未知/旧PVのZIPは引き続き保護すること。判断対象は「app所有ZIP更新を許す範囲」「作者改名時に旧名を残すか移すか」「manifestとZIP公開が中断した場合の復旧契約」。同名更新と改名の複数path更新を一つのatomic操作と偽らず設計/受入が必要。未知ZIPの自動採用は行わない。
2. 原ZIP所有作者はbookmark保存/ミラーをスキップするが、現候補は成功したCAS保存と別export処理を持つ。旧ZIP作者をどう識別していつスキップ表示するかは、上記ZIP所有契約に依存。新ZIPの存在だけでダウンロード/receiptを無効にする仕様を追加しない。
3. 元マスター作者フォルダーの読取り所属を専用projectionへ引き継ぐ操作、bookmark以外の作者保存からマスターcopyを作る入口は未接続。今回は専用rootに存在する実フォルダーだけを判定する範囲。旧ユーザーフォルダーの自動importやCAS作者存在の代用はしない。原重複の物理隔離/ZIP部分削除と不変receipt/CASの判断待ちは維持。

上記はP3実装側終了条件の未達として残す。非依存のheavy/light配置はコード候補にしたが、P3全完/全操作分母確定/P4移行とはしない。固定候補のCPU39実行結果は変更後の検証に加算/継承しない。独立監査/native/live/exact-head CI/総合UXも別未達。

## チャット/Claudeの検証手順（Codex未実行）

final/manifest.jsonと旧189のSHA対応を照合。既存S venv、PYTHONPATH=<candidate>/src、PYTHONUTF8=1、PYTHONDONTWRITEBYTECODE=1、新しい深いS TEMP/TMP/log/basetemp。次を実行し、codec skipは個別記録、権限変更/短root回避なし。

```powershell
& S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -B -m pytest tests/test_author_projection_layout.py tests/test_save_exports.py tests/test_bookmark_media_projection.py tests/test_bookmark_media_deep_path.py tests/test_direct_projection_references.py tests/test_save_exports_gui_fixture.py --basetemp <fresh-deep-S-temp> --junitxml <fresh-S-log>/layout.xml
```

期待: light側に新しいマスター作者フォルダーなし、heavyは原folder再利用+bookmark出力、CAS不変、frames順序/delay/参照先bytes保持、旧intentの再実行不変、編集拒否、失敗Retry/再起動で同一jobの配置固定。既存fixtureの新規lightは出力ファイル数がheavyの半分になるがsource job数/保存成功/manifest proof条件は弱めない。実GUI4ケースは前段合格後に親側判断で既存check_p3_save_exports_browser.pyを使用。所有process正常終了・port解放・前後hash一致、8089/PID37168/EMA非干渉。

最終候補hash/最小差分/旧189不変/書込み終了は専用artifacts final。
