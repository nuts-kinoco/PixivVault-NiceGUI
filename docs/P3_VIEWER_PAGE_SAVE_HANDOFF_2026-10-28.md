# PV-B 表示ページだけ保存: managed receipt接続候補

実装/回帰コードの納品。pytest/import/構文検査/build/GUI/製品起動/実API/実データ操作は未実行。元152操作の分母/算定は不変更。原個別DL全体/P3受入・独立レビュー合格とはしない。

## 基点と原仕様

- 固定267: `S:/Projects/PixivVault-NiceGUI-p3-capacity-reactivation-20261028`、branch `phase11/p3-capacity-reactivation-20261028`、HEAD `2cf4a7a7286dd728e8df8125094498a3622babba`、dirty267、集合 `24fe5146d152a56cd58ed7dfeaff4913b93dd8f5f6ac33dc43968a80c309b609`。開始前にWRITE_END/267個別SHAを照合。
- 新候補: `S:/Projects/PixivVault-NiceGUI-p3-viewer-page-save-20261028`、branch `phase11/p3-viewer-page-save-20261028`、同じHEAD。固定manifestのコード差分だけ複製、CoSの旧artifact/実データは複製しない。
- 原Flet固定ref `3ac3aa0d47a64785251593bcb09bb2d5f6df374b` の `docs/design/ARTBOOK_VIEWER_SPEC_2026-08-19.md:84–92` は個別DL=表示ページ1枚、全作品queueとは別。`artbook_viewer.py:957–1026` は原画像の選択index、クリック時のsnapshot、保存完了の遅着UI抑止。`909–952` は送信中/保存済/失敗のボタン状態と同一ページへの反映。`875–907` はlast_backupの記録を全作品完了と区別する。
- 基点ViewerControllerはpage番号を渡さず `ViewerSave.save→save_explicit_work` を呼び、全ページjobを追加していた。今回、原どおり個別DLを静止画/漫画の選択1ページへ接続する。作品全体の保存API・queue・JobServiceは残す。

## 実装の範囲

1. `managed_viewer_page.py`: 既存ManagedPixivDownloadsの専用rootだけを使用。work ID/author/page/count、既存の認証/原画像transport、画像形式/画素/bytes/SHA/リンク拒否を再利用。縮小previewを保存したりJPEG再圧縮したりしない。全URL配列の整合は検証するが画像GETは指定indexの1回だけ。
2. 全作品の有効receiptがある場合はその選択ページの実bytesを検証して再利用。画像GET/コピー/active pointerの更新なし。
3. 未保存の1枚は `viewer-pages/work-<ID>-page-<1-based>/` に画像と `scope=pixiv-original-page` receiptを格納。receiptには元ページ総数と0-based選択index、受理metadata、実画像hashを残す。初期所有markerを検査し、専用incompleteから検証後のdirectory renameで公開する。既存成果の上書き/削除、未知データの採用はしない。長パスI/Oは既存native_io_path経由。
4. receiptは既存の全作品v1 receipt/active-generationsには登録しない。単ページを完全保存として作者差分/scheduler/ライブラリ/autoZIPへ混入させない。全作品DLは後から全ページを取得できる。同ページの再押下・再起動・公開後通知喪失は実bytes/receipt照合で再利用。破損した対象は保存失敗、他ページを巻き込まない。
5. ApplicationServicesの明示ページ保存をoff-loopで実行。選択はUI側で受付時に固定。受付後のページ移動/閉鎖は保存workerを中途放棄せず、表示だけを世代で棄却。アプリ終了はI/Oの終了を待ってからHTTP/保存ownerを閉じる。これは同じ既存close経路のbarrier追加で、旧L12 lease/cacheは変更しない。
6. 実ヘッダー個別DLからpage/count/authorを渡す。画像未decode/旧イベント/二重clickを拒否。DL中→保存済または失敗→再試行。ページ移動後は古い成功/失敗を新ページへ表示せず、終了後に新しいdecode済ページのボタンを復帰。

## 必ず分ける未達

- この候補はmanaged領域の単ページ原画像/receipt接続。原版のauthor/workフォルダー互換配置・`use_work_folder`・原ファイル名のprojection、およびlast_backup一覧への単ページ履歴接続は未達。CAS内 `page-N.ext` は内部名であり、原版ファイル配置の完全互換とは報告しない。
- 再起動後にこのreceiptを照合して保存結果を再利用できるが、部分保存の新ライブラリcatalog/作者全容量の混合規則は作っていない。旧DBへtouch_last_backupを実行していない。
- 同じページの既存成果は不変。遠隔更新時の部分保存の置換/世代更新契約を勝手に導入しない。metadata不一致/改竄なら拒否し、自動上書きしない。
- 原版うごイラ個別DLは表示用GIF保存（固定artbook_viewer.py:961–964）。本候補の共通静止画経路へ新しいアニメーション変換を加えていない。既存うごイラ専用保存/再生、作品全体dispatcherは保持。うごイラの個別GIF導線は別残件。
- 原寸切替/ズーム/パンの未採用条件、ZIP更新/作者改名/物理隔離は変更しない。

