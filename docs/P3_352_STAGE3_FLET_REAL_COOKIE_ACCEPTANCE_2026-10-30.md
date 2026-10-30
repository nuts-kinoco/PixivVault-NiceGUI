# P3-352 Stage 3 — Flet完全互換・実Cookie・障害/GUI（2026-10-30 JST）

## 範囲・厳格な判定

- 基準branch `phase11/p3-vault-cleanup-race-20261028`、Stage3 GUI開始HEAD `0970b468cfbbf0b1b9d8582380e992f2c79ad1f7`。原Flet固定HEAD `3ac3aa0d47a64785251593bcb09bb2d5f6df374b`。このStage3作業で製品source/実Flet source/元DB・画像・ZIP・Cookieの編集・Pixiv実アカウント書込みをしていない。他担当のP3-348 dirty変更は保護。
- `docs/P3_FORMAL_152_OPERATIONS_LEDGER_2026-10-29_v8.json`：固定SHA256 `487065a9a52228266d540e36d3e3b83ea34a38bdde4d1fdb54e275d725e55bb4`、152原操作・479参照。旧履歴33 complete/43 not met/76 unconfirmedは今回候補の形式PASSではない。
- 形式ゲート `artifacts/p3-352-stage3-formal-r1/result.json` は **0 PASS / 152 INCOMPLETE / exit1** (`candidate manifest missing`)。`docs/formal-152/current-candidate.json` の偽証拠埋めは禁止。以下の限定実ブラウザ/CPU/実GET結果だけから正式152 PASSへ昇格させない。
- 実通信を伴う追加実行はなし。既存の `artifacts/p3-344-real-read/read-r3/result.json`（5件の限定GET、認証済み、ランキング各50、RAM thumbnail decode、Cookie/原source不変、Pixiv書込み0、exit0）を再利用。**実作者差分DL・ZIP・試験Vaultへの保存、失効/429/通信断/中断/性能は未証明**。

## 独立原本監査・現在の差異

独立worker-3のread-only監査（編集、実GUI起動、実GETなし）は、元Fletの原操作と現実装を5件対応付けた。原ファイル・参照範囲および残る証明は以下。

| ID | 固定Flet・現在NiceGUIの観測 | 新候補の判定 |
| --- | --- | --- |
| D01 | `discovery_tab.py:1215-1233,4381-4460` の「もう一度探す」は `ui/learning_view.py:1404-1408,1515-1538` の「候補を取得」に相当。旧「未実装」は誤り。 | 失敗→利用者の明示再取得は下記合成GUIで確認。自然20round→30秒待機の時系列/全原参照は未証明。 |
| D16 | Flet `discovery_tab.py:2220-2229,2373-2386`、NiceGUI `learning_view.py:1016-1046`。`tools/check_image_races_browser.py:334-352` に404→画像再読込→decode回復・判定復帰の既存assertあり。 | 新候補の正式receipt/独立原scope受入が未結合。 |
| K13 | Flet `ranking_tab.py:1025-1071,1275-1277,1331`、NiceGUI `ui/ranking_view.py:190-232`。既存合成GUIは小説の追加ページ失敗→再押下成功。 | 原条件「第1頁失敗→明示再取得」の代替証拠とは認めない。 |
| U01 | Flet `unified_download_tab.py:194-200` は初期mode `followed`。NiceGUI `ui/managed_download_view.py:55` は `bookmark`。切替UIは存在し、旧「未実装」は失効。 | モード切替と作者DL GUIの合成受入はPASS。ただし**初期mode互換差/P2候補は未解決**。既定値変更は本人判断の対象。 |
| X07/X08 | Flet `gui.py:5500-5588` は確認後の物理move。NiceGUI `ui/duplicate_view.py:72-77,504-509` は既定copy+論理除外、物理moveは明示opt-in。 | copy既定の安全なGUI動作は合成PASS。ただし**物理move同一操作/復元receiptと元動作差は未解決**。安全策を無断で弱めない。 |

独立監査は5件の**新規確定P1は0**、U01/X07の具体的なP2互換差を識別。旧341/343の未実装分類を現コードへ無批判に流用しない。全152 IDの独立最終判定ではない。

## 今回の合成実GUI（生成作品・loopbackのみ）

### X07/X08 — コピー隔離・部分失敗・再起動

