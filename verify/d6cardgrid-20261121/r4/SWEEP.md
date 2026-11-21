# D6 第4回 修正前の棚卸し

基点 `9d5afcc8a1324162b27d3c8a781b3690da32f3a1`。Neon の `S:/Projects/PV-DEV-20261117/tmp/rx_cardgrid3.md` と製品の CardGrid 所有者・呼出し元を照合した。

| 利用面 | (a) 非同期の文脈・一覧更新の所有と競合 | (b) 一覧 record と別の詳細メタデータ |
| --- | --- | --- |
| 世間ランキング | `_schedule_first_load/_first_load` は初回のみ。`changed/_load_changed_conditions` は明示条件を保留し最新版を取得。`_sync_local_revision/_refresh_local_preferred` は revision の前後不一致を残し、タイマーが読み直す。`_schedule_hidden_refresh/refresh_hidden` は再表示ごとの永続集合読取りで busy による再要求破棄はない。`load` は世代と取得中の操作を照合する。 | `open_unknown` が画像 pin に詳細を確定 → GalleryState のみを差し替える。一覧 record は未知のまま。再描画で比較に失敗し、確定ページを巻き戻す。 |
| 自分のランキング | `_sync_local_context`（1秒）、`local_tags_changed`、`_after_hidden_change`、タブ再表示、`load` 前読取りが同じ文脈を要求。実行中の早期 return で変更通知を落とす。改訂番号を伴わない非表示更新もある。`server_changed/_condition_pending` は取得条件を別に保留済み。 | 継承した `RankingView.open_unknown` と GalleryState。文脈更新の `render` が未解決レコードを再投影するため世間ランキングと同型。 |
| ギャラリー | 検索と末尾追加は同期。ビューアの async 詳細・GIF・作者遷移は選択/要求番号を照合し、一覧投影と別。 | デモ一覧は確定済み。ViewController の詳細表示キャッシュは選択に表示する追加情報で、一覧のページ数や artwork を置換しない。 |
| 作者ページ（おすすめの作者オーバーレイを含む） | `_records` は作者キー単位で進行中の取得を共有。同じ作者の再表示の各呼出しは結果を await。明示再試行は旧キーを切り離し、表示 epoch と pin を確認。`hide/close` は別ライフサイクル。 | 専用 `open_unknown` が GalleryState のみを差替。同じキャッシュの再表示・検索・末尾追加は状態を再利用。全件を再取得すると新 pin。共通の保持処理を使い、別 pin に詳細を移さない。 |
| おすすめ棚 | 棚は独自カード。タブ再表示の `_refresh_pending` は既存。`_viewer_hidden` は保存成功通知なのに busy 時に破棄するため、同型を追加で確認。保留に `preserve_viewer` の意図も持たせる。 | `open_work` が選択用の画像 pin と `selection.artwork` に確定結果を保つ。棚は独自 state、選択の寿命・世代を照合し、棚レコードで開いた選択を上書きしない。 |
| ブックマーク | `BookmarkView.request_refresh` は親へ委譲。`LibraryView._refresh_bookmarks_async` は重複 GET を抑止し、完了・復旧・再表示でサービスのスナップショットを採用。投影は `_bookmark_projection_pending` でビューアを閉じた後に適用。独立した文脈要求の破棄はない。 | `_open_unknown_bookmark` は `_resolved_bookmark_artwork` に保存。`_build_viewer_state` が同 pin の解決結果を優先し、投影済みライブラリや pin の交換・明示更新で廃棄。 |
| ライブラリ作者・ページ | Pixiv 検索は各条件に epoch を発行し旧結果を拒否。`_legacy_restore_request` は同じ対象と generation のみ共有し、測定完了時に現対象の未採取分を再評価。`reload(after_viewer=True)` は同一ソースを維持し異なるソースを保留する。 | `LibraryWork` のページ数を `LibraryState` に投影。リモート未知数は既存の「1枚だけ表示」契約。ローカル詳細の独立キャッシュは上記ブックマーク経路。背景の差替はビューア終了に合わせる。 |

| (c) 画像条件 | 修正前の分岐と追跡 |
| --- | --- |
| 共通 `CardThumbnail` | `loading` は `eager`、`fetchpriority=high` は `first`、失敗時だけ `retry_epoch` による再取得。優先判定はこのテンプレートと親 CardGrid の `watch.cards` に限る。 |
| 標準ギャラリー（両ランキング・作者・おすすめ作者） | 先頭3行 eager、index 0 high。空→一覧では昇順挿入。旧先頭と同じ時および部分重複時は defer を行わない。完全非重複のみ兄弟の src を1 tick遅らせる。実際の旧 src を持つカードだけを再利用対象とし、先頭の画像が新しい時に新規兄弟へ限定して遅らせる。 |
| ブックマーク、ライブラリ作者/ページ | `layout` 分岐で標準用 defer と `first` の対象外。各レイアウトの先頭3行 eager は同じ。ランキング切替の優先順位変更は標準用の分岐に限る。 |
| おすすめの独自棚 | CardGrid 外。標準ギャラリーである作者オーバーレイだけ上記の優先条件を共有。 |

実行中の変更通知を1回分の保留にまとめ、文脈の読取りを直列化する。ページ数は現在の画像 pin が確定した詳細を投影へ反映し、検索や再描画で同一の詳細を保持する。先頭優先のテストは部分重複の再現と既存画像の再利用を同時に確認する。

## 修正後の照合

- 自分のランキングは NiceGUI が追跡する1タスクで文脈を直列に読む。保存・明示通知を保留にまとめ、途中で通知された読み取り結果を表示へ出す前に再読込する。改訂番号の前後照合と表示ライフサイクルを維持。通常の1秒ポーリングは進行中の改訂照合を共有する。検索処理は同じタスクの確定結果を待つ。
- おすすめは保存済み表示の読取りを保留し、非表示通知の再読込で詳細ビューアを保持する。タブ再表示の再読込も既存経路で受ける。読み込みの追加は永続データの再読取りに限る。
- 両ランキングと作者の詳細解決は GalleryView に集約。現在の画像プロバイダーで確定したページ数を投影に重ねる。pin が変わると解決だけを失効し、新 pin の一覧情報で描画する。
- 先頭の画像が新しいとき、前回の同一キー・同一 src を持たない兄弟だけの src を次の Vue 更新に送る。残存画像は DOM と src を維持する。完全非重複でも部分重複でも同じ判定を使う。

呼出し一覧は基点と製品コミットの Git オブジェクトから抽出した。 [修正前](calls-before.txt) [修正後](calls-after.txt)
