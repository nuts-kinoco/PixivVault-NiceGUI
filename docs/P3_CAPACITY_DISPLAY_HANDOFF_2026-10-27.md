# L12 managed表示・失効状態修正（239バッチ、未検証）

固定238 `01c691f2ac95c43e626f8cdb5dd570e258296409a7d725a6d244663a9360ae2c` から別候補へ分離。旧Flet固定 `3ac3aa0d47a64785251593bcb09bb2d5f6df374b:library_tab.py:420–445,691–703` を照合。作者全体容量は旧保存先全体、通常ソートはそのcapacity_bytesを使う。managed限定承認は全体容量欄の意味変更を含まないため、今回その上書きを撤去する。

製品変更はui/library_view.pyのみ。再計測成功は画面内 `_managed_capacities` に記録し、通常容量とは別行に「管理保存 N B」を表示。元LibrarySnapshot、author_capacities、AuthorSummary.capacity_bytes、通常容量ソート値と順序を変えない。新ディスク走査/DB/永続cacheなし。実測APIとadapterは238から無変更。

reloadではmanaged cacheとstatusをクリア。show_subview/set_active/closeではepochを失効させstatusを即消去。古いworkerの成功・失敗、検索/世代不一致でも進行中や古い失敗を残さない。busyはworker終了まで保持し二重測定を防ぐ。表示済managed値はタブ再訪でsession内保持、reload時に消去する。

既存実保存テストの正規refreshと作者91・実byte検査を保持し、新しい契約としてsnapshot同一/別managed表示を確認。新 `tests/test_capacity_display.py` は通常容量約9MBとmanaged 0/1784Bの分離、capacity順保全、reload後のstatus/cache消去、subview/active/reload/closeで失効したworker成功/失敗8条件を検査。旧検索失効テストにもmanaged cache空・status空のassertを追加。ソースの静的照合のみ、未collect/未実行。

CoS最小一括確認（専用S合成環境）:
`python -B -m pytest tests/test_managed_capacity.py tests/test_capacity_display.py tests/test_library_view.py tests/test_library_filters.py -q`

1280×720/1920×1080の実画面で、managedと旧データ相当の全体容量を混在させ、通常容量欄とcapacity順が再計測前後に同一、managed実測が明示された別表示で読めることを確認。正常0/未計測/失敗を区別し、再計測中に検索/タブ/再読込/閉鎖を通常操作してstatus消去と遅延結果拒否を確認。正常完了後のreloadでも旧statusとmanaged表示が消えることを確認。両テーマ・小さいカードで追加ラベルの欠け/overflowがないかも確認し、screenshotsとcomputed値、前後manifest、所有process通常終了を記録。実OS symlinkは権限を変えず従来SKIPを維持。

旧237/238のGUI容量値は旧コードで全体欄を上書きしていた証拠なので、今回の通常容量保全を証明しない。独立再レビューとCoS実画面確認へ戻す。Codexはpytest/import/構文/build/GUIを実行していない。旧folder/ZIP・永続cacheとP3残件は未完、33/152・43未達・76未確認は変更しない。この文書が旧managed-capacity handoffの「author_capacitiesへ反映」を今回の表示意味について置き換える。
