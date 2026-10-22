# 旧Fletランキングの隔離性能ベースライン

最新追補（2026-10-22）: 原UI7ファイルの隔離コピーとFakeClient/FakeDBを使い、Flet 0.85.3の専用ウィンドウを起動した。合成120作品/320画像を事前に温め、架空作品0003の全3ページを実画面と校正画像で確認した。第2版fixture SHA-256は `f1406aa85562b6b1b477d71d236bf175433dcb35d70796f1957018b4f56b3524`。現在のlauncherはranking tabだけで原製品shellを省くため、全画面互換や製品全体の速度受入は未完。原本をimport/起動せず、本番DB・画像・Cookie・APIは使用していない。以下は調査・実装時点ごとの記録で、各節の未実施記述はその時点を指す。

## 判定

**条件付きで可能。** 旧 `ranking_tab.build_ranking_tab()` と `artbook_viewer.build_artbook_viewer()` の実装を隔離環境へ限定コピーし、実際の `ft.GridView` / `ft.Container` / `ft.Image` / `ft.Stack` をFletで描画する構成なら比較の意味が残る。ただし「合成120件を本物のランキング描画ロジックへ流す入口」は公開されていない。ランキング取得・キャッシュを安全なFakeClientから通すか、隔離コピー内にテスト専用のfixture投入口を付ける必要がある。元モジュールをそのまま `import` して `gui.py`／`main.py` を起動する案は採用しない。

同じPNGとは、元と新で**PNGの実バイト列、作品順序、ページ数、画像寸法**が等しいことを指す。旧Fletへ合成PNGを `data:image/png;base64,...` として返す場合、HTTPメディア配信するNiceGUIと転送経路は異なる。転送方式の差を含むエンドツーエンド指標と、120カード描画／再描画の指標を分離して報告する。外部通信ゼロかつ画像の実表示を維持しない実験は「同一UI性能比較」とは呼べない。

## 関数と呼出元

| 対象 | signature／返却値 | 呼出元／副作用 |
| --- | --- | --- |
| `ranking_tab.py:439-440` | `build_ranking_tab(page, db, DT, uniform_border, _ui_update_lock, _marshal_to_ui, on_grid_card_size_change=None, dispatcher=None)` | `gui.py:4832-4836` が呼ぶ。戻り値は `ranking_tab.py:2605-2611` の `tab_ranking_bg`, `on_tab_open`, `open_personal_with_tag`, `close_viewer_if_open`, `set_grid_card_size`。 |
| `artbook_viewer.py:240` | `build_artbook_viewer(page, get_client, ui_update_lock, db, show_result_snack, DT)` | `ranking_tab.py:600-606` から常時構築。戻り値の `viewer_overlay` を `ft.Stack` に挿入、`open_viewer(work_id, is_ugoira=False, author_id=None, author_name=None, title=None, start_page=1)` はハンドラを返す（`artbook_viewer.py:1173-1265`）。 |
| `grid_card_size.py:36-67` | `cols_for_mode(mode)`, `grid_max_extent_for_cols(avail_width, cols, spacing, min_extent=170)`, `load_grid_card_size(db)` | 実列数上限は大6・中8・小12、デフォルト大。DB設定を読む。カードサイズUIは `ranking_tab.py:2542-2550` で `grid_card_size_ui.build_grid_size_row` に委譲。 |
| `ranking_tab.py:792-867` | 内部 `_build_card(item, rank_no)` | 本物の `ft.Container`, `ft.Column`, `ft.Stack`, `ft.ContextMenu` を生成、作品IDをキャッシュ。thumbnail URLがあれば8 workerのpoolへ投入（815-816）。 |
| `ranking_tab.py:886-982` | 内部 `_render()` | `_cache` の作品を整列しDB非表示IDを除外、実Flet `GridView.controls` に反映。差分の `world_column.update()` はFletへの実パッチ（975-982）。 |
| `ranking_tab.py:1143-1157` | 内部 `_ensure_loaded(force=False)` | キャッシュ未命中ならスレッドから `_fetch_page` を開始。実クライアント `get_ranking` は `ranking_tab.py:1016-1024`。単に`on_tab_open`を呼んでよいわけではない。 |

## 最小DIとFake契約

