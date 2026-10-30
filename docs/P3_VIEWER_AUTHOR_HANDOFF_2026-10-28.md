# PV-B 作品詳細→作者一覧 接続候補（2026-10-28）

実装・回帰コードの納品。試験/import/構文検査/build/GUI/製品起動/外部通信は実施していない。機能受入、独立監査、PV-B/P3全体完成ではない。原152操作の算定を変更しない。

## 基準と保全

- 基準259: `S:/Projects/PixivVault-NiceGUI-p3-learning-fixture-fix-20261028`、HEAD `2cf4a7a7286dd728e8df8125094498a3622babba`、dirty259集合SHA `91d11437746cf7afc5053f7127ddcd0cb31a6b5adcc788313236e5f5eeab4f97`。
- 新候補: `S:/Projects/PixivVault-NiceGUI-p3-viewer-author-20261028`、branch `phase11/p3-viewer-author-20261028`、同じHEAD。259のmanifestに掲載された差分だけをローカル複製。CoSログ/実データ/実設定はコピーしていない。
- `artifacts/p3-viewer-author-20261028/manifest.json` に全dirtyファイル、集合hash、259からの変更を掲載。`changes-vs-259.diff` は今回だけの差分。
- `preservation.json` と `source-manifest.json` は259の全manifest不変、および全src/toolsの同一・変更対応。L12/D15の製品/fixture、services/shell/JobService/receipt、S21/L13/X04は無編集。
- `WRITE_END.txt` は最終書込みの区切り。Git commit/push/main統合、旧成果の削除、他所有プロセス操作はしていない。

## 原操作との対応

原版はすべて `S:/Projects/PixivVault` の ref `3ac3aa0d47a64785251593bcb09bb2d5f6df374b` をread-onlyで参照。

| 原操作/根拠 | 259の現物 | 今回のコード |
|---|---|---|
| 作品詳細の説明・タグ・投稿日/ブクマ/閲覧/解像度、初回明示取得。`ARTBOOK_VIEWER_INFO_ACTIONS_SPEC_2026-08-27.md:51–77` | 説明ラベルのみ、詳細GET未接続 | 既存詳細GETへ本人操作で接続、work/authorの一致確認、plain text表示。欠けた値は不明、0は0。キャッシュ、失敗時の再開操作、別作品/閉鎖/離脱後の不反映 |
| 詳細は画像を縮めない。詳細再クリック/Esc/画像クリック/ページ送りで閉じる（同仕様） | 詳細の絶対配置と操作は実装済み | 同じstage内の重ねパネルへタグ/数値を追加。画像・既存の開閉/キー処理を維持 |
| Pixiv作品ページ入口。原画集仕様§2-1、情報操作仕様§6 | 共通viewerに入口なし | ヘッダーの作品メニューに検証済みIDだけの固定originリンク。自動オープン/自動GETなし |
| 作者一覧overlay・初回50/追加50・作者cache。`AUTHOR_PAGE_SPEC_2026-08-19.md`§2–4、`recommend_tab.py:3426–3435,3622–3627,3678–3703` | 共通viewerから未接続 | 作者名ボタン→同じタブ内overlay。既存get_user_works(interactive)と画像pinを再利用。追加時既存カード維持、戻る時元一覧維持、失敗再試行、遅着拒否、欠損previewカード保持 |

正規のGETクライアントと画像所有APIを呼ぶ製品コードを実装したが、実API/実Cookieでの適合・速度は未確認。authorの `lane='interactive'` は既存クライアントに渡す（そのクライアント内部のスケジューリング変更はしていない）。

原152操作台帳の正確なV/A行番号を持つ正本は今回checkoutに見当たらない。`docs/BASELINE.md` のV05/V07/V08/V09は旧13項目の別表であり、152行と同一と決めつけない。上記の原操作名/根拠と親側の台帳を照合すること。採点分母/分子を追加していない。

## 変更ファイル

