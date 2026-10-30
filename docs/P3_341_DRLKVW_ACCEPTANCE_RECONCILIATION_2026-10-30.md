# P3-341 D/R/K/L/V/W 原152期待・現実装・既存証拠の読取照合（2026-10-30 JST）

## 判定単位と正本

正式IDは **67件（D13 / R13 / K14 / L18 / V8 / W1）**。この文書の初回
（2026-10-30 12時台）整理を、同日13:26 JSTの保存済み `p3-343-ranking/edge-r3`
と実checkerの実行経路で再評価した。
**最新の機能範囲分類：(a) 原期待の当該機能条件を既存合成GUIの実操作・assertで覆う候補8件
（K03/K04/K06/K07/K08/K10/K11/K12）、(b) 原明示条件の不足が残る59件、
(c) 現ソースに確定した欠落0件**。
この **(a) 8件は正式独立APPROVEではない**。対応機能の観測範囲が原期待を
満たすと評価できるもので、実行条件・source SHA・証拠SHAを下に示す。
独立判定はworker9、横断Native/実GET/perfなどは別担当・別ゲート。
公式152の歴史集計 **33完了/43未達/76未確認** とv8の認定値には一切変更を加えない。

根拠（読取のみ）：

- `docs/P3_FULL_ACCEPTANCE_152_INDEX_2026-10-28.md`
  SHA256 `78fa125f87a82e241a983d4be7d90c5b49c4e1f702203e8b9500a47723c4d1b6`。
- `docs/P3_FORMAL_152_OPERATIONS_LEDGER_2026-10-29_v8.json`
  SHA256 `487065a9a52228266d540e36d3e3b83ea34a38bdde4d1fdb54e275d725e55bb4`。
- `docs/P3_FORMAL_152_EVIDENCE_GAPS_2026-10-29_v8.md`
  SHA256 `0d81b149d292636bbb9bfa66c8cf561fe5875c62bf0e771177a74135de47abeb`。
- 原v8各recordの `original_id`、`original_sources`、
  `original_expected_references_318`（`reference_id/source_line/axis/operation/expected/proof_path/proof_sha256`）、
  `expected_full`、`start_condition_provenance_v8`、
  `checker_route_v8`、`evidence_nodes_v8` を参照。**以下の短い原条件要約は原文そのものを置換しない**。
  本対象は `expected_full` 63/67記入。L04/L07/V07/V08の4件は未記入だが原参照・index条件は存在する。
  67件とも `start_condition_provenance_v8.status=not-explicitly-defined`。
  **これは原文に開始fixtureが指定されていないことを表すだけで、不合格条件ではない**。
  実試験の開始条件は委任された範囲で設計できる。実際に使ったsynthetic reader、初期タブ、
  画面幅、操作、断言、保存証拠を記録すればよく、原文にない実ネット・長期負荷・全作品を
  当該IDの個別必須要件として追加しない。
- v8は本対象中 **10件**だけに追加 `evidence_nodes_v8`（D09/D10、R01a/R01b/R14、
  L04/L07/L13、V01、W01）を結合。旧結果に別のproofがあっても
  v8の時点で正式全条件PASSと認定していない。

## 現340/341 source と古い証拠の扱い（初回照合の歴史）

固定 `artifacts/p3-340-native/frozen-01/source-manifest.json` の175 filesを
初回作成当時のworktreeで **実SHA照合**：174一致、1差分、欠落0。
唯一の差分は `src/pixivvault_nicegui/managed_similarity.py`
`083d1613f383f6bce6752ab70db5fab423196d0dcec04b96122516dfe367099a`
→ `764adf7e90faf900b8fc21bece900231a7d94fc2ed0f65c715ab6cfed1d9fb65`
（媒体再走査30秒制限の独立APPROVE済P2修復）。
元tree SHAは `f42fafe1f2f2e95a161f853225c3f54790cc54800bbc913d2fb19d851f26f796`、
manifest SHAは `d045e51078b06f12eb6341b4ca75acaa64cb3e3b61ae4345733f055b40689987`。
これは初回照合時点の事実。後続の現343実行を否定するものではない。
**元340からの変化が類似検索処理だけだったのは初回当時の事実**であり、
Native入出口の変更という理由だけでD/R/K/L/Vの既存実操作を一律再実行する根拠はない。

