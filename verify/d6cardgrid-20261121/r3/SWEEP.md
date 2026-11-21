# D6 第3回 修正前の全経路棚卸し

基点 `172e7d727ad2dbd98abe77b5bc9747ee8fcdc57b`。Neon の [第2回再レビュー](S:/Projects/PV-DEV-20261117/tmp/rx_cardgrid2.md) を入力として、実装の発信元まで確認した。第2回 SWEEP の「自分のランキングにタグタイマーなし」は誤り。

| ビュー | 世代更新・閉鎖に到達する全経路 | 同一内容での修正前の挙動と境界 |
| --- | --- | --- |
| 共通 GalleryView | 初期描画、`search → set_query → close_viewer/render_gallery`、`more → render_gallery`、`set_viewer_active`、ViewerController の Esc・閉じる・次の作品を開く・終了 | 初期・検索は無条件世代更新。末尾追加だけ既存カードの先頭一致を照合。活性、選択した作品の変更、閉じる操作と破棄はライフサイクル境界。 |
| 世間ランキング | `load`（初回・手動・モード変更・more）、ソート、`RankingCardMenus.refresh_hidden`（再表示・除外操作）、`_sync_local_revision → refresh_hearts → _refresh_local_preferred → refresh_hearts`、`set_active/close`、既知/未知作品の `open` | 1秒監視はハートだけ。通常 `render` は同じ一覧でもビューアを閉じ世代更新、append でもビューア閉鎖。除外した現在作品だけ既存のビューア保持。取得条件 epoch と一覧 epoch は別。 |
| 自分のランキング | `load`、`_sync_local_context`（1秒タイマー・明示タグ変更）、`_start_context_refresh → _refresh_context`（再表示・非表示管理・取得前）、条件・フィルタ変更、`_show_unfetched`、継承 `set_active/close/open` | 同じ監視周期で同期と非同期完了が同じ結果でも最大2回閉鎖と世代更新。ベース文脈と確定文脈の違いで本当に集合が変わる場合は更新対象。 |
| 作者ページ | `show` の新作者・試行開始/完了・同じキャッシュを再表示、`retry_load`、`hide/close`、共通検索/more、`open_unknown` の詳細解決 | 作者・取得試行・画面表示ライフサイクルを author epoch で照合。同じキャッシュ再表示は DOM 維持、同じ可視作者を再び show する場合は不要な author epoch 更新。未知詳細の結果は author epoch・request・pin で照合。 |
| おすすめ | 本体 `render`（保存済み反映・手動・more/fold）と `open_work`、`_viewer_closed`、`set_active/close`。CardGrid は作者オーバーレイのみ、`_show_author_page` と `_author_card_menu`（作者 epoch・一覧 epoch・reader を照合） | 本体は独自カード。背景 read-only 反映は既存 `preserve_viewer` の経路。作者オーバーレイは作者ページと同じ共通描画を通り、本体描画からは世代更新しない。reader 交換は別コンテキスト。 |
| ブックマーク | `refresh`（表示/非表示、取得状態、キャッシュ採用、自動・手動更新、選択、more、バッチ状態、フィルタ、再読込）、ライブラリの `_bookmark_open_epoch`（タブ・条件・実際の open・画像スナップショット切替） | 旧 `retired` は DTO と ID のみ。操作可否・画像 URL・メニューの変化を見ない一方、表示テキスト/状態や reset_controls で不要失効し得る。自動採用の `_refresh_bookmark_projection` と reload が reset_controls を伝播。 |
| ライブラリ作者 | `render_authors`（手動/自動容量、検索/ソート、more、reload）、`apply_filters`、`reload`（初回・Pixiv背景検索完了・ダウンロード/保存済み反映・ブックマーク投影・DB更新・検索範囲変更）、`_sync_card_grids_active`、詳細への移動 | 容量と末尾追加の既存操作は先頭・代表作品・URL・profile・menu 比較で維持。reload は内容を照合する前に世代とセッションを廃棄。一覧・画像解決の同一性を先に比べる必要あり。 |
| ライブラリページ | `render_pages`（open_author・more・reload）、`_clear_pages`（検索、ソート、絞込、作者移動/戻る、reload）、`_sync_card_grids_active`、`close_viewer`（保留中の投影・reload）、`_retire_remote_images`（権限消失） | `_clear_pages` は空でも世代と more セッションを更新。リスト先頭が同じ場合の再描画は既存カードの操作属性を再検証しない。権限消失は同一 ID でも操作対象が失効し、該当するリモートビューアは閉鎖が必要。 |
| Vue 共有メニュー | `contextMenu` で開閉連番と epoch を採番、`render_epoch/grid_active` watch で廃棄、`menuAction/hideMenu/onMenuHide` で照合・復帰 | 遅延 hide は連番で拒否済み。無効カードへの連続右クリックでは旧メニュー配下の focus を opener として捕捉する。安定した元の opener を継承する。 |

描画の生の世代更新は CardGrid へ集約し、順序付きの既存キーと各レイアウトの操作属性の一致を共通境界で比較する。完全一致・既存部分が等しい末尾追加・容量やハート等の表示のみの変更では操作世代を進めない。ビューアを所有する GalleryState は同一作品で再構築しない。タブの活性変化、実際のナビゲーション、reader・画像寿命の変更は別途照合する。

## 元 Flet の再試行

世間は `ranking_tab.py:1275` から `_ensure_loaded(force=True)`。同じ Work ID は `_build_card` のキャッシュヒット（798–805行）で返り、失敗でアイコンのままでも再投入しない。自分は `_fetch_personal` の置換（1723–1725行）でカードキャッシュを消し、再描画時に `_build_personal_card` 相当（1533–1547行）でサムネイルを再投入する。したがって世間の手動同一更新は現行のまま、自分の明示取得が成功した場合は失敗サムネイルのみ再試行する。元が再取得する成功済み画像は、前回の要件に従って既存 URL と描画を再利用する。

## 修正後の照合

- `render_epoch` の加算は `CardGrid._retire_actions` の1箇所。描画は `publish`、活性変更は `set_active` を通す。既存順序、旧カードの操作属性、明示的なライブラリ検索条件を比較する。末尾追加で旧カードを維持し、削除・入替・ソース/許可の変更だけ既存操作を失効させる。
- 共通ギャラリーの作品差替は作品列と画像プロバイダーが等しいときに GalleryState とビューアを保持。検索結果が等しいときも同じ状態を使用。世間ランキングのハート更新は表示のみ。自分のランキングの1秒監視は読込みを予約し、同じ revision で完了した文脈から一度描画する。
- ライブラリの保全再読込みはスナップショット、ローカル画像 pin の token、検索範囲・結果が等しければ切替を行わない。容量ラベル、作者・ページの末尾追加は操作属性で比較。新しい pin は操作ソース変更として扱う。明示フィルタ、作者移動、非アクティブ化、権限消失では現在の操作対象を照合し、必要な失効を維持。
- ブックマーク投影の自動採用は無条件 reset を伝播させず、`_make_card` の画像 URL、無効、作者、保存状態、メニューを比較。チェックの変更は世代を維持。作者オーバーレイへの同じ可視作者の再表示は author epoch を維持。
- 無効カードへの連続右クリックは旧メニューの元の opener を継承する。開閉連番と render epoch の照合は維持。 [検証結果](RESULTS.md)

世代・閉鎖・監視の呼出し位置は基点と最終候補の Git オブジェクトから抽出した。 [修正前](calls-before.txt) [修正後](calls-after.txt)