1. **画面**: `page` は本物のFlet `Page` を隔離ウィンドウで使用（比較上必須）。`DT` は少なくとも `tools/test_ranking_more_state.py:57-65` の色・spacing・radius・fontトークンを継承し、本来のテーマとの差分を記録。`uniform_border(color,width=1)` は本来のBorder生成を維持。`_ui_update_lock` は `threading.Lock` 等。`dispatcher` は実Pageへ安全に投稿・更新できる `ui_dispatch` を維持する（`ranking_tab.py:460-475`）。FakePage＋`BaseControl.update=lambda:None` はテストでは成立しても性能比較では**禁止**。
2. **DB**: 原本接続は禁止。最小設定読取 `get_setting(key,default)`、隠し作品 `get_hidden_work_ids()`, `get_hidden_works()`、好み下限 `get_discovery_preferences()`、personal初期値の `get_discovery_author_preferences()` などを呼出分岐に応じて専用tempDBか読み取りフェイクから供給（`ranking_tab.py:467,525,675,905,1419-1450,1514-1527`）。`grid_card_size_ui`、`filter_panel.load_json_setting` と `_load_personal_tags` も `get_setting` を要求。UIの書込みイベントは `set_setting`、`add_hidden_work`、`remove_hidden_work`、`add_boosted_author` などへ進むため隔離書込先以外を許さない（`ranking_tab.py:88-111,640,731-759`）。ビューア操作は `get_work`, `get_following_users`, `is_following_user`, `touch_last_backup` などにも到達しうる（`artbook_viewer.py:152-166,770,897`）。
3. **FakeClient**: `ranking_tab.PixivClient` の生成を隔離コピー内でFakeへ差し替え、`get_ranking(mode,content,page,date,lane)` は120件を元のページ取得形式で返す。`download_thumbnail_as_data_uri(url)` は合成PNG実バイトのdata URIを返す（`ranking_tab.py:545-565`）。ビューア `get_image_urls(work_id,size='regular')` およびページ画像取得経路も同じfixture bytesに結びつける（`artbook_viewer.py:1238-1264,463-548`）。フォロー・ブクマ・タグ検索・作品詳細・うごイラの各メソッドは画面の操作を含める範囲に従ってスタブを定義し、アクセス時は外部へフォールバックせず明示失敗させる。既存 `tools/test_ranking_more_state.py:96-127,159-175` はFakeClient注入とtmpDBの前例だが、同テストの `image_urls={}` と no-op update は性能ベースラインへ流用不可。
4. **非同期**: `ranking_tab.py:455` が構築時に `ThreadPoolExecutor(max_workers=8)` を作る。`_get_client` は初回使用時に `PixivClient(db=db)` を生成する（477-480）。`_fetch_page` と画像fetchを本物のFlet UI投稿経由で実行し、終了時にワーカー・ウィンドウを回収する必要がある。直接非公開のクロージャへ外部から `_cache` を注入するだけでは現実の読み込み・画面状態遷移の比較にならない。
5. **オーバーレイ**: `artbook_viewer.py:290` で実 `ft.Image` を生成し、`ranking_tab.py:2589-2595` の同じ `ft.Stack` に載せる。`get_image_urls` 後に画面更新するスレッド経路も原実装を残す（`artbook_viewer.py:1173-1265`）。ページの `on_keyboard_event` をビューアが差し替える（`artbook_viewer.py:1153-1171`）。クリックやEsc比較まで行う場合は本物のPageで観測する。

## 推奨隔離構成（将来）

`tools/legacy_baseline.py` は新レポジトリ側の制御入口だけとし、明示的に列挙した旧UI関連Pythonソースを、**公開対象外**の `artifacts/legacy-baseline/` 配下の一時ディレクトリへハッシュ記録付きでコピーする。そこから専用のFlet環境／一時作業ディレクトリ／tempDB／合成120件PNGのみで起動する。原本 `gui.py`, `main.py` はimportも実行もしない。Fletモジュールのトップレベルimportには `ranking_tab.py:25-34` の `artbook_viewer`, `discovery_ranking`, `filter_panel`, `nana7mi_vault`, `database`, `pixiv_client`、viewer側の `core`（`artbook_viewer.py:32-33`）があり、これらの**import時副作用の隔離／最小代替モジュール**が必須。未確認のimportがDB・Cookie・認証・ネットワークへ到達しないという前提で起動しない。専用プロセスのネットワーク遮断と原本ルートのimport探索排除を前提にする。