ただし各旧proofの `source_changed=false` は「そのrun中にsourceが変わらなかった」の意味。
現340と完全に同じという意味ではない。旧proofの `source_before` と現sourceを個別照合：

| 既存実GUI原票 | 元run結果・現在との関連source差 | 継承可能な限定事実 |
|---|---|---|
| `p3-335-d06-edge-r2/learning/result.json` | 16項目success、現在の追加GUI再実行なし | 4値判定、クリック/キーガード、Ctrl+Z、1280/1920での表示。review取消跡・復元・保存までD06全範囲認定しない |
| `p3-314-d15-review-r3/result.json` | 当時success=true・4run、現在 `ui/learning_view.py` SHA不一致 | 合成の振り返り保存・receipt。失敗/Retry、全review横断操作は不足 |
| `p3-315-d08-theme-recommend-r2/result.json` | top-level success=false、source_changed=true、現在learning_view別SHA | 途中の合成パネル操作だけ。全D08 PASSとは扱わない |
| `p3-318-d09-d10-live-path-worker4/edge-final/result.json` | success=true、source_changed=false（旧run内）。現learning_view/discovery_learning/pixiv_read_clientは3/3異SHA | 概要実UI開閉、短長頁、Esc、retry、明示GET等の合成操作 |
| `p3-314-r05-r08-recommend-r3/result.json` | top-level success=false、source_changed=true、現recommend_view異SHA | 既定順へ戻す/理由付き非表示等、run単位の限定操作は残す |
| `p3-318-recommendation-visual/edge-product-r4/result.json` | success=true、現recommend_view異SHA、recommendation_mediaとrecommendation_viewerは各SHA一致 | 合成画像カード・未保存viewer等の限定受入 |
| `p3-318-calendar-slider/edge-product-r2/result.json` | success=true/通常終了/port解放。現library_filter_panel SHA一致、library_view異SHA | L04/L07のカレンダー/slider releaseを実Edge操作済み、今の共通画面結線は限定要確認 |
| `p3-318-w01-context-entries/edge-r2/result.json` | success=true/通常終了/子0/port解放、現similar_view/services異SHA | 作者/ブクマ右クリック→類似、未解析理由・頁遷移の合成実Edge操作 |
| `p3-339-native/packaged-natural-cuda-r1/result.json` と `p3-319-gpu-runtime/numeric-parity-r1/result.json` | CUDA初回実行・2roundとCPU/GPU 1024次元数値の限定証拠 | W01のGPU実ノード全網羅や現341 UI全受入には自動拡張しない |

原FletドキュメントとPythonコードとの最終差分照合・実画面Flet比較はprime担当。
元FletのDB/画像/ZIP/Cookie、実作品ID本文、ONNX内容は読んでいない。
v8原参照には旧スクリーンショットや元IDも紐付くため、この文書には転記しない。

## D群：13件

以下、**a** は原機能条件が合成の実GUI操作とassertで覆われた候補（独立認定前）、
**b** は原文に明示された機能条件の具体不足が残る状態、
**c** は現コードの明確な未実装。各行のSW/RC参照と逐語期待の全集合はv8当該IDに固定。

| ID | 分類 | 元期待の主要軸と現在わかること／未結合範囲 |
|---|---|---|
| D01 | b | SW D-1/D-7：明示候補取得、在庫0、再探索、全操作応答。learning系checker経路あり、在庫0→再試行の全実UI結果未束縛 |
| D02 | b | SW D-1/D-2・RC57：4値・次作品・保存永続・review横断。335で4値の単回GUI、別タブ/再起動と全RC57は残る |
| D06 | b | SW D-3・RC57：Undo/Ctrl+Zの前作品復元に加え、reviewキーガード、切替、保存、取消跡、元に戻す、閲覧バッジ。335はCtrl+Zと画面だけ確認 |
| D07 | b | SW D-4：探索率sliderのrelease直後の数値・説明・位置一致。実部品と反映の一連原票未結合 |
| D08 | b | SW D-5：傾向パネル開閉、判定時自動閉鎖。315の限定runには上記source変動あり |
| D09 | b | SW D-6：概要開閉・Esc。318合成実Edge成功、現summary/learning結線SHA再照合要 |
| D10 | b | SW D-6：長文頁送り・先頭/末尾・Esc。318合成実Edge成功、実GETと現source接続は別 |
| D11 | b | SW D-1・RC57：「めくる/振り返り」切替、キーガード、復帰。learning_viewのreview dialog導線あり、原ビュー切替・一連GUI証拠不足 |
| D12 | b | SW D-8/D-13：検索・期間・0件・タブ復元。learning_view review検索/期間UIあり、境界ケース未結合 |
| D13 | b | RC57：任意review行取消・取り消しましたの跡。learning_viewのreview-undo配線あり、現候補一連操作未結合 |
| D14 | b | RC57：「元に戻す」・永続判定復元。review-restore配線あり、取消/復元/restart証拠不足 |
| D15 | b | RC57行26：振り返り好意行からローカル作品保存、verified receipt。314の合成4run限定PASS、失敗/Retry・現在source全scopeは未了。CSV exportと解釈しない |
| D16 | b | SW D-14：失敗画像→UI上のRetry→表示復帰。image races/review checker経路、失敗→復旧実UIを原条件へ固定し直す必要 |

