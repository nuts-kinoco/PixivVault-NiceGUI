# P3-318 統合チェックポイント — 2026-10-29

Goalは未達。原152操作、原データ保全、Windows配布、同条件Flet性能、独立レビュー、CIの必須ゲートを維持する。これは限定証拠の回収記録であり、正式合格数を更新する文書ではない。

## 作業対象

- 作業ブランチ: `phase11/p3-vault-cleanup-race-20261028`
- HEAD: `2cf4a7a7286dd728e8df8125094498a3622babba`
- チェックポイント時のstatus: tracked 68 / untracked 332 / 合計400。差分を破棄していない。
- commit/push/main統合なし。原Flet・原DB・原画像・実Cookieへの書込なし。
- 実Pixiv POSTは実行していない。書込試験は合成transportと所有したloopback TLSのみ。
- 歴史的正式集計33完了／43未達／76未確認を維持。各IDの原条件と現候補の実操作を揃えるまで加点しない。

## S03 フォロー同期の学習反映

設定で同期済み0人になっても学習側が空一覧を未取得とみなし、旧seedの作者を使うP2を再現した。また、別clientの同期結果が起動時のsnapshotに反映されなかった。

- RED: `artifacts/p3-318-s03-following/discovery-red-r1/result.xml` — 5 FAIL / 1 PASS。
- 修正: 未取得はNone、取得済み0人は空tupleとして区別。学習batchのproviderは現在のreader/accountに対応する最新のローカルstoreを読み、GETを追加しない。明示refreshで得たfallbackはreader/account/rowsを一体で公開する。
- GREEN: `artifacts/p3-318-s03-following/discovery-green-r1/result.xml` — 46 PASS。
- 旧seedは削除していない。未同期時の既存fallbackは維持。
- この証拠は学習workerと限定結線のCPU受入。製品Edgeの設定同期／0人／再起動とは別に記録する。

## X07/X08 隔離の製品受入と同時更新

確認・取消、1組、複数組の部分成功、失敗対象だけの再試行、ライブラリ更新を製品shell経由で試験するcheckerを作成した。

- `edge-product-r1`: 起動前にpsutil不足で終了。server/browser未起動。専用検証venvへpsutil 7.2.2を追加。
- `artifacts/p3-318-x07-quarantine/edge-product-r2/result.json`: FAIL / 1 run / source_changed=false。batch部分成功の公開中にcatalogを観測してHTTP500。成功1件と失敗1件を記録したmanifest自体は保持。
- 同runはapp exit0/port解放後にcheckerが子の自然終了を待たずcleanupしたためforced_cleanup=1。成功へ訂正しない。
- catalogの整合性チェックを維持し、index revisionが変化した場合だけ最大3回のローカル観測を行う修正を追加。receipt、manifest、画像の検証例外は握りつぶさない。
- RED: `catalog-race-red-r1/result.xml` — 1 FAIL / 1 PASS。
- GREEN: `catalog-race-green-r1/result.xml` — 51 PASS。原本、GET回数、source page、token、revisionを確認。
- checkerは同revisionのmapping/operationsを観測し、409の観測競合のみ有限再読込する。所有PID/作成時刻を確認して子の自然終了を共通期限内で待ち、実際の残存だけをforced扱いにする。phaseをprogress.jsonlへ残す。
- 独立レビューで、近似重複の連結鎖の中間画像を隔離した後のgroup再分割が不足するP2を追加発見。修復・再受入が残る。

## GET/POSTの実IO寿命

既存GETは少量の応答が届き続けるとreadが絶対期限を超え、close後もworkerが残る経路があった。

- GET RED: `artifacts/p3-318-get-lifecycle/red-r1/result.xml` — 5 FAIL / 4 PASS。header/body × deadline/closeで、fixture cleanup前に停止しないことを確認。
- 検証済みPOSTの所有処理を`owned_https.py`へ抽出。接続前socket登録、TLS/HTTPResponse alias、絶対期限timer、cleanup gate、sticky poisonをGET/POSTで共有。
- 公開GETのURL許可、Cookie/CDN分離、HTTPエラー型、retry policyは維持。注入transportは借用しcloseしない。
- GREEN: `artifacts/p3-318-get-lifecycle/green-r1/result.xml` — 346 PASS。実loopback 17ケースは全てfixture cleanup前に完了、全worker/server threadとlistener終了。最大観測時間0.776875秒。性能比較用の数値ではない。
- 先行POST loopback 11ケースも全て1 POST、全資源終了。JUnit SHA256: `421acbd560f20c72e4d6ec69edc5dcf408837c636f8daf917387331853f58979`。
- draft manifestのreader base SHAはLF正規化値。適用前Windows実bytes SHAは`3c69c770df530c52454e3bc023e809bfb2cf5e13fcb0bfd76726818740a4806c`、LF正規化SHAは`ff75f392683b816bfc5706982c3ce313aeb8a098252b16cfa788f7d6fdaf00ac`。両者を混同しない。

## R16/R17 共通操作と製品UI

`pixiv_actions.py`を追加し、app共通の固定intent、対象別pending、不明結果の再送禁止、明示GET再確認、単行following CAS、画面取消後の送信済み結果回収を実装した。

- core初回: 22 PASS。`account-green-r1`は23 PASS。共有readerのaccount無効化/変更もGETなしで表示状態へ反映する。
- unknown/opid台帳はapp lifetimeのメモリ管理。プロセス再起動を越える永続journalの完成は主張しない。
- 独立した実NiceGUI部品試験: `ui-green-r1`7 PASS。古いページ/カード/accountのtokenを拒否、private bookmark維持、2 client pending、結果不明時の確認、client削除後のdrain。
- viewer/recommend結線の初回影響試験: 109 PASS / 1 FAIL。再描画が共通timerを削除する問題を修正し、`ui-product-r2`9 PASS。
- 独立レビューでper-card delete callbackの蓄積とaccount切替時の旧処理中notice残留を確認。限定RED→修正→再監査を進行中。
- 既存Cookie CLIのGET専用契約を維持するため、通常はreadonly、明示CLI opt-inまたはnativeの保存設定を次回起動から反映する能力ゲートを統合中。ownerを実行中に差し替えてunknownを失う変更は行わない。

## 証拠台帳

`artifacts/p3-318-integration/inventory-r2.json`へ17証拠ファイルの実SHAと153製品ソースのチェックポイントSHAを保存した。

SHA256: `85864a96665d245cd031d285611b964fef40f7370e2d4a088f0a47a85ae252d4`

このソース一覧は記録時点のhashである。限定CPU証拠をその後の変更済みソースや最終凍結候補へ無条件に継承しない。Edge r2はsourceが安定していたが試験結果は失敗のまま。

## 継続受入の追記

上記の進行中記述に対し、同日継続で次の限定ゲートを完了した。過去の失敗runは保存したまま、正式152の認定値は変更しない。

### X07/X08の候補再分割と製品Edge

