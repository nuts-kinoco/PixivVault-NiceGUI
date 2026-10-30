# 正式152操作 v8 — 原文転記と候補別証拠の残件

この更新は受入台帳の証拠結合であり、製品完成・正式152合格の宣言ではない。原152ID・操作名・公式集計を保持し、新規の正式認定は0件。機能未実装と、実装済みだが現候補に証拠を結べていない状態を区別する。

## 1. 集計と原文の扱い

| 項目 | v8で扱う数・意味 |
|---|---|
| 公式の歴史集計 | 完了33 / 未達43 / 未確認76 = 152。変更しない |
| 原票行集計 | 完30 / 未達45 / 未確認76 / 原票誤記1。公式との差を推測修正しない |
| 現候補の全条件再認定 | 0/152。全152行は not-yet-fully-assessed |
| 原参照 | 479参照、34ファイル。原版ソース/仕様30、既存採用契約4 |
| 逐語確認 | 246参照。原票の観測欄・実作品情報は新規転記しない |
| expected_fullへの転記 | 100操作。v7記載参照一式の逐語転記で、原indexの全specまで網羅した認定ではない |
| expected_full未記入 | 52操作。コード/既存要約から完全な原期待を推測せず、理由・実原文SHA・行範囲を保持 |
| start_condition | 設定画面の原文入口を15操作へ転記。他137操作は明示理由付きnull |
| 開始条件の限界 | 転記15件も入口だけ。fixture/認証/初期値を含む完全な開始状態は原文未定義 |
| partial証拠索引 | 旧45IDを保持し、追加proof対象とのunionは52ID。新たな正式PASSではない |

partial索引の追加7IDは L13 / R01b / S05 / S06 / T02 / V01 / W01。9個の追加proofノードは限定した観測を記録する。全scope認定の代用にはしない。

原文未確定の52操作は次のとおり。

L04 / L07 / U10 / U11 / U15 / U16 / Q01 / Q02 / Q03 / Q07 / Q08 / Q09 / Q10 / Q11 / Q12 / Q13 / Q14 / Q15 / Q17 / Q18 / Q19 / Q20 / X01 / X02 / X03 / X04 / X06 / X07 / X08 / X09 / H01 / S16 / S17 / S18 / S19 / E01 / A02 / V07 / V08 / T01 / T02 / M01 / M04 / M06 / M07 / M10 / N09 / Z01 / Z02 / Z03 / Z04 / Z05。

W01のように原indexに別仕様書の参照がある操作では、v7に列挙されたRC期待を逐語転記できても、原全範囲が確定したとは扱わない。

## 2. 候補の分離

今回の成功証拠の候補は artifacts/p3-321-native-gpu/frozen-01。

- 固定169ファイル集合SHA: 1f7467974c52765ef718ac87492b5963df1411a047f72f9e39684b39a26e9a05
- manifest SHA: 6f893ad53b602dfabf37e73ba52a4f938e93ee27f6d8af063bfdd5c0a83c146e
- コピー先exe SHA: b9c9e73520192ee8c67c05e6d5678c1024ff5f9620640739c5a3f0e2fc11adc3
- コピー2523ファイルの既存一致記録: artifacts/p3-321-native-gpu/standalone-copy-r1/result.json

旧top-levelの candidate318 checkpoint と source SHA は歴史として残す。v8で結ぶ候補は candidate_bindings_v8 を参照する。

**candidate324は別候補で、v8の成功証拠を継承しない。** rootが確認した artifacts/p3-324-gpu-review-fixes/full-r1/run.json は3901 PASS / 7 FAIL / 4 SKIP、3912件、exit1、source前後541件不変。JUnit SHAは e3dbce0d615fd80f5576b946b364efa5edd14b929ec345021a78ae9ea5053152。7件は試験doubleの新引数対応6件と旧表示ラベルassert1件で、修復後結果とは別に残す。この7 FAILをGREENと扱わない。

## 3. 追加した証拠ノード

| 証拠 | 確定する限定範囲 | 認定しない範囲 |
|---|---|---|
| p3-321-final-revalidation/full-source-r2/run.json | 3877 PASS / 4 symlink SKIP、3881件、source前後540件一致 | 実画面・152操作・UX・実データの代用ではない |
| p3-321-native-gpu/packaged-natural-cuda-r1/result.json | 自然起動2回、初回CUDA、3頁×style/composition、結果頁2、再起動検索、正常終了・子0・2port解放 | 追加CDP遷移なし。再起動provider nullは新たなGPU推論ではない |
| p3-323-profile-gpu-restart/run-r1/result.json | 無引数で保存profile revision2、CUDA自動解析3頁、結果頁2、tray OFFのGDI復帰・確認終了 | 合成・Cookie未使用。tray ON・別DPI・旧picker失敗run全体の合格ではない |
| p3-318-w01-context-entries/edge-r2/result.json | 作者/ブクマ右クリック入口、類似結果・頁、未解析理由等の実Edge合成操作 | 原全scopeと現候補全体へ自動認定しない |
| p3-318-d09-d10-live-path-worker4/edge-final/result.json | 概要実製品経路、明示クリック前GETなし、合成GET/Retry、短長本文、安全描画、keyboard/resize | 実Pixiv GET・現製品全体の受入ではない |
| p3-318-recommendation-visual/edge-product-r4/result.json | 画像カードと未保存viewerの既存4run | 今も未実装とは扱わず、変わった製品結線を照合する |
| p3-318-calendar-slider/edge-product-r2/result.json | 原カレンダー実選択とslider release | L04/L07を旧不足表だけで未実装扱いしない |
| p3-318-native-save-layout/edge-normal-profile-r3/result.json | 通常profile direct/linkの既存4run | 通常profile・services等の変更範囲を照合する |
| p3-318-scheduler-70-authors/edge-r1/result.json | 70作者×1作品2頁、50/20分割から70generation/140PNG、再起動 | ☆作者全体、全間隔、停止復帰、実通知等を丸ごと認定しない |