## R群：13件

| ID | 分類 | 主要原期待／既存範囲と不足 |
|---|---|---|
| R01a | b | SW R-1・RC56：保存済おすすめ再読込、再訪・cache維持。318 visual実Edgeは画像/未保存viewer限定、再訪・失敗を全条件へ未対応 |
| R01b | b | SW R-6：取得中表示→入替→失敗復旧。更新UI/取得sourceあり、旧318 visual証拠だけでGET更新の成否全体は閉じない |
| R03 | b | SW R-3/R-14：節畳む/展開と持続。recommend_view shelf UI経路あり、再訪保持実操作未対応 |
| R04 | b | SW R-4：並べ替えdialog押下毎の順序と本体一致。controller順序変更・UI経路あり、連続GUI未対応 |
| R05 | b | SW R-5：即既定順。314合成操作はtop-level source_changed/false success、正式全条件未認定 |
| R07 | b | RC56/60A：SuperLike登録/保存対象と節2、実DL・直後反映。現recommend_view action/保存導線を元RC横断へ未対応 |
| R08 | b | SW R-7・RC60A：作品/作者非表示、理由・次回非表示・undo競合。314の作品理由3値の限定観測のみ、作者と後続読戻し残る |
| R10 | b | SW R-7・RC60A：非表示作品/作者を復元し再表示。Undo UI経路あり、復元/再取得連打の原期待未網羅 |
| R11 | b | SW R-7・RC60A：作者boostと節4の更新。登録導線はあるが元RC全文・再取得未対応 |
| R12 | b | SW R-7：boost解除と次の候補更新。解除UI/状態経路あり、変更後の実UI結果未対応 |
| R14 | b | RC56：6→12→18と実追加取得/終了条件、タブ軽さ。318 visual限定、長頁・失敗・性能/再起動全条件を未結合 |
| R16 | b | SW R-1・RC60A：明示Pixiv bookmarkと画面結果、viewer連動。pixiv_actions/read-only policyで配線・合成制御あり、実Pixiv POSTは本割当禁止で正式証拠にはできない |
| R17 | b | SW R-15・RC60A/B：作者follow、成功/失敗の表示、viewer導線。合成actions試験は外部実投稿の証拠にならない |

## K群：14件（**aが8件／bが6件／cが0件**、2026-10-30 13:26時点の機能証拠照合）

