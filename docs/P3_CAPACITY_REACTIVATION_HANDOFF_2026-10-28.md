# L12 再起動直後の容量cache自動復元 修正候補

実装と回帰コードを納品。pytest/import/構文検査/build/GUI/製品起動/実API/実データ走査は実行していない。原L12/P3の受入・独立監査完了とは扱わず、原152操作台帳の算定は変更しない。

## 基準・原因の証拠

- 基準265: `S:/Projects/PixivVault-NiceGUI-p3-viewer-author-20261028`、branch `phase11/p3-viewer-author-20261028`、HEAD `2cf4a7a7286dd728e8df8125094498a3622babba`、dirty265、集合 `aa7957d6966e91e145b08cd24be1811d9e4ed76c0b1ec5415a09900857c5e623`。WRITE_ENDと265/265を開始時に照合。
- 新候補: `S:/Projects/PixivVault-NiceGUI-p3-capacity-reactivation-20261028`、branch `phase11/p3-capacity-reactivation-20261028`、同じHEAD。基準のmanifest掲載差分だけを複製。CoSのログ・合成データ・実データはコピーしていない。
- 原票: 固定259 `artifacts/p3-learning-fixture-fix-20261028/cos-owned-gui-r1/P3_259_L12_D15_GUI_LIMITED_ACCEPTANCE.json`、SHA `30cd4b2dce7d7484fc8256b440f4137b9babdf48c79b85740455581193327c05`。永続cache 91=1024B/92=3072B、revision1のまま、再起動直後UIが636B/212B。明示保存先更新で正値に戻る。
- 固定259/265のLibraryView初期化は `reload()` から旧613–614行でcache復元を登録。MediaLibraryViewの共通生成処理 `ui/media_views.py:29–43` は直後に子をinactiveへ設定。LibraryView `set_active` 旧915–938行でepochを失効させるが再active時のcache再登録がない。旧341–351行の遅着ガードは正常に旧要求を棄却する。この組み合わせが初回復元を失わせる経路で、CoSの実測と整合する。

## 製品修正

変更は `ui/library_view.py` のみ。SQLiteスキーマ/容量cache/MeasurementLease/MediaView/保存APIは不変更。

- cache復元が必要か、現在どのstate/generation/epoch/作者集合で読取りを登録済みかをview内で保持。
- reload、再active、downloadedへの復帰、作者詳細からの復帰、検索/ソート/フィルタ変更後に現在の作者一覧へ復元を登録。同一要求のpending中および復元完了後の同じactive通知は追加読取りを作らない。
- inactive/close/読込失敗/別subview/作者詳細表示中には登録しない。旧state/generation/epochは引き続き拒否し、開始前に既に失効した要求はSQLite読取りも始めない。
- 計測中は新しい復元を登録せず、現在の計測workerが実際に終了してから必要な復元を再登録。busyを早く下ろしたり、SEALEDを物理commit成功と扱ったりしない。
- 読取りは既存 `service.cached` を `asyncio.to_thread` で呼ぶ。旧フォルダー/ZIP/managed保存を走査せず、cacheへ書かない。対象は従来どおり表示中先頭50作者。
- 成功したcache値のみ既存全体容量欄/ソートへ戻す。0は確定0、Noneは未取得として旧表示を保持。managed容量は別表示のまま。失敗で0へ変換しない。
- この修正は起動時cache復元の再登録であり、旧+managedの新合算、画像やDBの更新、原値の再計測、永続方式変更ではない。

## 回帰コード

`tests/test_capacity_reactivation.py` は15パラメーターケース（未実行）。

1. 合成SQLiteをclose/reopenしてから実MediaLibraryViewを生成。生成時のinactive→最初のactiveで1024/3072/0とNone保持、実表示ラベル、容量順、revision不変を検査。refresh_library_snapshotは実処理を包むspyで呼出0を検査し、明示更新に依存しないことを検出する。managed ownerのsentinelはMediaViewのイラスト分岐を選ぶだけで、製品callback/保存/計測の成功を代行しない。
2. 実cache読取りをEventで停止。inactive/ブクマ往復/作者往復/検索/reload/closeの6遷移 × 旧読取り成功/失敗。旧777B応答や旧失敗表示が新画面へ入らず、現世代cacheの1024Bを復元する。close後は元snapshot保持。
3. 現在cacheの読取り失敗はsnapshot/DB/revisionを保持し、再activeで正規読取りを再試行。
4. 実MeasurementLeaseを使う計測workerの停止待ち中にinactive→active。worker終了まで復元taskを増やさず、取消成立後にcacheを自動復元。旧計測999Bを画面/cacheへ採用せず、revisionも不変。