測定ゲートは、同じ120作品・同じPNG、固定ウィンドウサイズ、同じ操作（初期50→もっと見る50→残り20、大小切替、作品ページ送り、閉じる）を記録する。計測には起動から描画、実画像decode/表示、再描画、クリック反応、最終UI状態を含める。Fletの `more_cell` はGridView内部の最終セル（`ranking_tab.py:956-975`）であり、NiceGUIとボタンの位置が異なることを明示する。Flet本物のビルダーを保てない場合は「構造モック」であって、移植前後の性能比として公表しない。

## 未解決の確認事項

- `ranking_tab.py` のworld `get_ranking` 返却値の正確な正規化形式、`_fetch_page` が合成120件を何ページで読み込むか、`on_tab_open` の起動順序をfixtureで検証すること。カード入力の最小キー候補は `illust_id,title,user_name,url,rank,user_id,tags,is_masked`（`ranking_tab.py:792-855`）。
- `artbook_viewer.py:463-548` の画像取得メソッドと戻り値、再描画条件、外部ファイルアクセスと `core` import副作用を原本で追加精査すること。
- `filter_panel`・`grid_card_size_ui`・`discovery_ranking`・`nana7mi_vault`・`database`・`pixiv_client` のimport時動作と隔離copy範囲を確定すること。特に `core` を原本からimportさせない。
- UIの比較に使うFlet版を原本と一致させること（`artbook_viewer.py:269-303` に0.85.3の既知挙動）。アプリ起動から画像表示まで実測してからベースライン成立を判断する。

## 隔離ツール初版（2026-10-22）

`tools/legacy_baseline.py` と `tests/test_legacy_baseline.py` を追加した。Windowsで原本パスを明示して準備する例：

```powershell
.venv/Scripts/python.exe tools/legacy_baseline.py --source S:\Projects\PixivVault --prepare-only
```

ソースは `ranking_tab.py`、`artbook_viewer.py`、`grid_card_size.py`、`grid_card_size_ui.py`、`filter_panel.py`、`discovery_ranking.py`、`ui_dispatch.py` の7ファイルを `artifacts/legacy-baseline/source` に限定コピーし SHA-256 manifest を生成する。旧 `main/gui/core/pixiv_client/database` はコピーしない。静的 import は許可集合で検査し、不明な依存はコピー前に拒否する。fixtureは新NiceGUI側の `generate_demo_artworks(120)` と `DemoImageCatalog.get(...)` を使い、サムネイルと各ページPNGの実バイト・SHA-256を `artifacts/legacy-baseline/fixture` に記録する。

実行証拠（prepare-only）：ソース7件のハッシュと120作品のfixture manifestが生成された。データfingerprintは `ebf055b7c425afd161716bc857c174e891f1a8e68e7ca0f02655b9adc46d82cf`（`fixture.json` SHA-256）。`pytest -q --tb=short tests/test_legacy_baseline.py` は **5 passed**。ソース・fixtureのハッシュを起動直前に再確認し、FakeClient/FakeDB以外へのAPI呼出しは明示例外にする。

専用Flet環境をprimeが準備した後に限り、`--launch` を指定して隔離ウィンドウを起動する設計。コピー済み `ranking_tab.build_ranking_tab` を使い、`ft.GridView`・`ft.Image`・`ui_dispatch` の本実装を維持する。禁止依存を最小スタブに置換し、原本ツリーのファイルopenを拒否する監査フック、外部へのソケット接続拒否を設けている。現段階は**launch未実施・UI描画速度と更新動作は未検証**。専用環境での起動検証に成功するまで性能比較成立とは扱わない。初版のfake範囲は世間ランキング（daily/illust）に限定し、他のモード・操作は明示エラーとする。

## launch前修正（第2版・未起動）