| ID | 分類 | 主要原期待／不足 |
|---|---|---|
| K01 | b | SW K-1/K-8：公開mode/条件の一部と個人画面の表示操作は既存Edgeで観測。ただし原K-1の大中小・管理メニューを含む全部品を順に押す操作と世間/自分選択表示の全結果は、このr3チェックで直接assertしていない |
| K02 | b | SW K-1・RC46②：週間/月間およびR18切替の通信・表示はr3でassert。原RC46の `illust_ai_type` 実値照合・新人を含む全モード組合せ・作品3種ごとの結果までは観測していない（実GETを当然の個別ゲートにはしない） |
| **K03** | **a** | **SW K-5＋index明示の未来日付失敗**：r3のカレンダー日選択→表示日と先頭カードの変化、未来日付→説明表示/追加呼出なし/旧一覧保持を実assert。行 `acceptance.py:233–261`。未実装という初回判定は訂正 |
| **K04** | **a** | **SW K-4/K-11**：小説select直後に `公式小説ランキング`・`novel:` IDの3カードが表示、全表示カードの表紙画像decode（`naturalWidth>0`）を実assert。行 `acceptance.py:280–286`。novelクリック先は原K04に明示されていないので別要件にしない |
| K05 | b | SW K-6＋indexのmode別適用/無効理由：r3で週間R18のreader `weekly_r18` 到達、月間でR18 unchecked/disabled、reader `monthly` をassert（`acceptance.py:263–278`）。**月間/新人で無効となる理由文の表示そのもの**はr3でassertしていない。原indexの明示条件として不足を保持 |
| **K06** | **a** | **SW K-6**：小説AI除外を操作し、3表示→2表示・該当IDの除外をassert（`acceptance.py:296–299`）。これは表示内容が切替直後に変わる原期待の合成証拠。全作品実API照合は個別必須条件ではない |
| **K07** | **a** | **SW K-7/K-10**：tag入力だけでは追加readなし、Enter確定後に `0 件` 表示、旧カード0、synthetic readerの検索引数一致。失敗時には別noticeと再試行可能なボタンをassert（`acceptance.py:324–337`）。原0件・失敗分岐を観測 |
| **K08** | **a** | **SW K-3**：小説表示中の「評価数」選択で先頭カードのID変化を実assert（`acceptance.py:300–302`）。このkindの評価数は観測されたbookmark_countに基づく。全sort軸の網羅はK-3自体にない |
| K09 | b | SW K-9のタグ候補、含む/除外、個人プリセット・反映・保存/復元はr3で実操作。ただし当該recordの原参照RC#60Aにある**別画面おすすめカード右クリック、ブクマ/非表示/boost/queue、競合取消**はこの個人ranking checkerで試していない。個人ranking成功だけでRC参照を上書きしない |
| **K10** | **a** | **SW K-9**：ブクマ数を「500+」へ変更し古いカードを消して「未取得」表示、明示再取得で所定2件を実assert（`check_personal_ranking_k09_k12_browser.py:209–221`） |
| **K11** | **a** | **SW K-9**：期間を「1か月」へ切替後に3件→2件、全期間に戻して3件を実assert（同 `:188–194`） |
| **K12** | **a** | **SW K-9**：作者上限「1」を入力しTab確定、3件→2件、後のclearで上限「0」と5件復帰を実assert（同 `:195–207`） |
| K13 | b | SW K-1/indexの再取得・失敗復旧：明示取得/失敗notice/操作再有効はr3でassert（`acceptance.py:220–224,330–337`）。**失敗した同条件の再取得成功**までは実操作で繋いでいない |
| K14 | b | RC59/SW K-1：小説moreクリック→第2頁追加4件→終端notice/ボタン非表示、タブ往復のsnapshot保持はr3でassert。ただしRC59の**スクロール終端でのみ見える配置／無限スクロールなし／読み込み中disabled・成功失敗後復帰／複数頁後のタブ非固着**の実操作確認は欠ける。長期負荷試験等を新規必須にはしない |

上のa8件は **原機能範囲が合成製品GUIの操作で観測できた**という記録であり、
worker9の独立レビュー、Native/実データ/性能等の横断受入の完了とは分離する。
K03/K04の初回未実装判定と修復提案は後続source＋r3実証によって失効した。
K05の理由表示、K09の別画面RC60A、K13の失敗後再取得、K14のRC59分岐など
**原文に明示された未操作ケース**は引き続き不足として保持する。

## L群：18件