- 中間画像の隔離後は残存edgeから連結成分を再構成し、比較関係のない孤立画像を候補組へ残さない。再ハッシュ・再GETは行わず、元receiptと画像を保持する。
- `components-red-r1/result.xml`: 2 FAIL → `components-green-r1/result.xml`: 35 PASS。実dHash鎖の3画像/5画像で、残った候補が再スキャンと一致することを確認。
- 独立レビュー `/root/measurement_gate_review`: この修正を含むX07/X08のcore/consumer/copy/CAS/recovery/close範囲はAPPROVED、未解決P1/P2なし。実データやWindows配布の全体承認へ拡張しない。
- `artifacts/p3-318-x07-quarantine/edge-product-r3/result.json`: 4/4 PASS、source_changed=false。SHA256 `a7cc0915387ca2fcd057489982c9cfe37f40540549f5f74e76c208e0283caae1`。
- 1280×720/1920×1080の初回・再起動。単一Edge/context/pageとloopback portを再利用。確認取消、最低1枚保持、単組隔離、複数組の部分成功、失敗対象だけの再試行、製品ライブラリ再表示を操作。
- 元4receipt/6画像は全SHA不変。合成原画像GETは初期生成6回のみ、再起動GET0。各runでserver/所有子process正常終了、port解放、forced cleanup0、外部通信0、console/page errors0。
- S03は公開/非公開の明示同期→有効な0人→画面再訪→再起動を同じ製品画面で確認。GET0で再訪/再起動、過去の作者属性は保持。学習batch反映は別の46件CPU証拠であり、このEdgeだけから主張しない。
- 生成データの画面を目視した。隔離後の候補・枚数・部分失敗の表示を確認。同期結果はDOMと保存状態で確認しているが、保存した同期スクリーンショットはscroll位置によりsummaryが画面外であり、summaryの画素証拠とは扱わない。

### R16/R17能力ゲート・画面寿命・profile復旧

- `--pixiv-cookie-file`の既存GET専用契約を維持。通常readonly、明示`--enable-pixiv-actions`またはnative profile設定の次回起動でのみaction ownerを作る。保存設定変更時に現在のownerやunknown台帳を交換しない。
- `capability-red-r1`: 27 FAIL/1 PASS → `capability-green-r1`: 67 PASS。CLI、services、core、bootstrapの範囲。
- UI timerをclient layoutへ所有させ、per-card delete callbackの蓄積を廃止。`ui-lifecycle-red-r1`: 2 FAIL → `ui-lifecycle-green-r1`: 40 PASS。
- account変更時はpending中でも旧noticeを消す。`ui-account-red-r1`: 1 FAIL → `ui-account-green-r1`: 12 PASS。
- 独立レビュー `/root/packaged_codec`: viewer/recommendの結線と共通UI部品の範囲APPROVED、指摘P2の2件を閉鎖。実API書込と製品Edgeの承認ではない。
- 設定画面はreadonly/CLI-enabledの実modeを表示し変更不可とする。native preferences modeのみ編集でき、適用が次回起動であることを保存時にも表示。
- `preferences-green-r1`: 22 PASS/1 FAIL。失敗は従来の同期E01 fixtureがNiceGUI background taskへevent loopを与えなかったため。E01の期待値を維持してasync fixtureと初期task待機へ修正し、`preferences-green-r2`: 23 PASS。
- 別の独立P2として、明示action flagで起動する既存profileがCookie等の参照先を失うとparserが終了し復旧画面へ進めないことを再現。`profile-recovery-red-r1`: 1 FAIL。
- 参照不在と検証された既存profileの復旧分岐だけでCookie前提を保留し、復旧UIでは通常services/writerを作らない。通常CLIのflag-only拒否を維持。`profile-recovery-green-r1`: 57 PASS。
- 独立レビュー `/root/measurement_gate_review`: actions/capability/services/preferences/profile復旧の限定範囲APPROVED、未解決P1/P2なし。GET transport自身とUI token部品の別レビューを混同しない。

### R16/R17の製品Edgeと保存済み作品の作者ID

- `edge-product-r1` はreadonlyの初経路成功後、保存済みviewerのactionが無効なため失敗。推薦カードのowner結線は正しかったが、管理receiptから作るArtworkが検証済みauthor_idを受け渡していなかった。metadata安全条件を緩和せず、projectionの欠落だけを修正した。
- `saved-author-red-r1/result.xml`: 2 FAIL → `saved-author-green-r1/result.xml`: 66 PASS。後者SHA256 `95fbcdaa5458fac2a39ab3017a60150dc8866d1a07318b4bcf4d7babcc571f3e`。実ApplicationServices/推薦の「保存済みを開く」から製品LibraryViewへ進む結線を確認。
- 独立レビュー `/root/packaging_review`: 検証済みreceipt identityをprojectionへ渡す限定修正をAPPROVED。
- `artifacts/p3-318-r16-r17-actions/edge-product-r2/result.json`: readonly/enabled × 1280×720/1920×1080、4/4 PASS、source_changed=false。SHA256 `2f5222a969c256eb85f16c97b0dcfae9e32e937c6f8b345efcc822fffc29fdbe`。
- enabled各経路の合成GET13/POST3、readonlyはGET0/POST0。実Pixiv GET/POSTと外部通信は0。フォロー・解除の確定、ブクマの結果不明、別clientからの明示GETによる状態確認、再POST禁止、stale page/card/account、client削除後のdrainを確認。
- 全経路で生成Cookie・元receipt/画像SHA不変、console/page errors0、通常終了・子process終了・port解放、forced cleanup0。
- 2寸法の生成画像スクリーンショットを目視しaction表示を確認。推薦画像の視覚受入とは扱わない。後続の原版照合で、fixtureだけでなく製品側にも推薦画像情報・表示の欠落があると確認した（下記の原機能修復へ継続）。
- 「閲覧専用」の文言を「おすすめの登録設定は閲覧のみ」に限定し、Pixiv action能力と混同させない。

### 最新全体回帰の中間結果

- `artifacts/p3-318-final/full-r3/result.xml`: 3566 PASS / 4 FAIL / 3 SKIP、415.85秒。469ソース/試験/toolの実行前後SHAは一致。JUnit SHA256 `0239f49baa0e21734e65d95e26ffb57635d60fe659706a28f23c33219c61ecbc`。
- 4 FAILは旧fixture: servicesを直接構築する2件のlibrary_repository属性不足、commit後crash hookが新CAS keywordを受け取らない1件、存在しないDBを許す旧--check期待1件。製品境界を弱めずfixtureを補正した。
- commit crash試験は実際の公開結果を記録し、公開が1回でreceipt/SHAが一致することを追加確認。--check試験は未存在を拒否し、所有する閉じた合成SQLiteを用意後、sqlite3.connect禁止のまま成功・bytes/mtime/sidecar不変を確認。
- `full-r3-fixture-impact-r1/result.xml`: 24 PASS、SHA256 `815f5249acea6e83cc14f073285be331ae56105ffd0917752d00d295e3a4b64d`。
- 後続のN06/トレイ/X09/V06変更を含む`full-r4`を新しい専用領域で開始。結果未確定の時点では全体GREENと呼ばない。

### N06/N07 URI登録解除時の原設定復元

