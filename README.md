# PixivVault-NiceGUI

Windows版PixivVaultの段階移植用リポジトリです。**既定は架空データの開発デモ**で、元アプリの置換版ではありません。明示指定による旧ライブラリの読取接続とpixivのGET専用接続を追加しています。実アカウントでの受入完了は宣言していません。

見た目・操作順・データ互換を維持し、画面更新や画像表示の待ちを減らすことを目指します。速度改善と外観の一致は、実機比較が終わるまで未確認として扱います。

## 専用環境

Python 3.13を使います。既存PixivVaultの環境には追加インストールしません。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pixivvault_nicegui --check
```

## デモ起動

```powershell
.\.venv\Scripts\python.exe -m pixivvault_nicegui
```

ブラウザを手動で `http://127.0.0.1:8089/` に接続します。ブラウザは自動で開きません。終了は起動した端末でCtrl+C。
既定のデモは本番DB・画像フォルダ・Cookie・pixiv APIに接続せず、120件の架空作品とコードで生成した診断PNGを使います。
検索、50件単位の追加表示、カード大中小、作品詳細、作品内のページ送り、学習4判定とUndoの検証経路があります。
学習は既定では画面内のメモリ台帳へ記録します。直前1件だけUndoでき、再読込や再起動では引き継ぎません。実データへの保存・学習アルゴリズムの本番接続は未実装です。
ライブラリでは作者一覧→ページ一覧→共有ビューアと、マイブックマークの検索・選択・ビューア・戻るを利用できます。「絞り込み」からタグを追加し、すべて含む/どれか、＋/−による含む/除外、個別解除を使えます。条件は同じ画面内のDL済みとマイブックマークで共有し、別の画面には影響しません。作者一覧を絞り込んでも、代表作品と作者詳細の全ページは変わりません。日付・ブクマ数など他の絞り込み軸、実pixivダウンロード、重複整理、おすすめ、外部アカウントへの書込み操作はまだ未接続です。

失敗リストは旧DBを明示指定した読取modeで、失敗理由の全文、タイトル/ID検索、カテゴリ、8件ページング、絞込全件のCSV出力を利用できます。Windows専用窓でも保存ダイアログから保存先を選択できます。再試行・個別削除・一括削除はまだ未接続で無効です。読取失敗時には前回の一覧を保持して再読込でき、DB未指定の実API modeは「未取得」と表示します。

Windowsの専用窓は `pip install -e ".[native]"` の後に `python -m pixivvault_nicegui --native` で起動できます。
初期位置・サイズはプライマリモニターの作業領域とDPIに合わせて計算します。1920×1080論理pxが収まる環境では維持し、小さな画面では作業領域内へ縮めます。開発機175% DPIでは、配置補助なしのコピーexe連続2起動で画面内の表示と最小化復帰を確認しました。任意モニター・全DPIでの実機保証はまだありません。
開発機のWebView2とonedir exeでは、画像表示、一覧/学習の操作、最小化・復元・通常終了とプロセス/ポート解放を検証しています。
別PC、Windowsの全DPI条件、トレイ、URI連携、長時間運用は未検証です。配布手順は [Windows配布検証](docs/WINDOWS_PACKAGING.md) にあります。

### 合成デモの判定を保存する

新しい専用フォルダーの絶対パスを明示すると、デモの判定を保存して次回は未判定の作品から再開します。

```powershell
.\.venv\Scripts\python.exe -m pixivvault_nicegui --learning-sandbox C:\PixivVault-Demo\learning
```

保存先には本デモの識別ファイルと `learning.sqlite3` を作成します。既存の任意DBや、識別ファイルのない使用済みフォルダーは受け付けません。同じ保存先を指定すれば、ブラウザの再読込とアプリの再起動後も判定済み作品を除外します。Undoは開いている画面の直前1手だけです。

保存中は同じ画像を表示したまま待ち、成功後に次へ進みます。失敗したときは「保存を再試行」でやり直せます。別の画面が更新した判定を古いUndoで消すことは拒否し、保存状態を読み直します。`--check` は指定した保存先を開かず、作成もしません。

### 合成画像の保存と永続キュー