| ID | 分類 | 主要原期待／既存範囲と不足 |
|---|---|---|
| L01 | b | SW L-1：保存済作品/作者、旧folder/ZIP複数媒体。library_view/readonly_library経路あり、原媒体別全UI未完 |
| L02 | b | SW L-3：作者/作品/本文検索直後の結果・復帰。検索UIあり、原V02条件の全実操作証拠不足 |
| L03 | b | SW L-1：作者条件絞込、状態復元。UI実装あり、実条件結合不足 |
| L04 | b | SW L-1：日付from/to・カレンダー実操作。318 Edge r2の選択/通常終了を再利用、library_filter_panel同SHAだがlibrary_view変更あり。**v8 expected_full空欄だけを不合格理由にはしない**。現library_view結線での開始値→日付選択→表示結果の対応が不足 |
| L05 | b | SW L-4：タグ追加/切替/削除、サブview共有・件数。filter UIあり、3操作連続・2client条件不足 |
| L06 | b | SW L-5：AND/OR即時ラベル・件数変更。filter UIあり、正負結果と2client条件不足 |
| L07 | b | SW L-6：ブクマ数slider release後即反映。318 Edge r2に実release観測あり、library_filter_panel同SHA、library_view変更あり。**v8 expected_full空欄は不合格条件でない**。現結線・結果件数の対応が残る |
| L08 | b | SW L-7：R18トグルの件数変化。UI filterあり、実例/復元不足 |
| L09 | b | SW L-7：AI除外の件数変化。UI filterあり、実例/復元不足 |
| L10 | b | SW L-7：未読のみ表示。UI filterあり、既読/未読実状態の結果不足 |
| L11 | b | SW L-7：未取得のみ表示。UI filterあり、同ID媒体stateの結果不足 |
| L12 | b | SW L-1：容量再計測・自動復元、folder/ZIP・安全取消。314の1280/1920初回/再起動限定Edgeがあるが旧source_changedの候補認定に流用不可。313合成L12と別 |
| L13 | b | SW L-2/R-2/K-2：サイズ大/中/小、別タブ経由なしの共通反映。UI size_buttons/共有stateあり、v8 visual proofは部分。3タブ同時順序未完 |
| L14 | b | SW L-1：マイブクマ一覧切替、表示帰還。bookmark/library UIあり、全件・空件数証拠不足 |
| L15 | b | SW L-15：個別選択・解除、未選択状態のdisabled/表示。bookmark bulk checker経路あり、初期/取消/2client保持未統合 |
| L16 | b | SW L-15：選択作品一括DL、0件時disabled、実保存receipt。bookmark bulk実装あり、UI→保存確定・再起動を1セットで未結合 |
| L17 | b | SW L-1：ライブラリ明示更新、保存後すぐ再表示。refresh UIあり、保存世代・再起動実操作不足 |
| L18 | b | SW L-14/UG/RC：保存ファイル・folder/ZIPを実Explorerで開く、失敗表示。desktop integrationと外部表示buttonあり、実OS起動の記録と媒体種類別期待未統合（Native/OS担当と重複実行しない） |

## V群：8件

| ID | 分類 | 主要原期待／既存範囲と不足 |
|---|---|---|
| V01 | b | RC60C/V05：サムネ左click→viewer、Esc・focus復帰・他tab入力。318 visualの未保存viewer等は部分。v8にproofノード1 |
| V02 | b | RC60C/V09：左右キー/端/decode、現在頁＋次1枚まで先読み、別作品/他タブ。viewer controller/prefetch経路あり、全実操作未結合 |
| V03 | b | ARTBOOK_VIEWER_SPEC:71–77：regular既定、明示original、対象1枚だけ。過去原寸専用checker/修正があるが異作品残像・BMP・stale clickの修復後現GPU/GUI全受入未認定 |
| V04 | b | INFO_ACTIONS_SPEC:40–77：画像下48px情報行、I/Tab/Esc、詳細panel開閉。viewer UI部品あり、px・キー・切替原期待の現実画面証拠不足 |
| V05 | b | ARTBOOK_VIEWER_SPEC:84–93：現在の1ページだけ個別DL、作品まるごととは区別。現viewer_controller.py:323-343→managed_viewer_page.py:125以降の専用save経路あり、実1枚/再起動/失敗/receipt・元配置互換を未結合 |
| V06 | b | RC60C右クリック：Pixiv作品ページを開く、左クリックviewer維持。viewer pixiv_link/右クリックmenu/外部起動経路あり、実OS browser focus・旧閲覧維持未確認 |
| V07 | b | BASELINE V09/採用操作：zoom倍率、別ページ/閉じ後100%。旧 `p3-viewer-zoom-pan-20261028` 合成Edge限定成功を保存。必要なのは現sourceでの別ページ・閉鎖後倍率の具体的対応確認であり、v8 expected_fullの空欄自体ではない |
| V08 | b | BASELINE V09/採用操作：pan、pointer・ページ変更の復元。旧同checkerにpointer実操作あり。別作品・別ページでの復元を現source証拠に結ぶ必要があり、v8 expected_fullの空欄自体ではない |

## W群：1件

