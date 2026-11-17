# 元 PixivVault（Flet 版）共通シェルの見た目仕様

出典: `S:\Projects\PixivVault`（基準 commit 3ac3aa0d）の `gui.py` と `main.py`。寸法・色の実測値は、隔離起動した Web 表示を 1280x800 / 1920x1080 で撮った画面（`verify\original_20261117_225445\`）から取った。実測値は Flutter Web の描画なので数 px の誤差を含む。

## 1. ウィンドウ・テーマ（main.py:418-428、gui.py:463-466）
- 既定サイズ 1920x1080、最小 1280x720、起動時は最大化。「元に戻す」で 1280x720 に戻す。
- 既定は `ThemeMode.DARK`。`ft.Theme(color_scheme_seed="#0096FA")` をライト・ダークの両方に設定する（Material 3 の自動配色）。
- 実測の地色は、ダークが `#111418` 付近、ライトが `#f8f9ff` 付近。選択中タブは seed 由来の PRIMARY の 12% 地（ダーク時の文字色は `#a1cafd` 付近）。
- 注意: カード類（`DesignTokens.BG_SURFACE` など）は固定の暗色トークンのため、ライトモードでも暗いまま残る。設定ダイアログの本体も、ライトの地の上に暗いカードが載る。この見た目は元の仕様なので、忠実に写すなら同じ挙動にする。

## 2. ヘッダー（gui.py:6125-6136）
page.add の最初に `db_status_banner`（実測では上端に小さな `DB: <パス>` 表示。y=12、約 11px、薄い灰色）を置く。その下は左右 2 カラムの Row（`SPACE_BETWEEN`）。
- 左カラム（縦）
  1. ロゴ文字列 `PixivVault`。`size=32`、`FontWeight.BOLD`、画像ロゴは無い。実測は x=10、y=38、高さ約 46。
  2. `login_status_text`（認証ラベル）。実測は y=94、約 13px、色は状態で変わる。未認証・期限切れは赤で、文言は「× [未認証/期限切れ] cookies.txt が見つかりません。設定からインポートしてください。」。有効時の文言と色は gui.py の `check_login_status`（約 6205 行）にある。
  3. `app_connection_row`（spacing=6、y≈135）。並びは次のとおり。
     - 接続アイコン（丸）と「アプリ: 未接続」
     - web_bridge_toggle（Switch）と「iOS連携 (トークン末尾)」のラベル
     - トークン再発行の IconButton（REFRESH、icon_size=14、24x24）
     - nana7mi_toggle（Switch）と「API切り替え」のラベル
     iOS 連携・Shaft 連携は本人が移植対象から除外しているため、移植版では別扱いになる。
- 右カラム: `Row(spacing=5)` に IconButton を 6 個、左から次の順で並べる。サイズは Flet 既定の 40px 級（実測の中心間隔は 45px）。右端に寄り、実測の y 中心は約 100。
  1. `history_btn`（HISTORY アイコン。直近のバックアップ）
  2. `view_history_btn`（VISIBILITY アイコン。閲覧履歴。件数バッジの更新あり）
  3. `open_folder_btn`（FOLDER_OPEN。ツールチップ「保存フォルダを開く」）
  4. `theme_toggle_btn`（ダーク時は LIGHT_MODE、ライト時は DARK_MODE。「ライト/ダークモードへ切り替え」）
  5. `cookie_picker_btn`（PUBLIC。「ブラウザで Pixiv を開く (ログイン状態確認・自動連携)」）
  6. `settings_btn`（SETTINGS。「設定」）
- Cookie 未連携バナー（`cookie_banner`、gui.py:2828-2850）。ヘッダーとタブの間に出す、角丸の情報帯。
  - 左に info アイコン、太字「【Cookie未連携】」、本文の 2 行
  - 右に「Pixivを開く」ボタンと × の IconButton
  - 実測は x=10〜1270（幅は画面に追従）、y=175〜258、枠は青 `#42a5f5` 付近

## 3. ナビ（タブバー、gui.py:87-118、6080-6117）
- 並び（左から）は `TAB_SCREEN_ORDER` のとおり。ダウンロード、キュー、失敗リスト、重複整理、学習、ランキング、ライブラリ、おすすめ。`TAB_HISTORY`（直近のバックアップ）はタブバーに出さず、ヘッダーの history_btn から開く。
- アイコン（`ft.Icons`）は次のとおり。
  | タブ | アイコン |
  |---|---|
  | ダウンロード | DOWNLOAD |
  | キュー | QUEUE |
  | 失敗リスト | REPORT_PROBLEM |
  | 重複整理 | CONTENT_COPY |
  | 学習 | EXPLORE |
  | ランキング | LEADERBOARD |
  | ライブラリ | PHOTO_LIBRARY |
  | おすすめ | RECOMMEND |
