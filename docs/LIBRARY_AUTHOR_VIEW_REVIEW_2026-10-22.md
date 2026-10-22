# 合成ライブラリ作者閲覧の実装・受入

基準 main: `6d2046f4d6793340923661650b3e9cd2e29df35e` (PR #8)。専用worktree `phase4/library-view`。原PixivVault、共有側worktree、本番DB・実画像・Cookieは変更していない。

## 実装単位

`library_models.py`、`library_state.py`、`library_fixtures.py`、`ui/library_view.py`、`ui/viewer_controller.py` とshellの結線。120件の架空作品を作者IDで4人へ集約し、検索前の全作品の最新保存日を代表カードに採用する。作者詳細は全作品の保存日降順・作品内ページ昇順。最初50ページ、明示的50ページ追加、作品ページから共通方式の検証済み画像ビューアを開く。未知ページ数は1枚と注意表示、作品間移動/次ページ境界/画像失敗・再試行/遅着・フォーカスを独立状態で扱う。検索と閲覧数・保存日ソートは作者一覧だけに効き、作者詳細へ入った際は全作品を表示する。

既存rankingの表示、学習保存とUndo、VerifiedImageのdecode確認は継続。カードサイズはranking/libraryで共有する。作者詳細の状態・追加済みカードDOM・検索/ソート・スクロールは同一clientのタブ往復で保持し、新clientとは共有しない。マイブックマーク、類似、実ローカルZIP/フォルダ、pixiv検索、作者外部サイト、ブクマ・DL等はdisabledのまま。

## レビューで実際に修正した不具合

- 51作者以上で一覧が50件で打ち切られるため、作者側の明示的な追加表示を新設した。既存カードDOMを増分で維持する。
- 作者A詳細表示中に古い作者Bカードのクリックが届き、Bへ切り替わる経路をLibraryStateで拒否する。
- 単純なgeneration固定では詳細から作者一覧へ戻った同カードのクリックを拒否する回帰が実Edgeで発生。作者カードは再構成・reload時のsession identityで無効化し、詳細から戻った同カードのクリックは保持する。sort/searchで再構成した旧カードは拒否する。
- native受入器が参照する`native_ui_guard.py`欠落でpytest収集が停止したため、所有ウィンドウ・URL・操作期限・decodeを照合する補助部品を追加。nativeライブラリ検索セレクタを実DOMのplaceholderへ修正した。
- 作者カードに閲覧回数を表示し、閲覧数ソートの根拠を目視できるようにした。

独立担当2人で状態/イベント/試験器を読取レビュー。最後の状態差分を再照合し、確定blockingなし。従来の原版未移植項目と、native WebViewの最新版配布受入は別ゲートとして残す。

## 実行した試験

- 全pytest **358 passed**。既存shared venvのeditable installに依存するため`PYTHONPATH`を専用srcへ設定した。
- Edgeの`tools/run_browser_checks.py --channel msedge`はランキング、学習、画像競合、ライブラリの4scriptすべてexit0。1920×1080 / 1280×720、作者検索・並べ替え、指定ページ、98枚の50+48追加、画像decode、失敗/再試行、タブ往復、異なるclientの隔離を操作した。
- 最終確定前の1回は途中ソース変更がrunnerのhash比較で見つかり不合格として保存。次の`artifacts/library-phase4-20261022/edge-locked/runner-result.json`で全4成功、`source_changed=false`、`failure=null`、専用port解放を確認。出力PNGはignoredに保存。

## 保留中のゲート

今回のライブラリ画面を含む新SHAからWindows onedirをビルドした実WebViewの受入、別DPI/PC、サムネ実保存・容量、マイブックマーク・類似・詳細絞込、実DBへの読取接続、原製品shellとの外観比較は未完。CIのChromium試験もhead固有の結果を確認する。Phase4全体を完了扱いにしない。