| ID | 分類 | 主要原期待／既存範囲と不足 |
|---|---|---|
| W01 | b | RC61・SIMILAR_WORKS_SEARCH_SPEC:80–137。作者・マイブクマ右click「似た絵」、解析済みは類似度順、未解析は理由、閉じる、既存click無回帰。318 context-entries Edge r2は合成の入口/結果/状態と子0/port解放を実操作。339 packaged CUDAは初回のみCUDA（再起動未load）、319 parityはstyle/composition共に合格。現similar_view/servicesは318当時から変更、managed_similarityのみ340→341差。GPU限定APPROVEと正式W01全原条件PASSは別 |

## 正式認定ができない理由と新規実行の最小集合

機能範囲で原期待を満たした合成実GUIの **(a) 8件** と、具体的な原明示条件の不足を持つ
**(b) 59件** を区別する。checkerの存在やCPU件数のみで成功とはしないが、
実際に `expect(...)`／`assert` が通過した項目を「開始条件null」などでゼロへ戻さない。
原依頼の「実開始条件・操作・観測・証跡を明記」は下記のr3固定手順で実現可能であり、
原仕様の `start_condition=null`／`expected_full=null` に受入禁止という意味はない。
正式独立承認と152歴史集計の改定はこの文書の職掌ではなく、worker9の独立判断を待つ。

次の**新実行の候補**は広い再走査でなく、既存結果に無い観測を同一GUIセッションでまとめる：

1. **K（新しい実装を繰り返さない）**：K03/K04/K06/K07/K08/K10/K11/K12は
   `p3-343-ranking/edge-r3` の実操作証拠へ対応済み。残る実操作の候補はK01の
   管理/サイズ等、K02のRC46各モード条件、K05の無効理由表示、K09の別画面RC60A、
   K13の失敗後同条件再取得、K14の末尾配置/読み込み中/失敗復帰など**原文に明示され、
   checkerでassertされていない範囲だけ**。K03/K04の修復を改めて要求しない。
2. **D**：現 `check_learning_browser.py` に335で未実施のreview取消跡→元に戻す→保存/バッジ、D12の0件/期間復元、D16失敗再読込を加え、318 D09/D10の変更済みlearning結線だけ限定再確認。4値/Ctrl+Zを意味なく再実行しない。
3. **R**：318 visualで実施済の画像カード/未保存viewerを保持。
   今の `recommend_view.py` から保存結果/新GETの区別、節順・折畳復元、R05/R08/R10–R14の作者含む非表示/undo/boost/続き/終了を原期待の一連UIで検証。R16/R17は既存合成action結果に留め、禁止された実Pixiv POSTを勝手に行わない。
4. **L**：318の実calendar/slider成功をそのまま限定証拠として保持し、変更された
   `library_view.py` との結線だけ狙う。同じ既存library/bookmark-bulk/L12 checker群で、残るL01–L18の表示、検索/filter共有、0件、カードサイズ共通、保存後更新、selection→保存receipt、容量の停止/復元の欠けた分岐を結ぶ。L18 OS起動はNative/OS担当との共有範囲を先に調整する。
5. **V**：旧viewer/zoom/panの代表実操作を証拠保持。
   `check_v03_original_browser.py`、`check_viewer_save_parity_browser.py`、
   `check_viewer_zoom_pan_browser.py` で原寸失敗後の復帰、異作品/別ページ、詳細キー、
   個別ページだけの保存/receipt、右click起動/フォーカス、原キーガードだけ未確認部分を追加。
6. **W**：318の作者/ブクマcontext成功・319/339のGPU限定証拠を保持。
   変更された `similar_view.py` と `services.py` のクリック・世代・閉鎖/未解析理由だけを、
   既存 `check_w01_context_browser_owned.py` の合成caseで絞って再結合。
   GPU全graph数値や配布CUDAをNativeだけの変化を理由に一括再実行しない。

これらは**実行指示ではない**。今回新GUI/test/製品修正/Git/Claudeを行わず、
現ファイルと保存済JSON/XMLだけを読んだ。画面再試験の必要範囲はprimeがsource
固定とGUI担当専有を確認してから決める。旧Flet比較・Q・Nativeの別担当境界を保つ。

## 2026-10-30 13:26 JST 固定edge-r3の原機能条件照合（上記再分類の根拠）

### 実際の試験開始条件、ソース固定、結果

