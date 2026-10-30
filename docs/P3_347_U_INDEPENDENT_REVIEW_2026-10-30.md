# P3-347 U対象選択／作者個別設定の独立静的レビュー

**判定：REQUEST_CHANGES（U10を主対象とする P2 2件）。U01/U03/U04/U05/U06の通常UI結線と、U11の設定→ZIP後処理は限定的に確認。**  
2026-10-30 JST／worker-11。worker12のU実装には関与していない独立担当として、現ソース、原機能索引、既存合成テストと保存XMLを読取評価した。製品・tests・checker・harness・Xは変更していない。GUI／pytest／ネット／Claude／Git／原DB/media/Cookieの操作・読取は今回一切ない。この新規docのみ追加。

## 原採用範囲と現在のSHA

`docs/P3_343_REMAINING_85_FUNCTIONAL_EVIDENCE_2026-10-30.md:105-123,151-235,265-317` のU01/U03/U04/U05/U06/U10/U11、および `docs/P3_345_U_TARGET_SELECTION_2026-10-30.md:1-19` を採用。旧台帳の各 C は変更前実装を指し、現sourceの新しい機能と区分する。U10/U11の `expected_full` は旧v8でNULLのため、新たな逐語要求を追加しない。X物理移動の合否や正式152全体の合格数は変更しない。

| read-only `Get-FileHash -Algorithm SHA256` 対象 | SHA256 |
|---|---|
| `src/pixivvault_nicegui/author_download.py` | `8f6fce31f2782d3c97ce20cd999902933421c2ca580386cd6a1f2285d9be705c` |
| `src/pixivvault_nicegui/author_target_selection.py` | `6d4bb19d8e72c3e3199b9a4ae22494a266916e4f816210195d16933fc7e89870` |
| `src/pixivvault_nicegui/ui/managed_download_view.py` | `2451eb9c9e4dcc3d7be6b61fd42dff588e55dc6b977ec14bf5ae441ca2656d29` |
| `src/pixivvault_nicegui/ui/author_media_view.py` | `b75db5951f8721d22aa837bced186c15954c8effccf1772e197720ee4d47cd95` |
| `src/pixivvault_nicegui/services.py`（prime所有） | `01276afed78ee155f2ee2c8dc47c5b2196242abb3f7606180ce04f442a473f92` |
| `src/pixivvault_nicegui/author_media.py`（既存owner） | `0b5096b8605aa7d7e65269236a89dfa3daf512e69711dc079952bed724139746` |
| `src/pixivvault_nicegui/save_exports.py`（既存owner） | `b98fe56fbf6681aa079e63b0a1a4470d066866c149e8e2a16635bd9bf8f686bd` |
| `tests/test_author_target_selection.py` | `1dc9b7db0271ca1e74b9eb6eb9430b0dea9ce29830486677b33b04af925bab07` |
| `tests/test_followed_target_metadata.py` | `9628d9a156f3da6ceff8ce1f1cda7690984b9127f771597d337bf05ee27be3aa` |

## 正常経路の配線（限定的に肯定）