- `DT` は旧 `gui.py:151-176` の `DesignTokens` 全定数を直接定義（原本 `gui.py` import無し）。Border `#2a2b38`、Serif `Yu Mincho`、`PIXIV_BLUE`, `PIXIV_BLUE_HOVER`, `PIXIV_BLUE_GLOW`, `GLASS_BG`, `TEXT_ELSEWHERE` を含む。`uniform_border` も原本 `gui.py:179-185` の四辺指定と一致。
- `ui_dispatch.UiDispatcher(page, ui_update_lock=lock, shutdown_event=event)` をコピーした `ui_dispatch.py` から生成、`ranking_tab.py:460-475` の `post/mark` を経由する。`_marshal_to_ui` への互換注入も `post(fallback)` とした（同引数は現 `ranking_tab.py` から直接呼ばれない）。Flet `Page.update` や `BaseControl.update` の無効化無し。ウィンドウcloseイベントでshutdown eventを立てる。
- 旧世界ランキングの `ranking_tab.py:1016-1091` は `get_ranking(...)` の戻り値を **list[dict]** としてそのまま `items` に使う（`_PAGE_SIZE=50`）。`_build_card` は `illust_id` を `str` にするが、旧PixivのIDと同じ数値として扱えるよう `legacy_id=1..120` を新fixtureに記録し `work_id=demo-0001..demo-0120` に対応。サムネイルとビューアの `fixture:demo-ID:page:view` は同一PNGバイトを使用。ビューアは `artbook_viewer.py:1238-1264` の `get_image_urls(work_id,size='regular') -> list[str]` と `:524-548` の `download_thumbnail_as_data_uri(url)->data URI` を使用するためこれをFakeClientで実装済み。その他APIは明示例外（ランキングpersonal・ブクマ・フォロー・外部ページは比較対象外）。
- `ft.run(main, view=ft.AppView.FLET_APP)` を採用する方針。Flet 0.85.3 の専用環境でAPI適合はprimeの実起動確認待ち。窓title `Isolated Flet Ranking Baseline` 固定、`--width 1280 --height 720` または `--width 1920 --height 1080`。非loopback socket/DNSとbrowser外部遷移を遮断。
- **既存manifestを維持すること**：第1版の `artifacts/legacy-baseline` は旧fingerprintを保持。第2版の準備と起動は `--output artifacts/legacy-baseline-v2` を指定すること。第2版の `fixture.json` には `legacy_id` が追加されるため、第1版manifestをそのまま `--launch` すると明示拒否される。第2版のfingerprintはまだ生成・確定していない。

Known side effects: 元コードの `ranking_tab.py:455` は8ワーカースレッドプールを作り、`on_tab_open` で取得用daemon threadを起動する。コピーされた `artbook_viewer.py:1153-1171` は `page.on_keyboard_event` を置換する。`ui_dispatch.py:53-103` はPageをweak registryへ登録し、Fletの更新キューとUIスレッドを使用する。FakeDB設定はメモリのみ。ファイル出力は指定artifact配下のmanifest/PNG。未解決：Flet 0.85.3でのnativeウィンドウの実起動、終了時のworker pool drainの確認、実レンダリング/クリック・エラーログ。これらがPASSするまで性能比較の受入済みとはしない。

## 専用worktreeのディスパッチャ契約修正

編集先は `PixivVault-NiceGUI-wt/legacy-baseline` の3許可ファイルのみ。旧 `gui.py:507-520` に従い、`marshal_with_dispatcher()` は非同期関数を `dispatcher.post(async_fn, fallback=fallback)` で渡す。key指定時だけ `key=key` を追加。先行版の `post(fallback)` は非同期処理を捨てていたため撤回した。コピーされた本物の `ui_dispatch.UiDispatcher` が通常時のasync、UIループ不在時のfallback、終了時のshutdown suppressionを担当する。Flet更新は無効化しない。

専用Flet 0.85.3ソース `flet/controls/core/window.py:17-26,105-111` で `WindowEvent.type: WindowEventType` および `WindowEventType.CLOSE` を確認したためclose判定を `event.type == ft.WindowEventType.CLOSE` に修正。`flet.run(main, view=ft.AppView.FLET_APP)` の実際の関数signatureも同環境で確認済み。実ウィンドウは未起動。Flet専用Pythonにpytestはなく、試験は既存開発venvで **7 passed**、専用Flet PythonでAST parse PASS。`FLET_VIEW_PATH` を含む実binaryの配置確認と、closeイベント到着後のスレッド収束はprimeの実走事項。

## 実描画後の境界／テーマ検証追補