- 主checker `artifacts/p3-343-ranking/acceptance.py` の**ファイル実SHA256**：
  `f2968277e0b7ec10092fa520096e8c593f3f6ab3f4844b00ffd14a5f1ccc1db1`。
  `edge-r3/result.json:177` の `checker_sha256` と同じで
  `checker_unchanged=true`（同:514）。過去checkerの同名・類似版でなく**このbyte列**に紐づく。
- 同checkerがimportして直接呼ぶ
  `tools/check_personal_ranking_k09_k12_browser.py` の現ファイルSHA256：
  `a11204a4d2d0562976353a1dd51ea9722bf93b60fb9d9a9b2a9a5aa9fcfeb4a8`。
  個人タブのチェック本体は同ファイル `run_viewport:126–285`。
  主checker `acceptance.py:343–453` が一つの合成サービスを起動し、
  `:397–402` で公開rankingを **1280×720 → 1920×1080**、
  続けて個人rankingを **1280×720 → 1920×1080** の実Edge操作に通す。
  このimport先も静的に行単位で照合した。runの
  `checker_unchanged` は**主checkerだけ**をSHA比較するので、
  import先のrun時byte同一性を単独証明する値と混同しない。
- **開始条件**：`acceptance.py:39–101,103–167`。
  製品shell/RankingView/RankingSource/RemoteBookmarkImagesは本物、
  外部reader/Vaultだけ合成メモリーfixture。最初の公開一覧は3作品、
  小説も3件＋次頁1件（`get_novel_ranking:68–77`）。
  画像は自作の120×80 PNGを戻す合成reader（`:44–47,91–96`）。
  `serve:103–167` は非合成サービスを拒否し、外部通信を禁止。
  `run:343–401` は所有Edgeとnonce付きローカル状態照合を使う。
  実Cookie、実作品/元画像、実Pixiv GET、課金、実書込はこの試験の開始条件ではない。
- 保存結果 `artifacts/p3-343-ranking/edge-r3/result.json` の**実SHA256**：
  `15849d8520847ce7b218bb2722f79d6dddba7d0fba117a3ebf04caef261bfaaf`。
  結果 `success=true`、両公開viewport `passed=true`（各named-check14）、
  両個人viewport各15個のassertion名、`edge-r3-exit.txt=0`。
  `result.json:311–340` は `errors/page_errors/console_errors/external_requests=[]`、
  `forced_cleanup=false`、正常shutdown、source close成功、子0、
  OS所有プロセス exit0、port解放を記録。
  `source_before` と `source_after` は**170ファイルで不変**。
  これは現候補全573ファイル・別Native配布・実APIの結果の代用ではない。
- 関連現source SHAがedge-r3の `source_before` と一致することを再読取確認：
  `ui/ranking_view.py` =
  `0bb228af6a3571b247bcc8b77c4deed553b5b0fa03e2dfe07f376c69bb317adb`、
  `ranking_source.py` =
  `41c69025db64859804a4c7589d40674a230799ed64ffa8fd5c3d5787fbd93555`、
  `ui/personal_ranking_view.py` =
  `39ae692d9d876fe6e634f03790a00624ed7eb20b9396f46cbb704230747fab90`、
  `pixiv_read_client.py` =
  `87ae48dce6d0ba7c99e7fad89ff6e1a9477d83198e66ffad0a50043bb547153b`、
  `library_media_registry.py` =
  `15f752473e86a0075762ecebc0e0e180de9380d8f32daacef8013d3e996a2e1b`、
  `remote_media.py` =
  `ee2eb856d594c9673fc5249afe90d3e1da5c0d0e9628201cc2f8b8cc50e67206`。
  旧314/318試験の版差を現在のedge-r3へ混在させない。

### 原literal → 実マウス／キーボード操作 → 直接assert → 事後named-check

以下の「通過」は `edge-r3` 結果の `success=true` と各viewport
`passed=true` に加え、**named-check追加より前の実際の断言**を
checker原文で確認したもの。名前だけが存在することを成功根拠とはしない。