```powershell
.\.venv\Scripts\python.exe -m pixivvault_nicegui --native --download-sandbox C:\PixivVault-Demo\downloads
```

新しい専用フォルダーの絶対パスを明示すると、架空作品を検索・選択し、全ページPNGを保存できます。pixivからのダウンロードではありません。既存の任意フォルダー、実Cookie、旧ライブラリとの併用は拒否します。指定しない場合は保存操作を有効にしません。

キューは8件ずつ表示し、検索・状態フィルター、ページをまたぐ選択、絞込全件選択、個別/一括の一時停止・再開・停止、確認後の履歴削除を行えます。エラーは専用保存先の失敗画面から再試行できます。受理したジョブはタブや画面を閉じてもアプリが管理し、アプリ終了時はワーカーを停止してから画像資源を閉じます。

同じ保存先で再起動すると履歴を読み直しますが、未完了ジョブを勝手に再実行しません。履歴削除は保存済みPNGやreceiptを削除しません。旧 `failed_queue` の読取画面とこの合成ジョブの失敗画面は別の接続で、本番の失敗記録を更新しません。

### 通常イラスト・漫画の原寸画像を専用管理先へ保存する

```powershell
.\.venv\Scripts\python.exe -m pixivvault_nicegui --native `
  --pixiv-cookie-file C:\Private\pixiv-session.txt `
  --pixiv-download-root C:\PixivVault-Managed\originals
```

Cookieと保存先は絶対パスで明示します。Cookieの親フォルダーと保存先は互いに包含しない別領域とし、旧DB・画像ルート・合成保存・学習sandboxとの同時指定は拒否します。保存先には新しい専用フォルダー、またはこのモードが作成した正規の識別ファイルを持つ管理フォルダーだけを使います。元PixivVaultのImagesやDBを指定しないでください。

「ダウンロード」→「ブックマーク更新」→通常イラスト/漫画を選択→「キューへ追加」で、全ページの **original** を取得し、検証した元バイトを保存します。ビューア用のregular画像やサムネイルで代用しません。外部通信はGETのみで、ブックマークの登録/解除、元DBやCookie原本の更新は行いません。小説本文・うごイラの保存は未接続です。対応する静止画はPNG/JPEG/WebP、1頁20MiB・2800万画素、1作品1000頁・合計2GiBを上限とし、超過や破損を成功扱いしません。

保存完了後は「ライブラリ」→「保存先更新」で、管理先の作者一覧・全ページ・共有ビューアを開けます。検索・ページ送り・戻り・タブ再訪は既存閲覧経路を使用します。再起動時には保存済みファイルと履歴を読み、外部通信や未完了ジョブの自動再実行をしません。エラーの明示再試行では、受理した作品情報を専用操作台帳から復元します。完全なreceiptと全ページが検証できる同じ作品は再取得せず再利用し、履歴削除でも保存物は消しません。

この新しい管理保存先は、旧フォルダー/ZIP/旧DBへの書込み互換を置き換えるものではありません。リモートで変更された画像の自動検知・強制再取得、作者単位の自動巡回、旧失敗記録の更新は未接続です。また、ファイル公開とSQLite確定は別の操作です。公開直後の停止やDB障害では、正常な保存物を保持したままジョブが停止/未完了になる場合があり、再投入時の実体検証で回復します。Windowsの電源断耐性を保証したものではありません。