## 回帰コードと一括実行（CoSのみ、未実行）

`tests/test_viewer_page_save.py` は32パラメーターケース相当（未collect）。原画像GET indexとbytes一致、部分/完全receipt分離、後続全作品保存、完全receiptからの無GET再利用、再起動、8種類の改竄拒否、画像/receipt/rename/公開後通知喪失の4実注入（発火assertあり）、不正入力、277文字超native path、未知root保全、hardlink拒否、reparse属性の実対象注入、破損ページの局所化を含む。実GalleryViewのclick handlerで失敗→再試行→ページ移動→遅着拒否→次ページ保存を確認。閉鎖/古い画像イベント、task取消/HTTP closeのdrainを区別する。既存assertの削除/skip追加なし。

新候補cwd、既存S側venv、`PYTHONUTF8=1`、`PYTHONDONTWRITEBYTECODE=1`、`PYTHONPATH=<candidate>/src`。既存設定を使用、install不要。出力は新規cos-owned領域のみ。

```powershell
# 実装担当では未実行。CoSで一括実行。
& S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_viewer_page_save.py tests/test_viewer_save.py tests/test_managed_downloads.py tests/test_managed_download_requests.py tests/test_managed_download_catalog.py tests/test_p3_viewer_author.py tests/test_artwork_viewer.py tests/test_gallery_pages.py tests/test_library_view.py tests/test_three_media_services.py --basetemp=artifacts/p3-viewer-page-save-20261028/cos-owned-r1/tmp --junitxml=artifacts/p3-viewer-page-save-20261028/cos-owned-r1/page-save.xml
```

テストファイルの存在は納品時に静的照合。新テストのfakeは通信/画像だけ、保存owner/services/UI handler/receipt検証は製品コード。故障の5秒Event待ちは検査停止検知で製品応答基準ではない。

## 合成Edgeの実操作

`tools/serve_viewer_page_fixture.py` は実ApplicationServices/GalleryView/ViewerSave/ManagedPixivDownloadsを結線。HTTP clientは生成せず合成RGB原画像3枚だけ。rootはこの候補の専用artifacts配下のmarker付き領域に限定。rootを維持して再起動できる。

```powershell
# CoS専用。52381は例、担当が空きを確認する。8089や他所有PIDを変更しない。
& S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -B tools/serve_viewer_page_fixture.py --port 52381 --root S:/Projects/PixivVault-NiceGUI-p3-viewer-page-save-20261028/artifacts/p3-viewer-page-save-20261028/cos-owned-r1/gui-data
```

- 1280×720/1920×1080、dark/lightで合成123カードを通常click。2/3へ移動し画像decode後に個別DL。DL中/保存済と「このページを保存しました」を確認。`/_fixture/state`は観測専用で、APIから保存を代行しない。
- 原画像取得callsはindex1だけ、receiptはpage_no1/総数3、image SHAはexpected配列index1、whole_work_jobs=0/complete_receipts=0。実画像GETなし。fixture previewは保存原画像とは別の合成画像で、画面thumbnailを誤保存した場合にhash不一致となる。
- 同じページを再押下、通常終了して同rootで再起動、再び2/3から個別DL。新プロセスのoriginal_image呼出0、同receipt/hashで再利用を確認。
- 未保存3/3で合成保存失敗ON、通常DL→失敗/再試行可能/receiptなし。OFF→DL→page_no2だけ新規完了。失敗を保存済にしない。別ページに前ページの保存済文言が残らない。
- 元ビューアのページ送り/詳細/作者表示、ラベルのクリップ・重なり、console/page error/外部requestを確認。原Pixiv入口は外部へ移動せずURLを照合。
- before/after screenshot/DOM/calls/個別receipt+image SHAを記録。HTTP200やreceipt存在だけでGUI合格としない。所有server通常終了、PID/port0、候補/固定267 hash照合。

## 既存証拠と保全

親共有の固定265結果: CPU278PASS/4SKIP、合成Edge16/16PASS。原票は265の `cos-owned-summary-r1/P3_265_CPU_SYNTHETIC_ACCEPTANCE.json` と `cos-owned-synthetic-gui-r3/synthetic-ui-result.json`。今回の共通viewer変更後候補へ無条件継承しない。固定259の実GET7成功と、実データGUI切出しの安全判断資料不足による保留も別証拠として保持。拒否された操作を再送/迂回しない。

固定267はCoSが検証中。LibraryView/capacity cache/lease、D15製品・回帰、265の作者/metadata部品はbyte同一を記録。servicesと共通viewer/managed ownerは本変更の影響範囲なので、同一性と機能無影響を混同しない。L12等の進行中成果へのマージは行わない。

完全hashと差分は `artifacts/p3-viewer-page-save-20261028/{manifest.json,source-manifest.json,changes-vs-267.diff,preservation.json,WRITE_END.txt}`。独立レビュー・CoS実行・native・live・CI・全P3横断受入は別の未達として残す。