- `viewer_metadata.py`: injected readerによる詳細/作者DTO検証。HTTPクライアント、Cookie、DB、画像を新規生成しない。HTMLは実行せずtext化。外部URL入力をナビゲーションへ転用しない。
- `ui/artwork_viewer.py`: 詳細のタグ/数値/状態表示、作者名ボタン、Pixivメニュー。固定ダークなど新CSSを追加せず既存テーマを使用。
- `ui/viewer_controller.py`: 明示した詳細取得、作品/世代/画面寿命ガード、作者overlayへの接続/離脱cleanup。
- `ui/author_browse_view.py`: GalleryView/既存画像pinで作者一覧を表示。50件追加、同作者の取得再利用、表示できないpreviewを脱落させない。catalog構築/描画失敗時未採用pinを解放。
- `tests/test_p3_viewer_author.py`: 新規9関数。正しいID/元順序、数値0と不明、実詳細UIの遅着とretry、実作者click handler/離脱/戻り、50→51時DOM保持、missing preview、描画失敗時pin解放、未知ページ情報の遅着が後から選んだ別作品を開かないこと。
- `tests/test_artwork_viewer.py`: 明示Client/slotと削除cleanup、旧stage不縮小構造assertを新しいパネル階層に適合。既存assertを削除していない。
- `tools/serve_viewer_author_fixture.py`: CoS用実UI合成入口。既存DemoImageCatalog/LibraryImageRegistry/RemoteBookmarkImagesを使用し、全メタデータ/画像は合成。実Cookie/HTTPクライアント/実保存rootを作らない。

## CoS 一括検証（未実行）

1. 最初にmanifest/hash、branch/HEAD、WRITE_ENDと単一writerを照合。作業cwdをこの候補に限定し、既存のS側venvを使用。依存install不要。
2. `PYTHONDONTWRITEBYTECODE=1`, `PYTHONUTF8=1`, `PYTHONPATH=<candidate>/src`。pytestは既存設定で下記を一括実行。cache/basetemp/XML/logはこの候補の新規cos-owned領域を指定。以前のPASSを新候補へ自動継承しない。

```powershell
# CoS専用。実装担当はこのコマンドを実行していない。
& S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_p3_viewer_author.py tests/test_artwork_viewer.py tests/test_gallery_pages.py tests/test_ranking_ui.py tests/test_viewer_save.py tests/test_state.py --basetemp=artifacts/p3-viewer-author-20261028/cos-owned-r1/tmp --junitxml=artifacts/p3-viewer-author-20261028/cos-owned-r1/viewer-author.xml
```

追加影響: LibraryViewは製品ファイル同一だが、共通controllerを呼ぶため `tests/test_library_view.py` と既存Library GUIの閲覧操作を限定再確認。L12/D15全再実行を本差分のみで要求しない（前候補の受入は別担当の結果を保持）。

3. GUIはCoSが空きloopback portを予約し、実サービス/8089/他所有PIDと競合しない専用プロセスだけで起動。stdout/stderrとPIDを記録。

```powershell
# $env:PYTHONPATH はこの候補のsrc。52379は例であり空き保証ではない。
& S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -B tools/serve_viewer_author_fixture.py --port 52379
```

4. Edge 1280×720/1920×1080、dark/lightで以下を実ボタン/キーで操作。force click/JS dispatch/APIによるUI操作の代用はしない。外部requestは阻止して記録し、Pixivはリンク先/target/relだけ検査（実ページへ移動しない）。
   - 初期2作品、`/_fixture/state`のcalls空。501カード→画像decode後viewer、1/2→2/2のページ移動。詳細を開くまでdetails呼出0。
   - 詳細ボタン→合成説明501、合成タグ、`<text-only>`がテキスト、投稿日2026-10-28、ブクマ0/閲覧12/800×600。画像bounding boxが詳細開閉で縮まらず、情報行48px維持、ラベル/ボタンがクリップ/重複しない。取得後詳細の再開でdetails同workを再取得しない。
   - Esc一段目/画像クリック/ページ送りで詳細だけ閉じ、Esc二段目で一覧へ。別カード601へ移った時501の説明が残らない。
   - 作品メニュー→`viewer-pixiv-link`が501の固定Pixiv URL、別workではそのID、外部通信なし。
   - 501の作者名→`author-browse-overlay`、作者91、50/51表示。スクロールして「もっと見る」で51/51、最初の50カードDOM IDと元順序不変、ボタン非表示。作者内カード→2ページviewer→閉じる→作者一覧→作者close→元2作品一覧/query/size/DOMが維持。
   - 同作者再訪で`/_fixture/state`のauthor91呼出1のまま。初回未訪問92へ行く前に「合成作者取得失敗」をON→601作者でfailure+再取得。closeしOFFにして再訪→50件正常（失敗を0件成功/永続cacheにしない）。
   - 新ブラウザclientで「合成詳細取得失敗」ON→501詳細failure。閉じてOFF→同作品詳細再開で成功。失敗時unknownを偽0にしない。
   - viewport/theme/各段階のスクリーンショット、DOM、console/page errors、callsの初期/最終snapshotを保存。単なるHTTP200やfixture内部API成功をGUI合格にしない。