- 上書きする3値の元の型・値とキー存在を専用HKCU receiptへ保存し、解除時に復元する。DefaultIcon/追加verb/追加値を保存し、receiptが無い登録には解除の変更を加えない。新adapter・再起動・upgradeでも最初の復元点を保持する。
- 初回`red-r1`: 9 FAIL/1 PASS → `green-r1`: 22 PASS。
- 独立P2: A→Bのupgrade後の途中解除が、外部によってAへ戻されたcommandを上書きできた。復元開始時に検証した現在3値をbeforeへ固定し、復元再開時には再採取しない。
- `phase-external-red-r1`: 1 FAIL → `phase-external-green-r1/result.xml`: 35 PASS、SHA256 `e493bab10539b88f96470cdcb7fdd95cd8d01e675a32d9d58d5a39239f4e93a7`。独立レビュー`/root/packaged_codec`はP2閉鎖・APPROVED。
- 実Windows checkerは生成nonce付き専用schemeだけにアクセスし、実pixivvaultキーへ触れない。強制終了は行わず、probeの正常終了または自身の有限期限を待つ。
- `native-r1`/`native-r2`はcheckerがShellExecuteの末尾slashを拒否したためFAIL。r2の生成probe argvとexit81で原因を確認。製品parserは元からoptional slashを許容する。
- checkerのみ補正し、受信raw argvを保存したうえで既存製品parserを呼んで確認。`artifacts/p3-318-protocol-restore/native-r3/result.json`: PASS、SHA256 `896d29cbc33ef9d0043ce0427d020dc3979d21be0c4ba80a38df8bff2103fe67`。
- 元の7キー・8値を型込みで完全復元、再構築adapter確認、未登録解除0write、空白入りpathのURI起動、PID/birth/nonce/script照合、exit0・全子終了・force0・専用registry全消去・scope違反0・source変更なし。
- このWindows証拠はadapterと専用Python probeの範囲。配布exeのwindowや本番登録の受入と混同しない。独立レビューがregistry前後と実結果を照合し、この範囲を承認。

### N03/S15 トレイ終了の所有と通常の閉じる確認

- 原版のトレイ「終了」は確認なしで終了し、通常×では確認を維持する。旧処理はicon停止とserver終了を先行させ、×のCancel後にwindowだけ残る経路があった。
- `artifacts/p3-318-tray-shutdown/red-r1`: 5 FAIL → `green-r1/result.xml`: 48 PASS、2.51秒。SHA256 `420bdf7e7f6603dcd7082bc57d85702205762e31cea39c2ab1e4b61b4ce3fe96`。
- 固定NiceGUI3.18.0のnative startup経由で、唯一の所有master Windowとnonceへ限定した終了methodを登録する。既存のnative method queueを使い、受付時点ではicon/serverを閉じず、実closedで完了する。
- 独自methodは全経路Noneを返し、返答queueを汚さない。起動引数の他ownerを上書きせず、拒否・例外・未応答では復帰と明示retryを保持。通常×の確認は維持する。
- 独立レビュー`/root/packaged_codec`: scoped APPROVED、残P1/P2なし。固定WinFormsの同期Close、spawn転送、応答queue契約を実runtimeコードと照合。
- `runtime-sha-r1.json`へ製品3ファイル・release runtime7ファイルと参照runtime2ファイルのraw/LF SHAを記録。証跡SHA256 `c5390356d6a6bb144b53182bf040c799b66dfd3f828161be7a2300d628fc9435`。レビュー内容のLF SHAは10/10一致。配布exeの実トレイ操作は別途必要。

### X09ごみ箱表示とV06おすすめ右クリック

- 原Fletのごみ箱は件数・容量のdialogであることを確認。専用管理領域の公開コピー数/容量と、通常一覧の除外ページ数を分けて表示する。未作成・作成済み0・読取失敗を区別し、読取だけで_prepare/GET/decode/writeは発生しない。
- 保存先はUIからpathを受け取らず、所有marker・link/reparse・許可rootを再確認して固定ごみ箱だけを開く。画像やreceiptを削除・移動せず、未完成コピーは集計しない。close/tab移動後の遅延結果は画面へ反映しない。
- おすすめ各作品の右クリックへ、既存ID検証を通った固定Pixiv URLを追加。new tabとnoopener/noreferrerを指定し、「保存済みを開く」を維持。demo等の非Pixiv identityには空menuを出さない。
- `artifacts/p3-318-x09-v06/red-r1/result.xml`: 12 FAIL/1 PASS、SHA256 `0f24a64fee6ce1ba8ffc8a94f2ad86f151b77436c839b6191948b6d4f23aab92`。欠落機能のREDであり回帰発生と呼ばない。
- `green-r1/result.xml`: 関連78 PASS、29.74秒。SHA256 `db129139810518da7b5c5dd8f0d4cbf605123e0c7580467ab74cb1bc8893992f`。独立レビュー`/root/packaging_review`: draftと関連実装の範囲APPROVED、残P1/P2なし。製品EdgeとOS保存先起動は未受入。

### full-r4の確定結果と適用範囲

- `artifacts/p3-318-final/full-r4/result.xml`: 3608 PASS / 0 FAIL / 3 SKIP、425.70秒。wrapper実測429.456965秒、exit0。484ソース・試験・toolの実行前後SHAが一致した。
- JUnit SHA256: `ebebe82db3df4be524678a6b1e3ed2ae50f9d74d2be9cfe373501003c4a8aca2`。3 SKIPはWindows symlink権限の試験で、codec/cryptoはSKIPしていない。
- この全体回帰は後述の推薦画像・L04/L07・S10修復前の基準点である。その後の製品変更を含む全体GREENとは扱わない。

### X09/V06の製品Edge確定

- `artifacts/p3-318-x07-quarantine/x09-v06-edge-r1/result.json`: 1280×720と1920×1080の2/2 PASS、source_changed=false。SHA256 `a0575f1be8df0fdb833d9dd478306db6a1429fa8a5d8676043653e8ef57c85ea`。
- ごみ箱は公開コピー1件・363bytes・除外1ページを実manifestとファイルで照合。読取失敗は0と表示せず、明示更新で回復。読込中の閉じる・タブ往復後の遅延結果拒否・選択保持を確認した。
- 元4receipt/6画像SHA不変、追加原画像GET0、推薦取得/フォロー取得0、外部通信0。2runとも通常終了exit0・全子終了・port解放・forced cleanup0・console/page errors0。
- おすすめ右クリックは実menuを開き、固定Pixiv URL・target・relを確認。外部リンクはクリックしていないためOSブラウザ起動の証拠ではない。ごみ箱のOS保存先起動も別途必要。

### 実Windows junctionの拒否

- `artifacts/p3-318-junction-native/native-r1/result.json`: PASS、source_changed=false、forced_cleanup0。生成SQLiteと所有する空フォルダへ実Windows junctionを2つ作成し、実属性1040（0x410）を確認した。
- frozen library source、E01のsource/destination、managed path、Desktop起動の各境界が拒否。SQLite connectを禁止したまま検査し、出力0・OS launcher呼出0。
- 生成DBの12288bytes、mtime、SHA `cc3c69780dc05abf528c3737dbbca605d35909e128ef1b1957d37fec1c2b738b` が不変、sidecarなし。確認済みjunctionだけを解除し、実体のDB/フォルダは保持した。symlink権限3 SKIPとは別の受入である。

### 原版との照合で確認した機能欠落と修復