- コマンド：`S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -X utf8 tools/check_managed_quarantine_browser.py --channel chromium --output artifacts/p3-318-x07-quarantine/p3-352-stage3-x07-r1`。
- 初回実行は検証用venvに `psutil` がなく **ModuleNotFoundError / exit1、試験開始前失敗**。共有venvを変更せず、`psutil==7.1.0` を追跡外 `artifacts/p3-352-stage3-python-deps-r1/` のみに配置して `PYTHONPATH` から読ませた。出力pathは初回に作成されておらず、次実行は新たな有効な証拠。
- `result.json` SHA256 `298d3759761bf875fd41ba5b7c633ec05f7e000998db5e234f85da6365649ce9`、**success=true / exit0**。1280×720・1920×1080の初回/再起動の**4/4 PASS**、各終了0・port解放true・forced cleanup0・残留子0・ブラウザerror/外部接続0、source_before/after不変。
- 実操作は単組キャンセル/commit、複数組partial failure→失敗対象だけ再試行、元データ不変、論理除外と再起動維持、製品一覧反映、フォロー公開/非公開同期・0件復帰。**`duplicate-physical-move`を選ぶ実GUI操作は今回のcheckerにない**。物理moveをPASS扱いしない。

### U01 — 作者モード切替と合成保存

- コマンド：`S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -X utf8 tools/check_author_download_browser.py --channel chromium --output artifacts/p3-352-stage3-u01-browser-r1`。
- `result.json` SHA256 `f03b63822fa705fcb2bdaa591c931dea940e275c76ff920627f34db6f59590c9`、**success=true / exit0**、生成fixtureのみ・1280×720/1920×1080の初回/再起動**4/4 PASS**、自然終了4/4・port解放4/4・外部通信0・page/console error0・Git HEAD試験前後一致。
- 新規clientの最初の `data-target-mode=bookmark`、明示 `author` への切替・作者選択のclient隔離・切替往復保持・実行ジョブと再起動後状態・専用root保存を確認。これは**Flet既定followedの互換判定ではない**。

### D01 — 候補0→失敗→明示Retry（新しい限定checker）

- 追加checker：`tools/check_discovery_manual_retry_browser.py`。**生成した架空作品、独立Discovery DB、loopback、外部socket/DNS監査拒否、ブラウザ外部URL拒否、所有nonce、正常shutdown、before/after source hashを必須化**。現在checker SHA256 `c6279774b14ff9dd964e4958a016e37c2fb90dbbd89cdf9cf60bff12f0a29724`。
- `artifacts/p3-352-stage3-d01-retry-r1/result.json` SHA256 `7ba08a371ab8ab295a1116a667434b51c7b2ec89a3a4124d261cd0200b1e8881`、**exit1**。候補0・明示失敗は実操作で確認したが、復帰成功時の一時表示ラベルをcheckerが必須にし、実候補が入ったあと表示文言が上書きされてFAILとなった。製品sourceは変更せず、checkerのみ永続的な候補・画像decode・判定有効化を条件に修正。
- 再実行 `tools/check_discovery_manual_retry_browser.py --channel chromium --output artifacts/p3-352-stage3-d01-retry-r2`、`result.json` SHA256 `c687b012cbefd67215326208ebf664f310647dc77c2f18c3b7acd5dc572b6764`、**success=true / exit0**。候補0で自動補充停止を表示、実ボタンで失敗、次の実ボタンで候補を取得、PNG decode、4判定有効化、原source不変、正常終了、port解放、外部要求0を確認。
- **重要な制限**：既存policyの20round到達をサーバfixtureから直接注入した。20回×30秒の自然待機、複数client、実通信障害や原Flet画面そのものは実施していない。D01正式受入は依然INCOMPLETE。独立レビューの範囲と残る限界は次項に記録。
- **独立worker-4 read-only再監査を回収済み**：r1 は `initial_zero=true/explicit_failure=true/explicit_recovered=false`、r2 は3条件ともtrue、正常終了・port解放・source不変であり、失敗証跡も残っている。checker全296行を確認し、対象範囲の**新規確定P1/P2指摘なし**。ただし`/_retry/stop`によるgave_up/failed_roundsの人工注入、生成PNGのみのdecode、子孫PID全追跡なし、ソケット監査範囲の限界があり、自然20round・実通信障害・正式D01原scopeを承認するものではない。

### X07 — 物理moveを明示選択し、GUIから復元する限定受入

