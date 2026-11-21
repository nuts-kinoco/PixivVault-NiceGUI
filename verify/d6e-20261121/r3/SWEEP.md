# D6 第3回 修正前の網羅確認

対象 exp-d6-vue / 1e9abfb。A の件数は実 NiceGUI 3.18.0 Client・Outbox で調べる。通常更新はビューアの ArtworkPage 以下の ID のみを数え、FIFO の update と重なる ID と run_javascript の件数を記録する。シェル起動時の通常更新は HTML に埋め込まれ、その後クリアされる。遅延生成は標準 outbox から送られる。

| A 生成・利用経路 | 通常の生成時点 | 1e9abfb の配線順 |
|---|---|---|
| shell → GalleryView | HTML 前。ランキング用サービスのない構成 | ArtworkPage → 子の初期 props/on → bind → controller の props/on |
| shell → RankingView → _RankingGallery → GalleryView | HTML 前。ランキング構成 | 同上。ランキングのフォーカス操作はシェルが起動タブで制御 |
| shell → LibraryView | 既定のダウンロードからの遷移で HTML 後。起動先がライブラリなら HTML 前 | 同上。ライブラリ専用 test ID を子作成時に指定 |
| shell → MediaLibraryView → LibraryView | 同上。複数媒体でイラストの保存担当があるとき | 同上。外側が初期非アクティブ化。小説・うごイラ専用ビューは ArtworkPage を持たない |
| shell → RecommendView → RecommendationViewer | 既定起動からの遷移で HTML 後。起動先指定で HTML 前 | 共通配線 → おすすめ test ID の props → 非アクティブ化 |
| shell → PersonalRankingView → _RankingGallery → GalleryView | 既定起動からの遷移で HTML 後。起動先指定で HTML 前 | GalleryView と同じ |
| ViewerController._open_author_art → AuthorBrowseView → _Gallery → GalleryView | 最初の作者クリックで HTML 後 | GalleryView と同じ。作成直後の show は非アクティブ化してから作者取得 |
| RecommendView の作者アクション → AuthorBrowseView → _Gallery → GalleryView | 最初の作者クリックで HTML 後 | 前行と同じ |
| shell._build_view_history → ViewHistoryView → RecommendationViewer | ストレージ付きで HTML 前。履歴ダイアログのクリックでは既存インスタンスを使用 | おすすめと共通 → 履歴 root の test ID の props |
| shell → LearningView → ViewHistoryView → RecommendationViewer | 既定起動からの遷移で HTML 後。起動先指定で HTML 前 | 履歴と同じ。学習からの画像オープンも同じ履歴ビューアを使用 |

ArtworkPage の製品上の直接生成箇所は ArtworkViewer.__init__ の1箇所。ArtworkViewer は ViewerController._init_viewer の1箇所。_init_viewer 呼出しは GalleryView、LibraryView、RecommendationViewer の3箇所。付随する Focus/Zoom の JS はビューアごとの DOM ノードに付く。

| B 状態・リソース | 1e9abfb の所有者・束縛 | 解放・失効の確認項目 |
|---|---|---|
| _viewer_gifs | 唯一追加された Python の可変プロセスグローバル。弱参照辞書、ViewerGif を PageImage が保持 | close／切替／再試行／要素削除で参照解放。ブラウザーの表示失敗、app 間、サービス閉鎖に穴 |
| _displayed_gif／PageImage._viewer_gif／アニメ変換タスク | viewer/controller と image。変換は work/serial を照合してから採用 | 失敗時の両参照、再試行の URL 更新、閉じる・要素削除・client 削除。短時間の同一 client 再接続では現表示を保持 |
| _pending／_published／_dom_actions／_scheduled | ArtworkPage と client outbox。Vue 側は各ビューアの DOM に zoom/focus 状態 | 削除で作業を取り消し公開済み ID を FIFO で退役。切断は rewind 可能期間の再送を維持。切断期限後は NiceGUI の client 削除 |
| 容量通知タイマーと遅延中の計測状態 | LibraryView インスタンス。基点との差分に追加のモジュール共有状態なし | 既存容量試験。今回の変更範囲の外 |
| コンポーネント登録／クラス既定値・JS module | NiceGUI の登録表には component 定義。PageImage の None 既定値はインスタンスに上書き、JS module は描画定義 | インスタンス間で表示状態を共有しない |

C は製品ソースの構築箇所を AST の集合と比較し、増減時に対応するケースの見直しを要求する。初回・応答後の生成で実 Outbox の要素・メッセージを走査し、初期配線の重複と未オープンの DOM アクションを検出する。新設 HTTP リソース経路の集合を固定し、FastAPI の実ルーティングで GET/HEAD、別 app、失敗・再試行・閉じる・削除・サービス閉鎖を確かめる。タグ試験は非空から空へ変化する props を検査する。

## 修正前の実測

各ケースで標準更新はビューア配下63要素。実 NiceGUI Outbox の FIFO に追加された重複 ID 数 / 対象 root の JS 件数。

| 生成ケース | HTML 前 | HTML 後 |
|---|---:|---:|
| GalleryView | 0 / 0 | 10 / 0 |
| RankingView | 0 / 0 | 10 / 0 |
| LibraryView | 0 / 0 | 10 / 0 |
| MediaLibraryView | 0 / 0 | 10 / 1 |
| RecommendView | 0 / 0 | 10 / 1 |
| PersonalRankingView | 0 / 0 | 10 / 0 |
| AuthorBrowseView（両入口共通） | 0 / 0 | 10 / 0 |
| ヘッダーの ViewHistoryView | 0 / 0 | 10 / 1 |
| LearningView の ViewHistoryView | 0 / 0 | 10 / 2 |

NiceGUI 3.18.0 の Element.on と props は Element.update を呼ぶ。client.py:185 の初回応答は要素をシリアライズし標準更新をクリアする。outbox.py:101 の通常配信は標準更新を FIFO メッセージより先に積む。上表は送信される要素 ID の重複であり、同期配線の再マウント自体は観測していない。実測ログは red.log。

## 修正後

最終候補 a1198b7 の実 Outbox では、9経路とも HTML 前・後の重複0、未オープンのビューア向け JS 0。各ビューアの標準更新は63要素。初期 props とイベントを完了し、controller が bind_transport を呼ぶ。履歴の識別子は構築時に渡す。初回開封前の閉じる処理は保留中の要求があるときだけ片付けを実行する。

GIF の検索表は FastAPI app.state とルートのクロージャーに置く。PageImage が表示中の値を所有し、解放時にキーを明示して削除する。切替・再試行・検索による選択解除・表示失敗・ビューア終了・要素削除・クライアント削除で失効。同一クライアント内の一時切断では再送対象を維持し、遅着変換の採用前に要求と要素の寿命を確認。画像カタログの閉鎖フラグが立ったら GET / HEAD は503。フラグの参照はロックに入らず、カタログ入れ替え中も保持済み GIF を返せる。追加されたモジュール定数は UUIDv4 検査用の正規表現。

NiceGUI 3.18 の Client.delete は要素削除完了後に is_deleted を真にする。既に削除済みの client には退役メッセージを積まない。通常の client 削除順でキューに入る退役メッセージは停止した Outbox とともに破棄される。a1198b7 に対する Sol の読取り再確認範囲で追加の対応指摘なし。