- 原152のv4台帳は152IDを維持し、102IDへ原期待参照、35IDへ限定証拠を対応付けた。正式全行再認定は0、歴史的33/43/76は未変更。
- 元Fletのお勧めカードには画像と左クリックviewerがあるが、現推薦経路は画像metadataを落とし、画像部品がなく、未保存作品のviewerへ進めなかった。R16/R17のaction成功をこれらの代替にしない。
- metadata RED: `artifacts/p3-318-recommendation-visual/red-metadata-r1/result.xml`、6 FAIL/1 PASS、SHA `c604acb519e4073f1bd6f5140aa2282ce5186363f5bd6069d87dd15dc5b6e100`。任意preview metadataを取得・cache・旧SQLite・Discoveryに維持し、旧4項目cacheの未知値を保った。
- metadata GREEN: `green-metadata-r1/result.xml`、関連38 PASS、SHA `71cc14114f4cd80469a2ea698ad2f6107c3c6b8161e4d1004094beb9367517a3`。415製品/試験ソースの前後SHA不変。カード画像と未保存viewerの製品接続は継続中。
- L04/L07 RED: `artifacts/p3-318-calendar-slider/red-r1/result.xml`、13 FAIL/0 ERROR、SHA `3a6bd8a24ec13ffd2e1b89cbaba7b8555eca512064cad1f74624f47e1e0f9b27`。カレンダー・期間指定・スライダーが未実装だった。原の手入力条件と共有状態を保つ修復を統合し、限定再検証中。
- S10 RED: `artifacts/p3-318-native-save-layout/red-r1/result.xml`、6 FAIL/3 PASS/0 ERROR、SHA `ecb834266948f2945e7acfb9298e4abcd0bda1d7eb5acc8c2d63f54b4e476620`。通常profileの設定が無効で、完全再読込後もexport accountが未接続だった。設定保存はGET0、完全な明示再読込の確認済みidentityだけに一方向bindする修復を統合し、実direct/link出力と独立監査を継続中。

### L04/L07のモデル更新と遅延操作

- カレンダーの実日付・取消・期間プリセット、0〜1000/50刻みスライダー、release時だけの絞込を接続した。手入力137/1500は丸めず、1500のthumbだけ1000へ制限する。
- 初回GREENは32 PASS。その後の独立監査で、NiceGUI標準のthrottled model handlerがclear後の旧値を受け付けthumbを500へ戻すP2を再現した。旧raw値のREDに加え、同じpanel epoch内の数値変更/プリセットと新しいdragへの割込みを4ケースでRED確認した。
- slider専用subclassで標準モデルhandlerを一つのまま保ち、modelとreleaseの双方にpanel epoch/slider revisionを検証する。拒否時は現在のpreviewを保持し、新しいdragを古い確定値へ戻さない。
- `artifacts/p3-318-calendar-slider/model-gate-green-r1/result.xml`: 37 PASS、source423不変、SHA `cd814dfcc057899ecfde92c2e658af9d787b705e317e8b5fa0738b94a2b03be4`。実Edgeのカレンダー選択/ドラッグ画素と2client受入は別途進行する。

### S10の公開時点・保存元・Windows長いパス

- 通常profileで保存設定を編集でき、設定変更だけではGETしない。完全な明示bookmark再読込が現在の確認済みreader/accountに属する場合だけexport accountを一方向bindする。通常discovery初回取得は既存startup契約として別に観測し、全アプリstartup GET0とは主張しない。
- 独立P2: 画像snapshot公開直後に別clientが再本人確認/closeすると、後段の再検査が公開済み結果を失敗へ戻してaccount/cacheの対応が崩れた。最後の入口検査をadmissionとし、captured readerとaccount lockで公開/close順序を維持する。公開前のreader変更/closingは引き続き拒否する。
- `admission-red-r1`: 2 FAIL/2 PASS → `admission-green-r1/result.xml`: 4 PASS、SHA `14277fb71ad0f36551ecb9f25a3790bec7f5e7593cc98c15a12e3238d839a147`。独立監査は提示修復範囲をAPPROVED、全体承認ではない。
- 手動IDのrequest台帳があるだけでbookmark由来と誤認していた新規export admissionを修復。実bookmark会員条件に限定し、既受理intent/job/Retry/receiptは変更しない。
- `provenance-red-r1`: 1 FAIL/1 PASS → `provenance-green-r1/result.xml`: 15 PASS、SHA `054a5bd7e2e6a2bafe97527de3fca93963006e65abc98eaf713466a734001d02`。既受理ジョブはcache消失後の再起動/明示Retryでも元intentとbytesを保持した。
- S10の実保存が失敗したため生成fixtureだけで例外contextを記録。`runner-diagnostic-r3`はWinError3/errno2、263文字のpartial directoryを`managed_downloads.py`のmkdirで作れず、original GET前に停止していた。短いfixtureへ逃がさず、既存native_io_pathをmanaged保存/generation/quarantineのI/O境界に適用した。
- logical root・相対receipt・CASは維持し、lstat/reparse検査と実I/Oを同じnative spellingに統一。検証fixtureの読取だけもnative表記を用い、保存先の長さと期待bytesを変更しない。
- `artifacts/p3-318-native-save-layout/longpath-profile-green-r1/result.xml`: 13 PASS、30.18秒、source423不変、SHA `0561453d0ffe0ec930a14498f1068a4e0a4962838cd5994f75f871c6353c7b35`。通常profile→完全再読込→実static保存→direct/link出力→旧出力/CAS不変→再起動/account未推測を確認。ブラウザー操作や実アカウントの証拠ではない。
- `longpath-impact-r1`: 63 PASS/1 FAIL/1 SKIP。FAILはreparse注入がlogical/nativeのPath文字列比較で対象を外したため。注入側をlogical_pathで比較し、拒否期待を保持。`longpath-impact-r2/result.xml`: 64 PASS/1 SKIP、SHA `71a75d7132ec2f414ed72f764f8067220abc5640ad98ed22c9aaf8700dd94557`。SKIPはsymlink作成権限。新I/O境界の独立監査と実junction再受入は別途必要。

### 推薦画像・未保存viewerと独立P2

- 画像metadataに基づく可視カード画像、明示クリック/Enterから未保存複数ページ、共通原寸/zoom/pan/GIF viewerを接続した。保存済み作品は元source page mappingと検証済みpinを保持し、借用readerを閉じない。
- `artifacts/p3-318-recommendation-visual/green-product-r2/result.xml`: 55 PASS、source423不変、SHA `d25cc7875b9a408ac892ca24c0bf27949ce07bbaa72ea0d5e4e7ef16bb9fdf15`。3つの遅延離脱経路と実GIF bytes/3frame/delayを含むCPU範囲で、実Edge描画ではない。
- 独立監査P2-1: A1詳細GET待ち→B→A2待ち→A1完了でA2のpending guardを解除した。`request-owner-red-r1`は実detail GET3回対期待2回でFAIL。finallyにもrequest generation所有を要求した。
- `request-owner-green-r1/result.xml`: 8 PASS、source425不変、SHA `d6fddb3f4fb0cf5905ccfd14f556b9c171dd718a025dee8fd017c057b99fe9bb`。旧完了は新pendingを解除せず、最終選択/pin/reader/POST0を確認した。
- 独立監査P2-2: remote token発行後に同IDのmanaged tombstoneが公開されると、UI入口ensure_sourceは拒否しても既発行/media tokenは旧remote providerへGETできた。`remote-pin-exclusion-red-r1/result.xml`: 1 FAIL、SHA `23c050ec3560cc867ea85547e2ecd4113100565277146052fb419c76a9683442`。providerのI/O前後へsource guardを届ける修復中。現時点では推薦scopeのAPPROVEDや画像実受入完了を主張しない。

### 推薦P2の閉鎖と実Edgeの残件

