# P3 managed intent I/O 修正候補（CoS 検証前）

## 基線と観測根拠

専用候補 `S:/Projects/PixivVault-NiceGUI-p3-intent-io-20261027`、branch `phase11/p3-intent-io-20261027`、HEAD `2cf4a7a7286dd728e8df8125094498a3622babba`。固定基線は `p3-export-fixture-args-20261027`、178ファイル集合 SHA `2de889ea3610cfde5fe668d5c9b4724de88340a4658108619c7eb99e5097faf8`。開始前の作成処理を照合し、成功済み clone を再送せず引継ぎ。候補固有writerなしを確認。コピーは基線manifestの製品・テスト・文書のみ、CoSログ/一時データはコピーしていない。

CoS原票 cpu.xml SHA `8F887212A2343297C08B26A46567F95A3C1C07ECEDA405B42168301374CC5D5A`、thread-trace diagnostic.xml SHA `567BA62F49DDD87E9DB113EB8A11603AE85C8CC9B2760A5B6843BA9C4F68F2AB` の現物一致を確認。正確な相対pathは `artifacts/p3-intent-io-20261027/evidence-identity.json`。元の5PASS/2FAILは固定基線だけの結果。

実証済みなのは、1件の worker-thread 例外が一時 intent の open、FileNotFoundError/errno2/WinErrorなし、論理path長261だったこと。製品コードではこの `_write` のほか、名前空間準備・走査・公開先確認・rename・読取りがnative_io_path未適用だった。Windows長path対応の適用漏れをこの保存境界全体で修正した。実際の修正後成功は未確認であり、2件目の内側例外が同じだったとは断定しない。

## 最小変更と原契約

- `managed_download_requests.py`: root/requests/request-incomplete と `_path()` の公開表記は論理pathのまま。既存 `native_io_path` を実I/Oと、その同じ実体に対する `_safe_path` 所有/リンク検証へ適用。marker/namespace、scan、read、exclusive stage write、fsync、存在再確認、rename、公開後fsyncを揃えた。v1/v2のJSON schema、operation_idのSHA命名、内容衝突拒否、上限、lock、CAS側呼出しは変更しない。OS設定変更/短root回避なし。
- `ManagedRequestError` の公開文字列は従来どおり。I/O失敗時の `io_failure` 属性だけに operation/error_type/errno/winerror を格納。例外メッセージ・filename・利用者path・内容・operation_idは含めず、通常tracebackの `from None` を維持。新しいログや管理ファイルは作らない。
- 一時ファイルの契約は保持。成功renameは今回のstagingだけを消費。公開前失敗のstagingは不活性なまま保持し、再起動で自動公開/再利用/一括削除しない。未知データを削除しない。再試行は完全な新stagingを公開する。公開後fsync失敗は「未保存」と断定せず、既存内容一致による再受付/再起動読取りで照合する。ディレクトリfsyncは既存helperどおりWindowsではno-opであり、電源断耐性を実測済みと呼ばない。
- `tests/test_managed_download_requests.py`: lstat/rename/fsync注入の論理path比較を正規化し、発火assertを追加。reparse注入が別試験のローカル未定義変数を参照していた部分を正しい試験へ移し、NameErrorを拒否成功と誤認しない。元のraises/衝突/容量/残骸/再起動/未知データ保全条件は保持。
- 新規 `tests/test_managed_intent_deep_io.py`: 論理root長240以上、実staging/公開先260超でv1/v2-new/v2-updated受付・読取り・同値再受付・衝突・再起動。書込み前/部分書込み/公開前fsync/rename/公開後fsyncの5境界でラッパー呼出しと実発火を分離。公開前不在/公開後同値復旧、一時残骸/未知データ/CASとは別のintent marker保全、読取り拒否、途中namespace初期化復旧を検出する。書込み先がnative表記であることもWindowsでassert。全て合成データ。

製品差分はmanaged intent一ファイルだけ。GUI checker/fixture metadata/ボタン/保存形式/receipt/CAS/ZIP/projectionは変更していない。静的読み戻しだけ実施し、pytest/構文検査/lint/アプリ/GUIは実行していない。過去のPASSやGUI結果を変更後候補へ継承しない。

## CoS 再確認（順序と終了条件）

1. `artifacts/p3-intent-io-20261027/final/manifest.json` の全dirty集合、branch/HEAD、base178対応と `base-preservation.json` を照合。新しいS専用TEMP/TMP/pytest basetempとログrootを使用し、元fixture証拠は保全。既存S venvだけ使用し、PYTHONPATH=<candidate>/src、PYTHONUTF8=1、PYTHONDONTWRITEBYTECODE=1。8089/他所有processは触らない。
2. CoS権限で次の限定CPUを実行（この文書は実行記録ではない）。

```powershell
& S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -B -m pytest tests/test_managed_download_requests.py tests/test_managed_intent_deep_io.py tests/test_save_exports_gui_fixture.py tests/test_p3_residuals_fixtures.py --basetemp <fresh-S-owned-temp> --junitxml <fresh-S-owned-log>/cpu.xml
```

全期待例外・発火assert・深いpath・v1/v2再起動を保持し、以前失敗のreview_saveとauthor_three_mediaを個別にPASS確認。失敗時は `.io_failure` の4項目と対象試験/境界を保持、秘密やraw利用者pathの公開ログを増やさない。コーデック等の既存条件によるSKIPは個別に記録し、未到達を成功にしない。深い一時rootを短くして回避した結果を受入にしない。

3. 前段CPU成功後、同じ候補/別の新S出力rootで既存の実UI checkerを実行。

```powershell
& S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -B tools/check_p3_save_exports_browser.py --channel msedge --output <candidate>/artifacts/<fresh-owned-export-gui-run>
```

`P3_EXPORT_GUI_HANDOFF_2026-10-27.md` の4ケース（1280/1920 初回+同データ再起動）を維持。設定保存→公開/非公開bookmark実ボタン選択→source保存job→export成功/拒否/Retry表示→projectionのTXT/相対画像をnative readerで再利用。projectionと元CASの閲覧証拠は区別。BOTH/件数/metadata/reader参照・hash/再起動preferences/jobs一致・外部request0・通常所有終了/port解放・前後source不変を確認。成否不明の操作は照合前に再送しない。

## 保留と書込み終端

本候補では試験/GUI未実行、2件目の原因同一性と修正後結果はCoS確認待ち。独立コード監査、native配布、live、exact-head CI、総合UX、P3全完/P4判断は別の未達。旧ZIP変更・物理隔離・heavy/light/完全hardlink互換等の未採用差分は維持。

最終集合SHA/個別SHA/基線との差分はfinal/manifest.jsonとminimal.patch、基線178保全はbase-preservation.json、書込み終了はwrite-end.json。基線178に加え今回触った基線の元ファイルも再hashする。所有app/pytest/serverは起動していない。最終process-state.jsonは候補参照Python/pytestの読取り確認であり、他所有processの操作ではない。