表のパスはすべて artifacts/ 相対。実ファイルSHA・候補との比較結果・限定対象IDはv8 JSONに保存する。

### 旧証拠と候補1fのSHA差

| 旧証拠 | 同一 | 変更 | 記録対象 |
|---|---:|---:|---:|
| W01 context entries | 144 | 12 | 156製品ファイル |
| D09/D10 live path | 3 | 2 | 5製品ファイル |
| recommendation visual | 137 | 19 | 156製品ファイル |
| calendar/slider | 138 | 18 | 156製品ファイル |
| normal-profile save layout | 137 | 19 | 156製品ファイル |
| scheduler 70 | 141 | 15 | 156製品ファイル |

D09/D10で変わった2ファイルは discovery_learning.py と pixiv_read_client.py。W01では services・similar_saved・library_view・similar_view等が変わっている。差分は旧証拠が失敗したという意味ではない。変更範囲を追い、同条件で成功済みの操作を理由なく繰り返さず、現結線を確認する必要がある部分へ絞る。

## 4. 原条件を閉じる優先群

| 順 | 原ID | まだ結ぶ必要がある原範囲 | 既存経路 |
|---:|---|---|---|
| 1 | N01/N02/N03/N04/S01/S04/S15 | 通常profile、tray ON/OFF、実アイコン復帰、×取消/確認、終了。既存実行結果を先に回収 | check_native_profile_window.py / check_native_tray_window.py |
| 2 | N06/N07/N09/N10 | 配布exeのURI登録解除、cold/warm単一起動、PC拡張payload→managed receipt→status | check_protocol_registration_windows.py / check_native_extension_window.py |
| 3 | S05/S06/T01/T02/S09 | 起動設定・間隔・☆作者・履歴/CAS・停止復帰、Windows通知ON/OFF/重複防止 | check_scheduler_70_owned.py |
| 4 | S11/S12/S13/S16/S17/S18/S19/S20 | 通信間隔/retry、学習FIFO・共有LRU、次先読み、上限/排出/clearを設定consumerへ結ぶ | check_theme_fields_browser.py / check_p3_residuals_browser.py |
| 5 | S07/S08/S10/S14/U10/U11/U12/M01/M04/M06/M07/M10 | TXT/EPUB/BOTH、frames/GIF/MP4、direct/link/ZIP、実ファイル・receipt・再生・次回適用 | check_p3_save_exports_browser.py / check_native_bookmark_layout_browser.py / check_packaged_ugoira_native.py |
| 6 | L18/S22/X09/V06 | Explorer/画像/ZIP/browserの実OS起動 | check_native_window.py / x09_v06_browser_focus.py |
| 7 | 残るD/R/K/L/U/Q/X/H/E/A/V/W/Z | 原IDごとに既存証拠・原文・開始条件・実操作・観測を結ぶ | JSON各行 checker_route_v8 |

checkerファイルの存在は全条件coverageの証明ではない。check_protocol_registration_windows.pyは専用Python probeで、実配布exeのcold/warm起動とは別。run_browser_checks.pyは4経路で、152操作を網羅するrunnerではない。check_packaged_ugoira_native.pyの既定には強制cleanupがあるため、元の正常終了条件を確認する実行では既存の --no-force-cleanup を使う。

Q群はv7でproof結合がなく、機能不在とは未判定。Q01–Q03は一覧/状態/検索、Q04–Q12は個別/一括制御、Q13–Q15/Q17–Q20は旧失敗一覧/絞込/検索/Retry/削除/CSV。既存の download_jobs / failed_records / failed_managed_retry checkerへ原条件を対応付ける。元データの削除は行わない。

Z01–Z05もv7でproof結合がない。解錠/lock/資格情報更新/個人検索/premium条件を既存の合成・loopback試験と独立Vaultレビューへ結ぶ。nana7mi実利用の別承認境界は維持する。

## 5. 既存の横断条件

原152以外では、同じ画像・実描画面積・DPI・負荷・測定方法での現候補Flet比較、承認済みGETの実データ保全、Windows配布の所有PID/HWND・正常終了・port解放、独立レビュー、承認範囲のPR/CI/統合が残る。新しい任意ゲートは追加していない。

元ソース・DB・画像・ZIP・Cookieを変更しない。実Pixiv書込API禁止、実画像・実ID・認証値の非公開を維持する。v7とPROGRESSはこの作成処理で変更せず、旧RED/部分成功/旧source bindingを保持する。過去の文字化けに見える文字列を推測修復しない。

台帳: P3_FORMAL_152_OPERATIONS_LEDGER_2026-10-29_v8.json。

## 6. 保存確認

rootの正規CoS経路で生成スクリプトを実行し、exit0。原34参照ファイル、v7、既存PROGRESSの前後SHA不変、152原ID・操作名・公式集計保持、欠落checker 0を確認した。

- 台帳bytes: 1,353,077
- 台帳SHA256: 487065a9a52228266d540e36d3e3b83ea34a38bdde4d1fdb54e275d725e55bb4
- 生成スクリプト: artifacts/p3-324-formal-152/write_v8.py
- 子agentからの先行CoS呼出しは返値未回収だったため、rootで2docs未存在を確認してから新規排他作成した。先行呼出しを成功扱いしていない。
