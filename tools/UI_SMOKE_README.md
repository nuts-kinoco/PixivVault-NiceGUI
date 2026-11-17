# UI スモーク（D4: 主要画面の自動回帰）

全ナビタブ（ダウンロード、キュー、失敗リスト、直近のバックアップ、重複整理、学習、ランキング、
ライブラリ、おすすめ、自分のランキング、定期チェック、メタデータ補完）と設定ダイアログ、
それに起動直後の画面を、実ブラウザ（Edge + Playwright）で 2 サイズ × ライト/ダークの 4 通り巡回する。
各フェーズ（機能追加・修正のたび）に繰り返し実行し、画面の崩れ・JS 例外・要素の欠落・自動通信を機械的に検出する。

## 実行

作業ツリーのルートで（製品コードは変更しない。`src` はそのまま使う）。

```powershell
$env:PYTHONPATH = "S:\Projects\PV-DEV-20261117\wt-l\src"; $env:PYTHONUTF8 = "1"
$env:TEMP = $env:TMP = "S:\Projects\PV-DEV-20261117\tmp_l"      # C ドライブに作らない
S:\Projects\PixivVault-NiceGUI\.venv\Scripts\python.exe -B tools\ui_smoke_browser.py
```

所要は約 2 分。終了コードは PASS で 0、FAIL で 1。

| オプション | 意味 |
|---|---|
| `--only download queue ...` | 画面キーを絞る（`startup` と `settings` も指定できる） |
| `--out <dir>` | 出力先（既定は `S:\Projects\PV-DEV-20261117\verify\ui_smoke_<日時>`） |
| `--port 22488` | 試験用ポート（既定 22488）。実機の 22386 は拒否する |
| `--strict-network` | 画面ごとの `allowed_calls` を無視し、どの画面でも偽 live client の呼び出しを失敗にする |
| `--startup-wait-ms 4000` | 起動直後に何もクリックせず待つ時間。ここで出る通信を「起動直後の通信」として数える |
| `--settle-ms 1500` | 各画面を開いたあとの待ち時間 |
| `--dump-testids` | 各画面で見えた `data-testid` を全部出す（期待要素を作り直すとき用） |
| `--expectations <json>` | 期待要素ファイルを差し替える |

## 何を検査するか

画面（キー）×サイズ（1280×800、1920×1080）×テーマ（light、dark）ごとに次を判定する。

1. コンソールエラー、未処理例外、HTTP 400 以上の応答が無い。
2. 期待する見出し・ボタン（`data-testid`）と文言が見えている。ヘッダーの認証状態ラベル、DB 状態バナー、
   借用プレミアムのトグル、全ナビタブは全画面共通の期待に入っている。
3. 本文に `Traceback` と `Internal Server Error` が無い。
4. 横スクロールが出ていない（`scrollWidth` が `clientWidth` を 1px 超えたら失敗）。
5. テーマ切り替えが実際に効いている（`body--dark` の有無）。

通信ゲートの検査（画面表の外、末尾に表示）:

- 起動直後（サーバー起動後、ブラウザ接続前）と各ページ読込直後（4 秒待機、クリック前）に、
  偽 live client のメソッドが 1 回でも呼ばれたら FAIL。呼び出し元（製品の関数名）も表示する。
- 各画面を開いたあとの呼び出しは、その画面の `allowed_calls` に無いものを FAIL にする
  （ランキングと学習は、タブを開くと取得する設計なので宣言してある）。
- サーバーの外部接続（ループバック以外への socket/DNS）は監査フックで遮断して件数を数える。
  ブラウザの外部リクエスト（画像 CDN など）は中止して件数を数える。
- サーバーログの Traceback。Windows の asyncio が接続切断時に出す
  `_call_connection_lost` / `ConnectionResetError` は製品の不具合ではないので除く。

注意: 偽 live client は停止ゲートを通らない。「製品が通信を試みたか」は分かるが、実クライアントの
停止ゲートが実際に止めるかは、この試験では確認できない。

## 起動構成（隔離）

`verify\<日時>\run\` 以下にすべて作る。実機のプロファイル・データ・ポート 22386 には触れない。

- 製品の `register_pages()` と `ui.run()` を使う。通信しない偽 live client を渡す。
- 旧ライブラリ DB は製品自身の旧スキーマ（`legacy/database.py`）で作り、作者 1 人・作品 1 点・失敗キュー 17 件を入れる。
- Cookie 未設定の隔離プロファイル（`NativeProfileBinding`）を渡し、ヘッダーの認証ラベルと Cookie 案内を出す。
  Cookie リーダーも共有 STOP も作らない。
- 管理保存先・設定・発掘・借用 vault・フォロー同期・画像キャッシュの各ルートを `run\` 配下に置く
  （これらのルートは事前に作らない。製品が作って印を付ける）。
- 出力: `summary.json`、`screens\<画面>_<サイズ>_<テーマ>.png` と `.txt`（本文）、`run\server.log`。

## 期待要素の更新（`tools\ui_smoke_expectations.json`）

製品の画面を意図して変えたら、このファイルを更新する。

1. `--dump-testids` 付きで実行し、`summary.json` の `testids`（画面ごとの可視要素）と
   `screens\*.txt`（本文）で現状を確認する。
2. `screens.<キー>.testids` に必ず見えているべき要素を足す。先頭の 1 件は「画面が開いた」の合図になるので、
   その画面専用の要素にする。末尾 `*` は前方一致。
3. `texts` は見出しやボタンの文言（本文の部分一致）。言い回しを変えたらここも直す。
4. `absent_testids` は見えてはいけない要素（起動直後の画面で他タブが出ていないこと、など）。
5. 画面が設計上タブを開いたときに通信するなら `allowed_calls` に偽 live client のメソッド名を書く。
   `global.startup_allowed_calls` は起動直後の通信を許す例外で、既定は空。足すときは理由をレビューで残す。
6. 新しいタブを足すときは `screens` に画面キーを追加する（既定の開き方は `tab-<キー>` のクリック。
   別の入口なら `"open": "<test id>"`）と、`global.testids` に `tab-<キー>` を足す。
7. `python -B -m pytest tests\test_ui_smoke_helpers_20261117.py` で形式を確認する。

判定ロジックは `tools\ui_smoke_helpers.py`（ブラウザ不要の純関数）にあり、上のテストで検証している。