5. 所有サーバー通常終了、PID/port残存0、候補/固定259hash再照合。ブラウザの再訪cacheはセッション内、プロセス再起動後は原どおり再取得可能（永続author cacheの新設なし）。

## 未達・境界

- 原寸切替・ズーム/パン: 現候補に未接続。固定原版画集設計§2-2は原寸を明示要求時のみ、§5-5末尾は「ズーム・パンは入れない前提」。固定原版実装にも対応controlを確認できていない。原152台帳の採用根拠と対応する後続仕様を照合するまで、倍率/保存品質を新造しない。
- 表示ページだけDL: 原画集仕様§2-4は1ページ単位。現在のViewerSave→save_explicit_workは作品全体の既存managed receipt経路。今回その受付/receiptを改変しない。ページ保存を実装済みと扱わず、部分成果物を全作品完了と誤記しない別途の接続が必要。
- 元仕様の保存状態表示、bookmark/follow/作者差分DL、右クリック全項目、未保存recommend作品のviewer導線などPV-B以外も含む全操作をこの候補で完了とはしていない。保存状態をAPIの数値から推測しない。元GETクライアントが拒否するmasked応答を安全条件緩和で通さない。
- 元の原型にある細部（取得中スケルトン等）の全UI一致、実体の可読性・画像サイズ/クリッピング、性能、live GET、native、exact-head CI、独立レビューは未実施。CoS/Claudeの結果で判定する。
- 259以下のL12 cache/lease/LibraryViewおよびD15 learningの製品・fixtureは保全し、今回のsrc変更4ファイル以外を触らない。

## 作業中に共有された259のL12実測未達（本候補では不変更）

親から共有された `P3_259_L12_D15_GUI_LIMITED_ACCEPTANCE.json` の実SHA
`30cd4b2dce7d7484fc8256b440f4137b9babdf48c79b85740455581193327c05` を読取り照合した。
D15の合成GUI成功/再起動復元と、L12の再起動自動復元失敗を分離する。
L12は永続cacheの91=1024B/92=3072B・revision1が残る一方、初回画面は636B/212B、明示更新で復帰した原票。

静的に成立する呼出し順: `ui/library_view.py:195`の初期reloadが613–614でcache復元taskを登録。
`ui/media_views.py:29–43`はそのLibraryViewを生成した後 `set_active(False)`、51–56で子へ通知。
`LibraryView.set_active:915–938`はepochを失効させるが再activeで復元taskを登録しない。
`_restore_legacy_capacities:341–351`が旧epochの結果を拒否するため、初期cache復元を失う経路と整合する。
実機再現の原因を最終確定したものではない。本PV-B候補にはLibraryView/cache/lease/MediaViewの編集を混ぜていない。
次のL12修正は「非active化で棄却した初期復元を、正しい再active epochで再要求する」ライフサイクルとし、
再起動初回・二重active・切替中遅着・close・計測中競合をCoS回帰へ含める必要がある。
取消/CASの条件緩和やSQLite値の変更で表示問題を隠さない。台帳の算定は変更しない。