NiceGUI slot/clientと実coroutineを保持。background_tasks.createだけをpytestの稼働loop上のasyncio.create_taskへ結線し、no-op化していない。全taskをdrainし、threadのEventを解放してからClient/services/cacheを終了する。Event待ち5秒は合成検査の停止検知用で、製品応答時間の新基準ではない。

既存 `tests/test_legacy_capacity_connected.py` は再activeで新たに正規登録された復元taskをawaitする順序へ適合（task数2→3の根拠をコメント）。元の取消、revision、表示、再生成復元assertは維持。他のL12/D15/PV-B回帰を変更していない。

## CoS 一括検証（未実行）

前後に新候補manifest/HEAD/dirty、固定265/259のhash、writer/所有PIDを照合する。既存のS側venv、候補cwd、`PYTHONUTF8=1`、`PYTHONDONTWRITEBYTECODE=1`、`PYTHONPATH=<candidate>/src` を使用。新規依存なし。全出力はこの候補の新しいcos-owned領域。

```powershell
# CoS専用。実装担当では未実行。
& S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_capacity_reactivation.py tests/test_capacity_cache_injected.py tests/test_capacity_cancel_nonblocking.py tests/test_capacity_display.py tests/test_capacity_lease_sqlite.py tests/test_capacity_normal_configuration.py tests/test_capacity_selection.py tests/test_legacy_capacity_connected.py tests/test_managed_capacity.py tests/test_library_view.py --basetemp=artifacts/p3-capacity-reactivation-20261028/cos-owned-r1/tmp --junitxml=artifacts/p3-capacity-reactivation-20261028/cos-owned-r1/l12-impact.xml
```

GUIは259のCoS所有合成ハーネスを検証担当が再利用する。元の専用fixture/原票を変更せず、新候補と新しいcos-owned rootへ向ける。新候補のPV-B差分も含むため表示の例外を観測し、未実行のPV-B受入を成功としない。

- 同じ合成作者91/92の保存先条件（旧ZIPを優先して1024B、選択folderが3072B）で計測してcache revision1を作り、通常終了。
- 再起動後、実MediaLibraryViewの初回表示で**明示「保存先更新」や「再計測」を押さずに**1024B/3072B・容量順が自動反映されることを、両解像度1280×720/1920×1080で確認。cache読取り完了のDOM状態/数値を待ち、固定sleepや期待値緩和はしない。before/after screenshotとDOM、cache read-only snapshotを分けて記録。
- タブ離脱/復帰、作者往復、ブクマ往復、検索/ソート変更中の遅着、計測中の取消/再active/closeで旧応答/旧statusを出さない。別表示のmanaged容量と通常全体容量の意味を維持。
- 0/None/失敗旧値保持、DBのrevision不増、計測自動再実行なし、実Cookie/実API/実保存物へのアクセスなしを確認。
- 所有serverを通常終了しPID/port解放、固定候補のhash一致を記録。他のCoS実データ試験・8089・他所有PIDには触れない。

## 保全・未達

専用artifactsの `manifest.json`, `changes-vs-265.diff`, `source-manifest.json`, `preservation.json`, `WRITE_END.txt` を参照。PV-Bの製品4ファイルとfixture/tools、D15、capacity_cache/legacy_capacity/selection/managed保存、固定265/259は一致を記録する。
試験・実機・独立レビューはCoS/Claude待ち。CoSの別途実データ試験結果は未回収で本修正の証拠に流用しない。L12全受入/P3完了や台帳33/152等の算定を自己更新しない。PV-Bの原寸/単独ページ保存等の残件も継続保全。