- 新規checker `tools/check_x07_explicit_move_browser.py` は、上記X07用の既存**合成**製品server `tools/check_managed_quarantine_browser.py --serve` を新専用fixtureで起動する。1280×720 Chromiumで作品の隔離選択→確認ダイアログの初期unchecked→明示checkbox click→物理move→保存済み実台帳 `quarantine_operations` の `mode=move, state=moved` と元ページ減少→「ごみ箱」の復元確認→元ページbyte SHA一致・台帳`state=restored`を検証。外部通信はブラウザでblock、serverではloopback以外を拒否し、所有serverの正常終了・port解放・source SHA前後不変を要求。
- `artifacts/p3-318-x07-quarantine/p3-352-stage3-x07-move-r1/result.json` SHA256 `ee9868ca8d3c37c23f36d7ab8076b436d67aa4ecc006031029d35ed507e79b65`：**exit1**、Playwright `locator.check()` はQuasar div `role=checkbox` の状態更新に失敗。serverは正常終了・port解放。
- `.../p3-352-stage3-x07-move-r2/result.json` SHA256 `4a11b83a8647c142dd694994c84620fdb7fc57c01f26f8a81154a8c48f82d05d`：通常のclickに変更後、GUIで物理moveは実施でき、fixture台帳は `mode=move,state=moved`。ただしcheckerが `/_x07/state.operation_count` を `committed` の個数と知らず `moved` も1と要求して、**checker判定のみFAIL/exit1**。この実行では復元を行う前に終了したためfixtureの生成済みデータは保管し、上書きしていない。
- 新規 `.../p3-352-stage3-x07-move-r3/result.json` SHA256 `d0da6ed02592185b1c2dd6f18a43e176d915464b1f756369f453efcf85585ae5`：実台帳の `mode=move,state=moved` を直接検査し直し、隔離前元ページ3→移動後2→復元後3、元bytes hash一致・exclude解除と `state=restored` を照合して **move_ui=true, restore_ui=true, normal_exit=true, port_released=true, source_changed=false, success=true, exit0**。checker SHA256 `e4d8a93a036342feb3bac7243fbee7c5680e290da89a23c9cf7f1d1e131a88f2`。
- **独立worker-4からP2（checker偽陽性）指摘**：r3は可視`source_pages`（論理除外）と台帳`moved`を確認したが、実体として元sourceの画像ファイルが消えていることを直接証明できない。もし内部relocateが何もせず戻る不具合があっても、UIと台帳だけで偽PASSになる。製品バグ確定ではなく**受入チェッカーの証拠不足**として認め、指摘を閉じるまではr3を物理実体証明に使わない。
- 追加後 `.../p3-352-stage3-x07-move-r4/result.json` SHA256 `99a23a497820d9387045ab3f5e7cf5b91001ce0af1866e8853478ec5b2e55b80`、**success=true / move_ui=true / restore_ui=true / normal_exit=true / port_released=true / source_changed=false / exit0**。合成元ページ `outputs/work-123-*/page-2.png` を一意に特定し、移動前ファイルSHA256をメモリ内で保持、移動後は同pathの非存在・隔離先に元SHA一致の実体が存在・durable `mode=move,state=moved`、復元後は元pathの再出現・同SHA一致・durable `state=restored`・exclusion=0を確認。SHAの値・画像実体は会話/Gitへ掲載しない。現在checker SHA256 `4a50b940d61d1a734109617a2fe7c71e6aa844e894d9bbe563c5b847e4e54686`。
- **物理moveとその実体復元の明示GUI操作は限定合格**したが、X07のFlet既定moveとNiceGUI既定copyの相違、全異常・競合・復元失敗ケース、479参照への正式連携は未達。単一合成作品・単一画面幅での成功をX07正式PASSとしない。
- **worker-4独立再レビュー**：r4の元ファイルの非存在/再出現とmove/restore先SHA一致の実体assert、fixture所有ガード、旧P2偽陽性対策をread-only確認し、**限定APPROVE・新規確定P1/P2ゼロ**。Ubuntu CI本番実行は未実施。X07正式152合格とは別の判定。

## CIへの恒久GUIチェック登録（GitHub実CIは未確認）