## 実ブラウザの回帰試験

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[test,browser]"
.\.venv\Scripts\python.exe tools/run_browser_checks.py --channel msedge
.\.venv\Scripts\python.exe tools/check_learning_persistence_browser.py --channel msedge --output artifacts/learning-persistence-browser
.\.venv\Scripts\python.exe tools/check_readonly_library_browser.py --channel msedge --output artifacts/readonly-browser
.\.venv\Scripts\python.exe tools/check_live_bookmark_browser.py --channel msedge --output artifacts/bookmark-transport-browser
.\.venv\Scripts\python.exe tools/check_failed_records_browser.py --channel msedge --output artifacts/failed-records-browser
.\.venv\Scripts\python.exe tools/check_download_jobs_browser.py --channel msedge --output artifacts/download-jobs-browser
.\.venv\Scripts\python.exe tools/check_managed_downloads_browser.py --channel msedge --output artifacts/managed-downloads-browser
```

ランナーが専用の一時ポートにデモを起動し、Edgeで一覧・学習・画像故障/遅着を操作した後、自分のプロセスを停止します。一覧は1280×720と1920×1080で確認し、学習は操作試験に加えて画面サイズ別の画像・ボタン配置を確認します。
Chromiumを使う場合は `python -m playwright install chromium` を実行し、`--channel chromium` を指定します。
結果と合成画像のスクリーンショットは `artifacts/browser-ci` に保存されます。`check_browser_suite.py` は同じランナーへの互換入口です。pytestだけの成功を実画面の成功とは扱いません。
永続化のランナーは指定した出力先に新規の合成DBを作り、保存・再起動・ロック失敗からの復旧・複数画面のUndo競合を検査します。出力先は実行ごとに新しいフォルダーを指定してください。

## 既存データとの接続範囲

`ReadonlyLibraryRepository` は既存schemaの読取アダプタです。明示された既存DBを `mode=ro` / `query_only` で開き、ファイルが無ければ作成せず失敗します。
検証には一時DBだけを使っています。既定のUIに本番DBへの自動接続はありません。詳細は [読取契約](docs/LIBRARY_ADAPTER.md) を参照してください。

### 明示したライブラリを読む

```powershell
.\.venv\Scripts\python.exe -m pixivvault_nicegui --native `
  --library-readonly-db C:\PixivVault-Validation\library.sqlite `
  --library-image-root C:\PixivVault-Validation\Images
```

上記は隔離した検証コピーを想定した例です。アダプタはDBの初期化・移行・作品/キャッシュの書換えを行いません。SQLiteのWAL可視性を維持するため`immutable=1`は使わず、SQLiteが必要とする共有メモリsidecarの生成・更新までゼロであるとは保証していません。本番のDB/画像を使った受入とは切り分けています。

フォルダ・ZIPの静止画像、ID付きの保存名、一意に特定できる旧保存名、ブックマーク直下保存とハードリンクを読めます。別作品や別ページで欠損を埋めません。小説本文・うごイラ再生は未接続です。検索・選択・展開数は画面ごとに保持し、DB更新はUIとは別スレッドで読み、失敗時には最後に表示できた一覧を維持します。

### GET専用のpixiv接続

```powershell
.\.venv\Scripts\python.exe -m pixivvault_nicegui --native `
  --pixiv-cookie-file C:\Private\pixiv-session.txt
```

Cookieファイルはリポジトリ外で管理してください。値を端末出力やチャットへ貼り付ける必要はありません。起動だけではHTTP通信せず、マイブックマークの初回表示/明示更新時にホーム画面の認証済みIDと`/ajax/user/extra`で認証を確認します。有効期限を無視せず、公開/非公開×イラスト/小説の4経路がすべて成功した場合だけ画面のキャッシュを更新します。Cookie更新後はアプリを再起動してください。

画像は専用プロキシ経路で表示し、ブラウザへCookieを渡しません。画像CDNへのGETにもCookieを付けません。Cookieの書換え、ログイン実行、ブックマーク追加/削除、フォロー、DLは行いません。未DLイラストのページビューアと小説の表紙プレビューを実装していますが、実Cookieによる今回の実機受入はツール側の読取ブロックにより未実施です。接続コード・合成transport・実Edgeの検査を、本物のpixivに接続できたという証拠には置き換えません。

`--library-readonly-db`/`--library-image-root`と併用できます。旧DBのブックマークキャッシュが壊れていてもDL済み一覧は開け、ライブ取得に成功した場合は旧キャッシュの破損に妨げられず復旧します。ランキング・学習は引き続き合成データである旨を画面に表示します。`--check`は引数のモード表示のみで、DBやCookieファイルを開きません。

## 計画と証拠

- [フェーズ別計画](docs/ROADMAP.md)
- [基準と操作台帳](docs/BASELINE.md)
- [進捗・未完・検証結果](docs/PROGRESS.md)
- [作業規約](AGENTS.md)

NiceGUI 3.18.0を直接依存として固定しています。開発機で検証したWindows依存一覧は `requirements-lock.windows.txt`。別PCでの同一環境再構築と配布の受入は未完です。