- 旧 `gui.py:464-466` の `page.theme_mode=ft.ThemeMode.DARK`、`page.theme=ft.Theme(color_scheme_seed='#0096FA')`、`page.dark_theme=ft.Theme(color_scheme_seed='#0096FA')` を隔離Pageに追加。旧コードのFlet描画関数は変更なし。
- `safe_output()` は `--prepare-only` と `--launch` の両方で、worktreeの `artifacts` **直下の子ディレクトリ以下**のみに出力を限定。`..` 解決後、worktree外・artifacts直下・`.venv`/`source`/`fixture` パスや既存venvへの指定を拒否する。原本への書込みや実行経路は追加しない。
- `verify_pngs()` はlaunch前、manifestで検証したfixture.json内の全サムネイル／ページPNGに対してSHA-256を実バイトから再計算する。PNGのファイル名が単独basenameかも確認。PNG改変時はウィンドウを起動する前に拒否。
- `ui_dispatch.py` には `close()` / `shutdown()` の公開メソッドは存在しない（`UiDispatcher._is_shutdown()` は `shutdown_event.is_set()` を参照、`post/mark/_drain` が終了を抑止）。旧 `ranking_tab.py:455` は `ThreadPoolExecutor(max_workers=8)` をクロージャに保持するが、戻り値にshutdownハンドルがなく、無理な内部属性探索・UI関数patchでworkerを終了させる手法は性能比較の契約外。安全な終了はwindow CLOSEでshutdown eventをセットし、primeが**隔離プロセスと子ウィンドウの終了・残存PID/worker thread**を確認する方式。worker poolが残る場合は独立プロセス全体の適切な停止設計が必要であり、現時点で自然終了は未保証。
- 追加境界試験を含む `tests/test_legacy_baseline.py` は **8 passed**（開発venv）。既存Flet専用環境への依存追加・GUI起動なし。

## Warmキャッシュ校正（2026-10-22）

`FakeClient.download_thumbnail_as_data_uri` は全FakeClientインスタンス共有の `RLock` 保護されたURL→data URIキャッシュへ変更。キーは `(fixture_dir.resolve(), fixture URL)` で別manifestの同名作品に古い画素を使わない。キャッシュミス時のみfixture PNGのread/base64を行い、同時アクセスも同じ一回の読み込みになる。画像URLは許可された合成work_id、ページ範囲、`thumb`/`view`種別、正規のページ番号表記だけを受け入れ、外部URL・パス走査は拒否する。`warm_fixture_images` はworldの全3ページ（120作品）の全thumbと全viewページを事前読み込みし、warm校正の対象を固定する。

比較方法：cold（初回PNG読込＋base64変換含む）とwarm（全fixture画像キャッシュ済み）を別々に記録する。今回の3ページwarmベンチはlaunchで全サムネイル・複数ページ画像をキャッシュ温めしてから計測する。**same serving semanticsのfixture stub**であり、旧 `shared_image_cache` 本体は取り込まず、実Pixiv APIの通信遅延・認証・CDN応答は比較対象外。`tests/test_legacy_baseline.py` **10 passed**。warm対応後のlauncherで新しい専用プロセスを起動し、manifestと全PNGの検査、warm_cache_images=320を確認した。PID・HWNDは各試行のignored artifactへ記録し、次回は現在のprocess identityから再取得する。

## 主画像の描画面積の校正

launcher `6f2b66e53b275f0c614211e05d5f864e55c2f412`は任意の`--page-padding LEFT TOP RIGHT BOTTOM`を追加した。値はlogical pxで、有限・非負・4値・指定寸法内をlaunch前に検査する。未指定は既定を維持し、prepare-onlyでは使用しない。対象pytestは既存10件＋追加12件の22件成功、独立レビューにblocking指摘なし。

原UI7ファイルと画像fitを保ち、padding `10.5714 106.5 9.4286 4.8571`で、今回の物理client 1920×1080 / 175% DPIにおける3ページの実画像描画矩形をNiceGUIと揃えた。全6校正画面を目視し、各backendのwarm60遷移と通常終了を確認。これは原shellの省略を解消する変更ではない。数値・失敗試行・条件の限界は`MATCHED_VIEWER_BENCHMARK_2026-10-22.md`を参照。