- `remote-pin-exclusion-red-r2/result.xml` は発行済みURL、I/O中の除外、warm cacheの3件でFAIL。新 `RecommendationRemoteImages` が現在のmanaged source/tombstoneをI/O前後で検証し、旧remote bytesを返さない。既存ranking経路・借用reader所有は変更しない。
- `artifacts/p3-318-recommendation-visual/remote-pin-exclusion-green-r1/result.xml`: 関連32 PASS、source425不変、SHA `1ac3faed1fc25fc661c68d9cc594d4f909c14e438b58a53194dc7ee33d9279ed`。request所有P2と合わせ、独立監査はこの修復範囲をAPPROVED（P1=0/P2=0）。全体・Windows配布・画像実受入の承認ではない。
- 実Edge `edge-product-r1/result.json` はカードサイズ切替後の旧DOM参照でFAIL、SHA `688aab57716b6801a5b7739f9a7599c45d0e3b18c3f0576fe57b9ef71a6a5747`。checkerが新thumb DOMへの切替を待つよう修正した。
- `edge-product-r2/result.json` は通常画像、大中小、6→12→18→6、保存済みsource page対応、未保存複数ページ/原寸/zoom/panまで進み、GIFの実3色描画確認でFAIL、SHA `7f668f3bf7fc683fc5f4249128e3385b540b704f92c729c5ff8924579d50f9af`。どちらもsource不変、通常exit0、全観測server子終了、port解放、強制終了0。これを4run合格とは扱わない。
- `gif-diagnostic-r1/1280x720/initial/gif-diagnostic.json`（SHA `af46ea4fad9e02dcd62b0ec31ed7c5fa7bbde18294811db0a32aa9c1178b247e`）で実DOM GIF 3552bytes/loop0/3frame/40・90・170msを確認。前面化前の撮影はstable待ちtimeout、前面化後は同一src/同一nodeで286.887ms内に青・赤・緑を実PNGで取得した。両pageのvisibilityStateはvisibleで、背景停止が原因とは断定しない。
- 診断全体は前半の観測timeoutを含むFAILのまま保存（result SHA `5448aa8fcf874ff74b5f0ac10b2867983ce2dda5a878897db04585832d95f3d6`）。通常checkerは所有操作pageの前面化、固定node撮影、途中sampleの逐次保存を追加し、新しい4runを進める。製品GIFを差し替えたり停止フレームを合格にしたりしない。

### 実Windows経路と通常profile受入

- 長いパス修復のmanaged3ファイルは独立読取監査APPROVED（P1=0/P2=0）。`artifacts/p3-318-junction-native/native-r2/result.json` は実junction/hardlinkの7境界拒否、target bytes/mtime/nlink保持、確認済みaliasだけ解除、4source不変、force0でPASS。SHA `2c2dabf6789e13fb6f6cff4dd0255569e850684539997e018f9841c42dd868bd`。
- S10 `edge-normal-profile-r1` は生成GET fixtureが `/ajax/illust/701` の詳細応答を持たず、保存の本人/作品再確認でFAIL。結果SHA `0ce083216b9cac58f3ae60c3941727d3fbc6877bbc5f9b4216d682c547722bbc`。同一ID/作者/1pageの詳細GETのみをfixtureへ追加した。
- `edge-normal-profile-r2` は実static保存、direct互換出力とSHA照合まで成功したが、後続link設定時のGET0比較でFAIL。後に、初回bookmark読取でaccountが先に確定し、続けて明示した再読込の完了前に次phaseへ進んだcheckerの待機不足と確認した。製品の保存後GETではなく、設定GET0条件も緩和していない。双方通常exit0、全観測server子終了、port解放、強制終了0、source不変。

### 実操作で新しく確認した保持の欠落

- L04/L07 `artifacts/p3-318-calendar-slider/edge-product-r1/ui/result.json`（SHA `9fcb3028a43928cf7c4407bfb260a89cba081b195a4597ceea9130ebd886eced`）では閏日/Cancel/from-to/期間/共有、slider押下中不変とrelease反映、手入力137/1500まで通過した。別タブから復帰するとnumberのclient valueが `[object Object]` となって空欄になるためFAIL。NiceGUIのloopback=falseと追加epoch envelopeイベントの衝突を修復中。CPU37 PASSだけで表示保持を認定しない。
- owned wrapper結果SHA `743449ad63bca96c83656ac7cc4af3ce192ffcde9b7674c45f8edaa7953f4299`。ASGI endpoint PIDはredirector rootの実子であることをnonce/PID出生で確認。server exit0/checker exit1、serverとEdgeを含む全観測子は終了、port解放、force0。操作失敗は成功へ変換しない。
- A02は原同作者cache再訪時の表示保持に対し、候補が毎回GalleryStateを作り直して展開100件を50件へ戻すP2を静的照合で確認。別作者/更新の境界と遅着拒否を維持する限定修復を進める。

### 残る原期待参照の補完

- `artifacts/p3-318-formal/source-specification-r3/remaining16-original-expectations.json` に16ID/24source/87参照を保存しraw SHAを全結合。SHA `c29aa4591dbf1fc1d3cdfac07c1a8bc4b3033cd304aad4e10a24eae028423260`。元Flet・採用済み移植条件・未追跡index・既存dirty原文書を区別する。
- T01のindex「70作者」とPhase9記録「70作品」、V07/V08の元仕様対象外と移植必須採用等の差は残し、勝手に緩和しない。原source hash inventory25本＋補完3本と候補契約5本を読取で採取し、原本変更0。残34IDの参照を独立補完中で、正式152加点は0。

### 通常profile・推薦・フィルターの実Edge閉鎖

- S10 `artifacts/p3-318-native-save-layout/edge-normal-profile-r3/result.json`: 2寸法×初回/再起動の4/4 PASS。SHA `c9f5c0b2a67568557f339ca0737521ce0326ade639e24274a268b773f6d64e37`。実UIのbookmark読込完了と保存受付完了を待ち、direct/link設定のみのGET0を保持した。合成701の実出力PNGを両配置で開き、SHA `ee23be0a4515f404e5819a31761d7e75043daf2f3a73b3338b2c7f20c9a6d11c` が一致。旧direct出力/receipt/CASを保持し、再起動で無断再保存しない。各exit0、全観測server子0、port解放、force0、source不変。通常discovery起動GETは別に記録し、アプリ全体startup GET0とはしない。
- 推薦 `edge-product-r3` は実同一node GIFの3色変化を275.213msで捕捉した後、タブ復帰時の旧カードDOM参照でFAIL。製品の非同期再描画が終わり、新thumb nodeが接続されたことを待つようcheckerだけを修正した。失敗結果は保存する。
- 推薦 `artifacts/p3-318-recommendation-visual/edge-product-r4/result.json`: 4/4 PASS、SHA `416bb703f7c84dd232219e13a02d250032e040b47473353160efda629c63c3dd`。大中小の実画像、6→12→18→6、保存済みsource page、未保存複数page/選択page原寸/zoom/pan、実GIF3frame、作品/タブ移動後の遅着拒否。各exit0/全観測server子0/port解放/force0、source/helper不変、外部通信・実Pixiv GET/POST0。配布版・Flet比較の代替ではない。
- フィルターP2はNiceGUIの既定raw modelと追加envelope modelの二重登録が原因。各要素の本来のVALUE_PROPへ一つのguardを構築し、LOOPBACK=Trueとprimitive再同期、Input内部値の公開updateValueで修復した。vendorやlistener表は変更していない。
- `setting-model-red-r1/result.xml` 8 FAIL/0 ERROR（SHA `29130fd5f673a300a02aafd38c85a09a943acbc36e73dee78b8a2cbfb92d84db`）→ `setting-model-green-r1/result.xml` 関連45 PASS/0 FAIL/0 SKIP、source426不変、SHA `09248f244642e0d2757454f3bb838da49ccd326c04cdd2f1f0a81af8902a347f`。限定独立静的承認済み。
- L04/L07 `artifacts/p3-318-calendar-slider/edge-product-r2/result.json` SHA `f492cdfefcda697d106f604027b4af968371328e8ab4672ccf0f0c171c6d41e5`、UI結果SHA `0792130f350c21079fb3ed332438c34e3f246edf55d7c2aa04b84c71136a67f3`。2寸法＋2独立client PASS。閏日/Cancel/期間、手打ちfrom-to/AI、137/1500のタブ・共有保持、drag hold不変/release反映を実確認。server/checker/全観測Edge子exit0、nonce/PID出生確認、port解放、force0、source/helper不変。代表PNGも目視した。