- 部品は `ft.TextButton(label, icon=...)`（既定の文字サイズとフォント）。バー全体は `Row(scroll=AUTO, alignment=START)`。
- 選択中は `color=PRIMARY`、`bgcolor=PRIMARY の 12% 透過`。非選択は `color=ON_SURFACE`、透明地。
- 「重複整理」の直後に `Container(expand=True)` の伸縮スペーサーを挟む。スクロール可能な Row 内では伸縮が効かず、学習より右の 4 つは重複整理から約 21px 離れて並ぶだけになる（実測）。学習ボタンだけ単独の `Row([btn], spacing=0)` に包まれている。
- 実測の幅と位置（1280 幅、y=269、高さ 32）は次のとおり。
  | タブ | x | 幅 |
  |---|---|---|
  | ダウンロード | 10 | 139 |
  | キュー | 159 | 96 |
  | 失敗リスト | 265 | 124 |
  | 重複整理 | 399 | 110 |
  - 学習 x=530 幅 82、ランキング x=622 幅 124、ライブラリ x=756 幅 124、おすすめ x=891 幅 110（1280 幅でも 1920 幅でも同じ値で、左詰め）。
  - ボタン間は通常 10px。重複整理と学習の間だけ約 21px で、スペーサーは幅を取らない。
- 高さの共通値は `TAB_HEADING_HEIGHT = 44`。キュー・失敗リスト・重複整理の見出しブロックに使う。見出しの下のコンテンツ領域は、タブバーの下に直接置く。
- 起動時に開くタブは `TAB_INITIAL = TAB_UNIFIED`（ダウンロード）。`main.py --tab <key>` で変えられる。
- ログ欄は `LOG_HIDDEN_TABS` により、全タブで非表示。

## 4. 配色トークン（`DesignTokens`、gui.py:151-176）
| 名前 | 値 |
|---|---|
| BG_VAULT | `#0b0b10` |
| BG_SURFACE | `#15161c` |
| BG_CARD | `#1e202d` |
| BORDER_VAULT | `#2a2b38` |
| PIXIV_BLUE | `#0096FA` |
| PIXIV_BLUE_HOVER | `#0080d6` |
| PIXIV_BLUE_GLOW | `#0096FA` の不透明度 25% |
| GLASS_BG | `#15161c` の不透明度 94% |
| TEXT_MAIN | `#e8e8ef` |
| TEXT_MUTED | `#8a8a99` |
| TEXT_ELSEWHERE | `#b9b9c4`（TEXT_MAIN と TEXT_MUTED の中間。MUTED と同値にしない） |

- 間隔: `SP_XS=4`、`SP_SM=8`、`SP_MD=16`、`SP_LG=24`、`SP_GRID=12`
- 角丸: `R_CARD=12`、`R_BUTTON=8`
- 見出し用の明朝系: `SERIF="Yu Mincho"`。設定ダイアログ・API 切り替えダイアログのタイトルなどに使う。
- 共通チップ `build_filter_chip`（gui.py:200-282）
  - 角丸 8、高さ 34、最小幅 112、パディング左 12・右 10
  - ラベル 12.5px、右端に件数バッジ（幅 26、高さ 20、角丸 4、11px）
  - 選択時: 枠 PIXIV_BLUE の 1.5px、地 PIXIV_BLUE_GLOW、バッジは PIXIV_BLUE 地に BG_VAULT 文字
  - 通常時: 枠 BORDER_VAULT の 1px、地 BG_SURFACE
  - 0 件は文字を MUTED にして、バッジの地を透明にする
  - 無効時は不透明度 34% で、件数は「–」
- ダウンロードタブの主ボタン（モード切替の選択中など）は PIXIV_BLUE 系の塗り。実測は `#0283da` 付近。
- 実測のカード外枠は、ダーク・ライトともに `#2a2b38`、カード地は `#15161c`。

## 5. 設定ダイアログ（gui.py:2406 付近）
- 角丸の `AlertDialog`。タイトル「設定」は SERIF、色は TEXT_MAIN。
- 本体は縦並びのグループで、先頭は「アカウント」。続く項目は次のとおり。
  - Cookie のインポート（ファイルを選択）
  - フォロー中リストの同期
  - 保存フォルダーの設定
  - ブラウザ拡張機能との自動連携（トグル）
- 実測の寸法（1280x800）は幅約 548、高さ約 604、中央寄せ。内部は暗いカードで、スクロールする。

## 6. 参照できる画面
- 隔離起動の全タブ・設定ダイアログ・テキスト（セマンティクス DOM）: `S:\Projects\PV-DEV-20261117\verify\original_20261117_225445\`。ファイル名は `<幅>x<高さ>_<dark|light>_<tab>.png` と `.txt`。
- 起動と撮影の手順: `S:\Projects\PV-DEV-20261117\wt-o\tools\original_run\`