1. **U01 モード切替**：`managed_download_view.py:75-88,108-165,170-196,219-246` にマイブックマーク／フォロー作者の選択、`data-target-mode` と別パネルが存在。mode変更は親`epoch`を増やして古い親ブラウザイベントを無効化し、双方の選択状態を保持する。`tests/test_author_target_selection.py:46-88` は合成NiceGUIで切替後の可視性・選択保持を検査。
2. **U03 ソート／U04 ☆絞込**：`author_target_selection.py:16-35` はフォロー順・名前順・ID順、昇降、検索、`is_favorite is True`のみを対象にソート／絞り込む。`managed_download_view.py:144-152,590-626,665-725` は実UIコントロールへ接続。`author_download.py:17-36` は `None` の☆と未設定順序を保持し、`services.py:1570-1616` は同期済みのfavorite/orderを継承、未同期GETではローカル確定favorite集合が無い場合は `None` のまま。reader入替・アカウント切替は`checkpoint`で棄却する。unknownが1件でもあると ☆only／☆一括を抑止する保守的仕様（`managed_download_view.py:613-625,648-652`）。`tests/test_followed_target_metadata.py:47-92`に同契約の合成assert。
3. **U05 全選択・解除／U06 ☆一括**：`managed_download_view.py:153-163,628-663` はfollow絞込結果の**全件**を `select_many_authors`に渡す。画面48件窓（`689-730`）と選択集合を分離。`author_target_selection.py:38-52` はuser_idで重複排除、先行`origin`維持、2万件上限を途中成功なしで拒否。☆一括は `True` だけを採用。`tests/test_author_target_selection.py:17-38` は421作者を選択、`46-114` はモード・sort・☆未知の安全拒否を確認。ブックマーク全選択は `managed_download_view.py:269-301` で**現在表示窓**、別途128件の受付限界を維持する。
4. **U10 作者別媒体の実接続**：`author_media_view.py:28-58,86-165,173-256` は設定読込→ `configure_author_target`（既存CAS）→作者別`target_type=None`で `services.author_media_preview`を全選択作者に実行→`enqueue_author_media(candidate,operation,origin)`へ明示submit。 `services.py:1148-1162` は `AuthorMediaService.resolve_author_target`で作者ごとにtargetを決めて `AuthorMediaService.preview/enqueue`を呼ぶ。`author_media.py:214-314,347-402` は静止画・うごイラ・小説の差分／source更新／既存job ownerへ結線し、`404-411,454-535` は受理IDと現receiptの証拠をreconcile。プレビュー70件で50件だけ画面描画、**選択70件は維持**、queue50で20件未受付を保持し、応答喪失時は同じoperation IDで再試行する（`author_media_view.py:199-205,224-256`、`tests/test_author_target_selection.py:181-286`）。
5. **U11 作者個別ZIPの実接続**：`author_media_view.py:45-50,267-290` が作者別ZIPの読込／CAS保存を呼び、同じ作者設定revisionを共有する `author_media.py:128-183` は `BEGIN IMMEDIATE` で競合を拒否。`save_exports.py:506-526` は保存完了が証明された静止画receiptsと作者別 `author_zip_setting` を参照し、`on/off/inherit` を全体設定より優先して `auto-zip` ジョブへ接続。単なる設定保存だけの孤立UIではない。個別ZIP設定の変更時に同clientの旧差分表示を失効させる `_changed()` は `author_media_view.py:281-285` にある。ただし下記の競合と同時操作条件が残る。

## P2-1：別clientの作者媒体設定変更後、旧previewの媒体が受付可能（U10）

**静的成立経路**：  
(1) client Aが作者91の`target='author'`でpreview（設定`both`）し、静止画・小説を選択する。`author_media_view.py:61-63,135-155` の選択スナップショットは親author_revision・親author_targets・UI target/formatだけで、**作者設定のDB revisionを含まない**。  
(2) client Bが同じ作者を`novel`へ変更し、`author_media.py:143-159` のCASが成功。Aの画面は設定変更を受信せず、`selection_snapshot == _snapshot()`のまま。  
(3) Aが元previewに残った静止画をsubmit。`author_media_view.py:224-247` はAのローカルsnapshotを確認してenqueueへ到達。`author_media.py:76-82` のcandidate admissionにも作者設定のrevision/targetは含まれず、`author_media.py:347-402` は作品のrevision・receipt・operation replayを確認するが、現在の作者設定が`novel`に変わったことを照合しない。作品自身の変更がなければ、**新設定で除外した媒体の古い候補をキューへ受付できる**。

**影響**：U10「作者ごとに設定した保存媒体が次の対象確認／受付へ作用する」境界に不一致。二client CASは設定書込み同士の競合を守るが、設定変更後の旧preview受付までは守らない。既存`test_author_target_selection.py:117-178`は同clientで設定→previewの成功を検査し、`test_author_media.py:105-125`のstale拒否は作品update_date／元proofの変化であり、このDB target revision変化を試験していない。**実GUI／実APIで再現済みという主張ではなく、現コードの状態遷移によるP2指摘。**

**限定修正条件**：通常`target='author'`のpreview時に作者ごとの `AuthorTargetSetting.revision` と `target_type` を固定し、enqueue直前（ownerの排他／CAS境界で）に同じ作者設定revisionを照合する。異なれば受付前に明示再previewを要求する。明示的な`target='both'/'illust'/'novel'`という今回限りの全作者共通指定は本来の明示overrideとして維持する。古い既存jobの正当なidempotent replay/restoreを新仕様で破壊しない。今回はコード修正・新テスト実行をしない。