### A02再訪と旧Retryクリックの修復

- 同作者再訪の展開100→50を実CPUで再現し、同一cache tuple・生存pin・GalleryState・DOMの再利用へ変更。別作者/差替cacheは再構築し、旧GET taskが新cacheを上書きしない。`green-r1` 10 PASS後、差替cache・旧GET応答の2境界も上記45 PASSに含めた。実スクロールはCPUから認定しない。
- 独立監査で、旧作者91のRetry clickが後の作者92へ作用する別P2を確認。実登録listener配送の `retry-click-red-r1/result.xml` は2 FAIL/0 ERROR、SHA `f1b8c7fb9aad97317414fd1caa0e8a6fbac31e3762d5d7057607ffe56b4a51eb`。click時author/epochを固定し、visible/retry表示/closed/closing/同一作者/同一世代を確認するguardを追加した。
- `artifacts/p3-318-author-revisit/retry-click-green-r1/result.xml`: author全14 PASS、source426不変、SHA `de651001348b29ae36a6f1c0b08ed8d7bab9533f1909fa9c457188a0d198cc53`。別担当の限定独立再監査はAPPROVED、P1/P2残0。実Edgeは原50→100/検索/スクロール/再訪と少数作者・有効Retryを既存fixtureで受ける準備中。

### 正式152の全ID出典結合

- 残34ID/13source/144参照を `artifacts/p3-318-formal/source-specification-r4/remaining34-original-expectations.json` に保存。223813 bytes、SHA `470c18d67916f98ac0b9912976598564a7b03d5522b7a9a378c1d0b93d205373`。原ID/操作/行番号、raw SHA/HEAD/LF差を照合し、原物理移動と現readonly境界、queue検索・Retry等の原資料差を残した。
- `docs/P3_318_FORMAL_152_OPERATIONS_LEDGER_2026-10-29_v5.json`: 679405 bytes、SHA `50e6feb7976ea2321f44bc830e25b9271e737229dc00b712c07c16d7708d36e4`。152 IDすべてに参照を結合、限定証拠40ID。元ID順/操作/歴史値/candidate status/still_missingを維持し、18個の参照・後続証拠ファイルをSHA検証した。
- 正式33/43/76と現候補full-scope認定0は不変。出典が埋まったことを受入合格としない。T01は70作者×各1作品の実UI→50受付/20保留→70保存/再起動GET0を準備し、過去の単一作者70作品を流用しない。
- 最新修復後の全CPU回帰を `artifacts/p3-318-final/full-r5` で実行。確定結果と残障害は以下に記録する。3608 PASSの旧full-r4は現候補の結果ではない。

### full-r5の確定結果と長い保存先パス

- `artifacts/p3-318-final/full-r5/result.xml`: 3674 PASS / 6 FAIL / 3 SKIP、476.60秒。src/testsのpy/jsとpyprojectの426ファイルは実行前後不変。SHA `aeae1b0e5f65930f4db594c4ce41a6e7572b3ce6b8ee623ef2bfcaa53c62daad`。合格とは扱わない。
- 4件は故障注入側の不整合。ugoiraのlink/reparse注入は論理パスとWindows native IO表記を同一化して実際に注入されたこともassertし、novelのPOSIX lock分岐はmodule内のosだけを代理化してpathlib等の実Windows判定を維持した。拒否条件は変更していない。`artifacts/p3-318-full-r5-repairs/native-path-injection-r1/result.xml`: 106 PASS / 1 SKIP、SHA `de1ad7195da73f053ab165555a3106c0c5063a8974c7a4146085fe7bd2195b1e`。
- exportの1件は新規projectionに必要なbookmark候補集合をfixtureが与えていなかった。候補なし0件、明示候補で2件、候補消失後の再起動でも受理済みjob/receipt/outputを保持する試験へ補正。製品のprovenance gateは変更していない。
- 残る1件は実製品障害。長い保存先でJobStoreのmkdir/lock/SQLite接続が通常表記を使っていた。論理保存先・絶対パス要件を保持し、全親lstat/reparse/hardlink検査と実IOにnative表記を使う修正を適用した。新deep regressionは同一保存先、二重owner拒否、同job/operation/receipt再開、runner再実行0を検証する。
- `artifacts/p3-318-job-path/red-r1` はcatalog更新によるpatch拒否の後に未存在testを指定した0test/exit4の実行手順失敗であり、機能REDに数えない。適用確認後の `red-r2/result.xml` はWinError206の1 FAIL、SHA `af21e8989b795e5b7eba81ffd54157b4e2dcc10492970913120477458eda888f`。
- `artifacts/p3-318-job-path/green-r1/result.xml` は54 PASS / 1 FAIL、source426不変、SHA `343c7dc7ae31e0b9c56418a64eaa35d6d10891774314744e5f81a2526373582d`。JobStoreを通過した先のAuthorMediaService._dbで通常表記のsqlite3.connectが長いパスを開けず、修復を継続中。ディレクトリ名greenは全合格を意味しない。
- `/root/packaged_codec` はJobStore製品差分とexport試験補正を限定静的APPROVED（P1/P2=0）。実Windows結果や最新全体回帰の承認とは区別する。3 SKIPはWindows symlink権限で、codec/cryptoはSKIPしていない。

### A02実画面の失敗観測とT01の準備

- A02 `edge-product-r1` は操作失敗位置の記録が不足しており原因未確定のFAIL。`edge-product-r2` は展開100/中/scroll575まで進み、575番のimg complete=false/natural0が5秒内に解消せずFAIL。console/page/external error0、正常server終了・全観測子0・port解放・force0。CPU成功から画像実受入を推定しない。
- opt-in fixtureに限って元catalogの103枚の同じ640px view bytesを準備し、32MiB上限のimmutable mapへ固定。実provider/resize/media route/browser decodeと5秒gateは維持した。reader入口/出口と575番browser request/responseの観測を追加し、cold性能の証拠には使わない。tool-only変更は独立静的APPROVED。
- `edge-product-r3` は103枚/1,911,731 bytesを準備し、実要求100枚は返却SHA一致、failed/active/drop0。575番の192px画像はdecode済みで5秒gateを通過した。次のprimary-revisitで、同じtest-idを持つ別の隠れたviewerをcheckerのfirst locatorが参照してFAIL。実画面には元501のviewerが描画されていることを保存PNGで確認した。元viewerのDOM IDと作品IDを固定して操作するようcheckerだけを修正し、新runを開始。r1/r2/r3は上書きしない。
- T01は70作者×各1作品/2画像、初回50受付と20保留、全70作者/作品と140画像のreceipt/SHA、再起動GET0を実UIで確認するowned wrapperを準備した。旧1作者70作品の試験は代用しない。fixture/inspection/wrapperの限定独立レビュー済み、実行結果は別途記録する。
- native基本/codec checkerへ `--no-force-cleanup` を追加しhelp exit0を確認。今後の配布受入では失敗時もterminate/killせず、所有PID出生・残存子・app/CDP port・正常終了の未証明をそのままFAIL記録する。過去強制終了runを成功に変更しない。

### 長いパスの限定閉鎖と共有作業ツリーの観測