| 原ID / 原SW | fixtureでの操作と実assert（行番号は上のexact checker） | named-check または保存先／残る境界 |
|---|---|---|
| K03 / SW K-5 | `acceptance.py:225–230` 入力日付でボタン表示・先頭カード本文・readerへのdate引数をassert。`:233–245` カレンダーの「2」を実clickし日付表示と内容、reader日付をassert。`:246–253` cancel後の追加readなしをassert。`:255–261` 未来日 `20990101` で**`expect(notice).to_contain_text('日付・モード・検索条件を確認')`**、追加呼出なし、旧カード表示保持をassertし、正常日に戻してからnamed-checkを追加 | `date_input_applies_without_fetch_click`、`calendar_day_click_updates_list`、`calendar_cancel_does_not_fetch`、`future_date_no_request_preserves_previous_results`。原indexの未来日付失敗も機能範囲で観測済み |
| K04 / SW K-4/K-11 | `acceptance.py:280–286` 小説select→notice、`novel:101–103` のカード3件、reader `kind=novel`、すべての表示 `img.complete && img.naturalWidth>0` をassert。続けて `:287–294` 明示moreで次頁と終端をassert | `novel_namespace_and_cover_decode`、`novel_append_and_end_of_results`。原K-11の非空サムネを合成表示で直接実測。**原K04に小説カードclick先の明示契約はない**。未定義の本文reader/外部リンクを追加合格条件にしない |
| K05 / SW K-6＋原index | `acceptance.py:263–278` 週間R18をclick→reader `weekly_r18`、月間へ切替後 unchecked/disabled・reader `monthly` をassert | `period_and_supported_r18_auto_reload`。**無効理由文の表示**はこのcheckerが断言しておらず、原index上の具体的不足 |
| K06 / SW K-6 | `acceptance.py:296–302` 小説一覧でAI除外をclick、カード3→2、残ID2件をassert。reader fixtureでは絵の詳細GETを禁止 | `novel_ai_filter_no_illustration_detail`。件数/内容切替の原SW条件は覆われる |
| K07 / SW K-7/K-10 | `acceptance.py:324–337` tagをfillするだけではreadなし、Enter→0件notice・カード0・reader引数一致。失敗語でEnter→失敗notice＋取得ボタン有効をassert | `tag_submit_only_and_zero_results`、`failure_distinguished_and_controls_reenabled`。実GETや無関係タグを全件試すことは原SWの要件に追加しない |
| K08 / SW K-3 | `acceptance.py:300–302` 「評価数」select直後に先頭カードが変わったことをIDでassert | `novel_bookmark_sort`。新小説の評価数は既存backendで明示したbookmark_count。別軸の架空値は保証しない |
| K10 / SW K-9 | `check_personal_ranking_k09_k12_browser.py:209–221` ブクマ数「500+」選択で古い一覧を破棄、明示取得でID2件になることをassert | `bookmark-requery-only`。開始条件は同一合成Vault、取得済み5作品 |
| K11 / SW K-9 | 同 `:188–194` 「1か月」選択で件数3→2、「すべて」で3へ戻ることをassert | `date-preset` |
| K12 / SW K-9 | 同 `:195–207` 作者上限「1」を入力・Tab確定、結果2件・クリアで「0」へ戻り一覧5件をassert | `per-author-limit`、`clear` |
| K09 / SW K-9＋RC #60A | 同 `:142–207,230–270` 候補節・タグ追加・含む/除外・期間・上限・保存/再訪/失敗を実assert | 個人画面の `candidate-toggle-default-open` ～ `disabled-empty-tag` は通過。ただし**RC#60Aのおすすめ別画面右click・中click・ブクマ/非表示/boost/queue・競合取消**はこのchecker範囲外なのでbを維持 |
| K13 / SW K-1 | `acceptance.py:220–224` 明示取得成功、`:330–337` 失敗後noticeとfetch有効をassert | **失敗した同じ条件を再取得して回復**した後のカード表示は未操作 |
| K14 / RC#59 | `acceptance.py:287–294` 明示more→追加1件/第2頁、終端message・more非表示をassert | **グリッド末尾の可視位置、自動継ぎ足しなし、loading中disabled、失敗時ボタン復帰、複数頁後のタブ非固着**は元RC59明示だが追加の実操作が必要 |

**ゲート分離**：以上は原機能の合成製品GUI操作と直接assertの突合せ。
Native配布・OS外部起動・実Pixiv GET・同条件Flet性能・最終独立監査/統合は
別枠の横断ゲートであり、**原K03/K04等が上記実操作で期待どおり動いたという
機能範囲の判定を取り消すための追加条件ではない**。またworker9の独立承認を
本書で代行したり、公式152歴史値を変更したりしない。