- `.github/workflows/ci.yml` の既存 `browser` jobに、独立生成fixtureの D01手動失敗→Retry と X07 opt-in物理move→実ファイルSHA照合→復元を追加。D01は `artifacts/browser-ci/discovery-manual-retry`、X07は checker の所有制約に従い `artifacts/p3-318-x07-quarantine/browser-ci-explicit-move` に出力し、X07の生成JSON/PNGだけを証拠artifact収集対象に追加。元のbrowserチェック・既存ゲートは削っていない。
- ローカルWindowsの該当CLIはPASSしたが、Linux Ubuntu hosted CIでの **新step実行/成功・PR headとの一致は未検証**。formal-152 jobも現候補欠落で意図的RED。CI全GREENの認定は禁止。
- 独立レビューでLinuxパス/OS分岐の静的整合を確認した。ただし既存browser job `timeout-minutes:10` に2ケースを追加した分の全体所要時間は未計測で、実CIのタイムアウトも未証明のまま。必要なら独立実測に基づき有限timeoutを見直す。

## 実Cookie保存前flightと現在の保留

worker-4はread-r2/r3の限定GET証拠とbootstrapをread-only監査。`bootstrap.py:73-77` のrootとCookieパス重複拒否、保存/ZIP/Vaultの専用CLI引数、Pixivアカウント書込みフラグ既定OFFを確認。しかし、現在使う専用root/Vaultの所有・隔離・復旧条件までは確証できなかった。**現在の実GET成功を実保存ワークフローへ拡大せず、新たなPixiv通信を実行していない**。元FletのCookieや実作品IDを表示・Git追跡しない。

### HTTP認証・失効・429等の合成障害回帰

- `tests/test_pixiv_read_client.py` はファイル先頭で明示されたoffline mock transportと生成Cookie fixtureを使用する。実Pixiv Cookie/ID/APIの参照は行わない。コマンド：`S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -X utf8 -m pytest -q --junitxml=artifacts/p3-352-stage3-http-faults-r1/result.xml tests/test_pixiv_read_client.py`。
- **187 PASS / 0 FAIL / exit0 / 5.47s**。Junit XML `artifacts/p3-352-stage3-http-faults-r1/result.xml` SHA256 `b375b5dba4fcb5be0bc9cf77ef5213fd7b4388f8c382db6a27c7b6e47d50f886`。`stdout.txt` は同じ証拠directoryに記録。
- ケースには生成Cookieの失効・不正・identity不一致、合成HTTP 401/403/404/429/500/503の安全分類、redirect/Content-Encoding/over-size拒否、通信失敗・deadline、CDN画像Cookie非送信、書込method拒否、各種不正JSON、うごイラZIPの境界検証を含む。外部通信障害を実現したわけではなく、実Pixivの429再試行や失効Cookieからの**実画面復帰**を証明したものではない。

### ZIP保存形式 — 同一合成作者から生成し再起動後の非上書きを検証

- コマンド：`S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -X utf8 tools/check_legacy_zip_export_browser.py --channel chromium --output artifacts/p3-352-stage3-legacy-zip-r1`。原Cookie・元実画像・DBを一切読まない既存の架空作者/作品fixtureを使用。
- `result.json` SHA256 `1c9b4a8bc2253f88025a346f8c102f3a8cf9ea56d68ca995286f55db7378a3d3`。**success=true、exit0、1280×720・1920×1080の初回/再起動計4/4 PASS、自然終了4/4、port解放4/4、page/console/外部要求0**。作者のNEW/UPDATED保存を経たZIPは2ページ、期待名/データbytes同一・ZIP CRC (`testzip`)・圧縮形式を検証し、再起動後非上書きとexport SHA不変を確認した。
- 合成版でのZIP保存形式と再起動復帰の限定証明にすぎず、**実Cookie由来の作品→差分DL→実ZIP→専用Vault保存**、Flet実同条件の件数・ファイル名/時間比較、既存実ZIP原本互換の全網羅は未実施。

## 次ゲート（未完は未完）

1. D01 checker限定独立監査は上記のとおり完了。正式証拠は自然20round・原Flet同条件・各原bindingの期待/操作/receipt SHA・scope reviewが揃うまでPASSにしない。
2. U01初期followed vs bookmarkとX07物理move vs copy既定は、原UX・保存形式・安全性を同条件比較し、仕様変更の本人承認が必要か分離。無断で既定を切替えない。
3. X07のopt-in物理move/実ファイル復元は上記の限定合成GUIと独立スコープレビューまで成功したが、原Flet既定動作/競合・故障網羅は未達。D16/K13原条件の新候補GUI、479原参照・実Cookie差分DL/ZIP/Vault、失効/通信断、GPU native、同条件p50/p95/max/overhead/private bytes、全体独立レビュー・GitHub CIは未受入。
4. 他担当dirtyや元Flet原本は維持。Stage3は部分証拠の記録であり、Stage2/3完成宣言・main統合を禁止する。