- AuthorMediaServiceのSQLite接続にも既存native_io_pathを適用し、logical root・sidecar入場検査・transactionを維持した。`artifacts/p3-318-job-path/green-r2/result.xml`: 91 PASS、SHA `4fb04e944908139bfd78787f0fa77a9b64021fb55ca43f4e7f1fa49d96ddd9cb`。深い実Windows保存先でsettings CAS、作者enqueue、operation/job/fingerprint、receipt3PNGと再起動無GETを確認。両製品修正は限定独立APPROVED。
- この91件の実行前後では、収集対象外の `tests/test_freeze_windows_candidate.py` が別更新され、global source_changed=trueである。値をfalseへ変更しない。`scope-verification-r1.json` で製品全体と実際の91件対象は不変と確認した。未知のfreeze tool/testは当担当の変更ではなく、そのまま保持し、候補凍結・配布へ使用/包含しない。
- 未帰属freeze helperには別途、親移動を含むlexical pathの出力境界と列挙エラー時の欠落にP1/P2があるため、使用禁止を維持する。既存の明示的owned固定先・全SHA検証による凍結手順を使う。最新全回帰は別の不変snapshotで確認する。

### A02とT01の実Edge閉鎖

- A02 `artifacts/p3-318-author-revisit/edge-product-r4/result.json`: 2/2 PASS、SHA `350ce57273e8c7d101ad7c196a5f86cb54a15e94a8a77def3348b01b8ace0b9f`。同作者の50→100、検索、中サイズ、100件の同じDOMとscrollを再訪で保持。primary GET1、少数作者は失敗＋明示RetryのGET2で3/3、再訪追加GET0。103枚の同じ元fixture bytes/SHAを確認。prepared warm fixtureでありcold性能ではない。
- 1280側UI SHA `d9cfd3f573987cd44ab71cd9f0c571cd19b23c825f2448402c4a297234b467c3`、1920側 `80e1bc5ed1bf0f396f34da40f5635b88fd1879e17e161427e837e0d5c06a41ef`。代表PNGも目視した。source/helpers不変、page/console/external error0、server/checkerと全観測Edge/server子正常終了、port解放、force0。限定独立レビュー済み。
- T01 `artifacts/p3-318-scheduler-70-authors/edge-r1/result.json`: 初回/再起動PASS、SHA `e7e19dcab3013122ff3cde024a59f5667c1ef754ba3a47abf4b44bfe78d4b1d4`。実UIの今すぐチェックで70作者×各1作品/2ページ、初回50受付/20保留から70保存、70世代/140PNGのSHA/画素、再起動manifest一致とGET0。`summary-r1.json` に平坦な件数照合を保存した。
- T01 browser結果SHA `829c732cf05f3f93852b41daab827418d4e0eba6f83091d277d02ed1d27e50e8`。最終deferred台帳は0。UIの最終履歴行は第2回受付当時の20受付/49保留を表示しており、現在の保留0をUIが表示した証拠とはしない。外部/実Pixiv通信0、source/helpers不変、正常全観測子終了/port解放/force0。元お気に入り作者全件や通知の全範囲受入とは別である。

### S16の単位別入力と値保持

- 原期待に従い、学習cacheのbytes/countで選んだ上限入力と対応する利用不可理由だけを表示する。非表示側の値は保持し、保存/CASを変更しない。`red-r1/result.xml`: 3 FAIL、SHA `4d0085b74a16bb8201c0d9ebffa7460e8fc432d8d0b4ff2ed60e6e7fcb5afb01` → `green-r1/result.xml`: 関連25 PASS、SHA `f11de1d155ef051e0b02ff12ea9538cddc0eef26c75cdbbcf42c93b562278d00`。限定独立APPROVED。
- `artifacts/p3-318-s16-cache-units/edge-r1/result.json`: 1280/1920×初回/再起動の4/4 PASS、SHA `61dd182803008c219a36904d02c11b05366ef3306a20f5b7f3597a69bf385d89`。MB2048/枚数200を切替・保存・未保存1024のdiscard・再読込・再起動で保持し、設定操作GET0/job0。fixture起動のdiscovery生成GET5は別記録。PNGも目視した。
- source/helpers不変、各server exit0、全観測server子0/nonce所有確認/port解放/force0。全Edgeプロセス木の認定には広げない。この受入は入力/保持であり、次の先読み判定からのFIFO消費/eviction全条件は別途照合する。

### W01の原右クリック入口とL01作者profile

- 作者カード・ブクマカードへ原『似た絵を探す』を接続。作者は代表p1に埋込があれば使用し、なければ同作者順の最初の埋込p1、なければ代表を使って理由を表示する。表示代表は変更しない。全作者/全page候補、composition優先・queryなし時style fallback、top30/0.50/同作品除外を保持する。
- 非保存ブクマは保存済み埋込不足の理由を表示し、remote詳細/画像取得や保存を始めない。state/provider/session/fetch epochを固定し、タブ/再読込/条件変更でinvalidate、await後・Retry/選択にも照合する。context menuの直接親は画像も含む外カードである。限定独立APPROVED。
- `red-r1` は9 FAILのうち8件が不足機能、1件はfixtureの未存在service hook。`red-r2` はmanaged未設定のjobs=Noneにsnapshotを要求したfixture障害。修正後 `red-r3/result.xml` は実context menu不足の1 FAIL、SHA `c74d54310d3feff4d1be38bc93d6c8cea3886b57be47a67dd7aad7f502f63dc2`。失敗記録を残す。
- `green-r1` は41 PASS/2 FAIL。旧element削除後にlistenerを取り出すtest手順の誤りを、削除前に実登録callbackを捕捉して後から同じeventで配送する形へ補正した。製品guardや拒否期待は緩めていない。`artifacts/p3-318-w01-context-entries/green-r2/result.xml`: 43 PASS、source431不変、SHA `5a15d71cd7e7799c89750ad20daf247682ff8bc68b8a3d1c4b8263c383bb7505`。画像上の実右クリックと描画は専用Edge受入を準備中。
- 原 `library_tab.py:909–915/1258–1263` にある作者profileアイコンの常時disabledを修復。既存pixiv_urlで正数ASCII作者IDだけを固定HTTPS users URLへ変換し、別tab/noopener/noreferrer、無効IDは理由付きdisabled。画像本体の作者openとは兄弟要素で、追加callback/GET/POSTなし。限定独立APPROVED。L01補助として扱い、S22/V06/A01の代用やOS navigation成功とはしない。

### 拡張native受入の準備と正式台帳v7

- `tools/check_native_extension_window.py` の最終r2 SHA `854693baa9feab8723ad379d5124dfc9bd0ccde0b13e0a649e8af3a94275327f` を実ファイルで確認、help exit0。入力/出力alias重複の旧P1を修復し、専用private出力、設定UIから通知OFF、POST前の1回/不明結果gate、所有window正常終了を限定独立APPROVED。実新exeでの実行は未実施。
- 既存の許可済み一作品GET受入の報告SHAを `artifacts/p3-318-native-extension-checker/source-evidence-r1.json` に固定した。Cookie/作品ID/本文/実画像は出力していない。初回FAILを保存し、旧exe結果を新候補へ継承しない。新checkerの入口で現receiptと画像SHA/静止作品/全年齢/1〜3ページを再検証する。
- 既存台帳v6を上書きせず回収。別更新のN09/N10は合成loopback＋stub receiptに限る45 PASSで、その3参照SHAを再照合した。実拡張/native/本物managed receiptの認定には広げない。
- `docs/P3_318_FORMAL_152_OPERATIONS_LEDGER_2026-10-29_v7.json`: 691668 bytes、SHA `abac61f96accbc52093561061c029f5ce082260dba5a416dcac17a51d87aacd8`。A02/T01/S16の実操作とsource-beforeを追加し、152 ID/順序/原操作/歴史値/still_missingを維持。限定証拠45 ID、正式33/43/76と現候補全範囲認定0は不変。
- 現修復を含む全CPU回帰を `artifacts/p3-318-final/full-r6` で開始した。完了前に結果を推定しない。旧full-r4/r5の合否はそのsource時点のまま保存する。