## P2-2：ブックマークモード切替後も、隠れた作者媒体の古いsubmitが受付可能（U01/U10）

**静的成立経路**：  
(1) 作者モードで`AuthorMediaView.preview()`後、保存対象が選択され、`selection_snapshot`が一致している。  
(2) `managed_download_view.py:223-246` の `set_target_mode('bookmark')` は親`epoch`を増やし `author_media_slot.set_visibility(False)` を呼ぶが、子 `AuthorMediaView.set_active(False)`、`generation`更新、旧submitの取消はしない。  
(3) 子 `author_media_view.py:65-66` の`_valid()`は `parent._valid()` のみで、`parent.target_mode`や親`epoch`を照合しない。`224-244`のsubmitには`_snapshot()`しかなく、`_snapshot():61-63`に親mode/epochが無い。そのため表示切替前のボタンイベントが遅れて届くか、hidden中の旧client handlerが実行されると、新しい作者媒体ジョブが受付可能。 `set_active(False)`でタブ自体を非表示にした場合は正常に拒否するが、**同一タブ内のU01モード切替**だけでは拒否しない。

**影響**：親UIが古いブラウザ操作をepochで拒否する設計なのに、作者媒体の有効状態が別扱いのためモード切替後の予期しない受付が成立する。下位`author_media.py`は正当な明示submitだと受け取る。既存 `test_author_target_selection.py:46-88` はパネル表示/選択保持を検査するが、切替前の遅延submit拒否は未確認。これは**実ブラウザでの発生頻度が未計測の静的P2**。

**限定修正条件**：author媒体のpreview/設定/submitが呼ばれる時点で親が `target_mode=='author'` であることと、発火元の親`epoch`／render generation が一致することをサーバー側で検証する。親mode切替で選択を保持する現UXを維持し、再表示時には最新状態で明示submitできるようにする。既に受付済みの operation は close/切替で二重投入せず処理完了を待つ（`_accepted_call` を壊さない）。

## 保存試験の範囲と未確認

`artifacts/p3-345-u-worker12-fullcpu-r1/results.xml` SHA256 `75f8b408cf05b2d165ca7a3cbd6d053274361c6e1f36abf720f2cf4e1fb10191`：**76 TEST / 0 FAIL / 0 ERROR / 0 SKIP**、9 test class（新`test_author_target_selection`10、既存`test_author_media_view`6、`test_author_download_view`9、`test_managed_download_view`7、`test_author_media`16、`test_author_media_pins`3、`test_author_output_connection`5、`test_followed_target_metadata`7、`test_save_exports`13）。`artifacts/p3-345-u-worker12-targets-r3/results.xml`は新U UI・既存AuthorMediaViewの **16件**の保存成功。primeが別途報告したmetadata 50PASSは当該result.xml path未提示のため、この独立workerは原票の総数・SHAを再照合していない。70media途中queuecap/同operation応答喪失/close後新規受付拒否は`tests/test_author_target_selection.py:181-286`の保存成功の範囲。

既存 `tests/test_author_target_selection.py:117-178` の媒体＆ZIPテストは合成`SimpleNamespace`でサービス境界を代用しており、実owner・保存ZIPの完全なend-to-end実画面証拠ではない。一方、`tests/test_author_media.py:59-142` と `tests/test_save_exports.py:26-110` は実owned合成owner・receipt/ZIPの既存局所回帰を示す。worker12のGUIcheckerは `docs/P3_345_U_TARGET_SELECTION_2026-10-30.md:31-48` に**準備・AST検査のみ**と記録されており、本レビューでGUI/Edge/natively実行した証拠ではない。正常終了/receipt強化中との指示を受けたが、その完了は未確認。

**最終判定：U01/U03/U04/U05/U06/U10/U11をまとめたゲートは REQUEST_CHANGES（P2-1/P2-2）。** 通常のmode/sort/☆/全選択/媒体preview→owner enqueue/作者ZIP→save_exports結線については上記の限定的な実装成果を認める。修正後は新旧クライアントの設定revisionと親modeのstaleイベントを固定した合成対照で再レビューすること。**正式152全体の完了・Xの自己レビュー承認・実GUI/natives/perfをこの判定から推定しない。**