## 残る必須作業

W01実Edge、全152原操作の全範囲照合と残る元機能の接続、最新全体回帰の回収、実Cookie GETと旧凍結DB/ZIPの保全、最新Windows再凍結と別配布コピー受入、原Fletと同条件の描画・性能、別PC/DPI条件、配布exeのOSトレイ/通知/URI/拡張導線、最終独立監査とCI/Git統合が残る。A02/T01/S16の限定実Edge成功を全原機能・最新nativeの成功へ拡張しない。先行全回帰、過去のnative配布、旧Flet比較値を現候補の完成根拠にしない。


## 上位互換方針での継続受入

ユーザーから「それも含めた上位互換的移植」「やり様は任せる」との継続指示を受けた。元拡張のCookie同期・媒体自動判別・保存フォルダ導線を含め、原操作・設定・保存データを維持して実運用まで進める。元データ書込禁止、実Cookie GETのみ、実アカウントPOST禁止、nana7mi別承認の境界は変更しない。

### 全回帰full-r6の完了と固定候補への切替

- `artifacts/p3-318-final/full-r6/result.xml`: 3704 PASS / 0 FAIL / 3 SKIP、467.99秒。SHA `3f30e2a766bdb08e02b6f14ff62e08ae95cc629ca706d9c91ef1f49cef3a7cff`。3 SKIPはWindows symlink権限によるもの。
- ただし実行中に `preferences.py` と2件の追加試験が別更新され、source_changed=true、431→433ファイルだった。試験中のrootによる対象ソース編集はない。この全回帰を同一ソースの最終合格とは扱わない。
- 別更新は削除せず実読した。Preferencesのreparse境界強化と追加試験を限定確認した `preferences-concurrent-change-r1/result.xml` は22 PASS、source433不変、SHA `7c32c1e5502b8ef23967daf1f8c08e5d8ab786e7523851a841281102ba9efda7`。
- 最終回帰は、製品と採用済み検証入力をそれぞれ固定したsnapshotで行う。同じ製品bytesからWindows配布を作り、共有ツリーへの後続変更を旧結果へ継承しない。未帰属のfreeze helper/testは変更も採用もしていない。

### W01の実Edge受入

- `artifacts/p3-318-w01-context-entries/edge-r1/result.json` はauthor代表/fallback成功後、ブクマ検索欄のchecker locator不一致でFAIL。SHA `d003332e5baaeff4e2cec31d76d950175028e829808fa76ae1f3e10acc3c9dd3`。実input自体にtestidが付く構造へcheckerだけを修正し、失敗runを保持した。
- `edge-r2/result.json`: 1280/1920の2/2 PASS、SHA `c0cbaf5458dfcae85657226ba8d9b21c2728e58d41b2f4e2e02ad4b52fdcd645`。実画像上の右クリック、作者代表p1/同作者fallback style、ブクマ選択保持、結果p2のdecode、未保存理由、検索中Close/tab移動後の遅い結果破棄を確認。実検索10回、追加外部GET/job/console/page/foreign-requestエラー0。
- UI結果SHA `8e083f773cbd4b4462793cc2f65b6be16d786de024e9cf8b5bd04b3d72fd58dc`。生成DBのSHA/mtime不変・sidecar0。source/helpers不変、server/checker exit0、観測したserver2/checker15プロセス全終了、port解放、forced0。
- 作者profile補助はcanonical href/無効ID無効化/画像左クリック保持まで確認。実OS browser起動は別ゲート。PNGはQuasar遷移中の描画を含むため、静止後の視覚品質をこの画像だけで認定しない。

### L18保存フォルダのヘッダー入口

- 起動時に検証したmarked storageと実CLI媒体rootを使用し、設定変更後の古いクリック、未設定、所有不一致、client終了、資源close競合を拒否する。単にallowed_rootsの先頭や保存親全体を開かない。OS openはUI thread外で一回、元領域へのmkdir/移動/改名なし。
- `artifacts/p3-318-saved-folder-header/red-r1/result.xml`: 12 FAIL、SHA `8b90aaa1d2dad9bd808b05ae5eb87b6ebcf0b441bbb371a4cfba339e4e3aff05`。製品適用後 `green-r1/result.xml`: 12 PASS、source436不変、SHA `d9593d8b58c251c290c2e32e6bedd9060ab3d64ca7a92ed755d6a6f9976e4b53`。
- 関連 `impact-r1` は76 PASS/1 FAIL。認証取消試験の旧SimpleNamespaceがsnapshot APIを持たないためで、実NativeProfileStoreを使うfixtureへ修正し、取消/reader close/二重close抑止の期待は保持した。
- `impact-r2/result.xml`: 77 PASS、source436不変、SHA `c78ca10d8eff3b9ade80bbdb8fcd84fe7df741c611edb0321bf767e7f158591f`。この証拠は生成パスとOS launcher注入のCPU受入であり、最新配布の実Explorer起動を代替しない。

### N09省略媒体の自動判別

- 元拡張の明示false/trueと既存direct callerの既定falseは維持し、workの媒体flag省略だけをautoとして扱う。本人確認とは分けた作品metadata GETのHTTP404だけで小説候補へ進み、認証/一時障害/不正応答では媒体を切り替えない。
- 媒体判定は既存owned bindings DBへchild受理前に一度固定し、再起動・明示Retry・child受理直後の中断でも既存operation/child key/receiptを維持する。限定独立レビューAPPROVED。
- `artifacts/p3-318-extension-autodetect/red-r1/result.xml`: 13 FAIL/3 PASS、source437不変、SHA `23376b13e2ed721c019b2f6a3c9eac8681e48e504ac15c8ff2f54a3c7ab015cf`。
- 製品適用と元契約に合う省略flagの旧期待修正後、`green-r1/result.xml`: 関連61 PASS、source437不変、SHA `93f4b44716e1e6f97294f105f7fe6713a4217a2671a4f65662a6a166a9914194`。
- actual reader＋生成GET transport、実Novel exporter/receipt、静止child経路fixtureの限定受入。実installed extensionや最新nativeでの実静止保存は未認定。

### 次に閉じる範囲

Cookie同期は原 `/api/cookie/sync` の契約に基づき、専用credential世代・profile CAS・短時間受信と稼働中reader切替待ちの分離を実装中。保存成功と現在認証成功を分け、queued/pausedがあるだけで永久待ちにしない。旧remote結果と画像pinは世代で失効させ、local保存pin、受理済みjob/receipt、writer unknown台帳を保持する。S01の有効/期限間近/期限切れ/未認証の原表示もここへ接続する。

原152全範囲の照合、先読み上限の実consumer確認、最新固定全回帰、新配布/native/OS操作、承認済みGET実利用と凍結DB/ZIP保全、同条件Flet比較、別PC/DPI、最終独立監査/CI/Git統合は引き続き必須。正式33/43/76と現候補全範囲認定0は変更していない。Goal未達、継続する。

