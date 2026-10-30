# P3-318 正式152操作：次の作業の棚卸し（読取のみ）

## 判定範囲

対象は `/projects/PixivVault-NiceGUI-p3-vault-cleanup-race-20261028`（以下 R）。`docs/P3_318_FORMAL_152_OPERATIONS_LEDGER_2026-10-29_v3.json` の152行と、`artifacts/p3-318-formal/source-specification-r1/sweep-expectations.json` / `realclick-expectations.json`、N/S/Lを中心とした現製品・既存checkerを照合した。原画像・実作品本文・Cookie内容は読んでいない。実repo編集、CPU/native実行、API/registry操作はしていない。

正式33完了・43未達・76未確認を変更しない。v3の `formal_revalidations_complete=0` を保持する。以下は具体的に確認できた次作業であり、列挙外の原IDを合格とするものではない。

## 1. 現ソースで確認できる原機能・導線の欠落

| 優先 | 原ID・原期待 | 現状の根拠 | 次に必要な作業 |
|---|---|---|---|
| 最優先 | おすすめ共通（R01a/R01b/R14、L13のおすすめ側、V01）：画像カードを表示し、サムネイルを押して画集viewerを開く | 原 `/projects/PixivVault/recommend_tab.py` の `_build_superlike_card`（1128行）、`_build_certain_card`（1717行）は画像取得と `ft.Image`、サムネ左クリックの `open_viewer` を実装。原SW R-10もサムネ空欄を不合格とする。現 `ui/recommend_view.py` 245行は文字・操作行のみ。`recommendation_state.py` 38行の型は4項目のみ、`recommendation_acquisition.py` 15行が画像情報を落とす。`ui/shell.py` 279行は保存済みライブラリだけを開く | サムネイルのデータ・表示・検証済み媒体providerを接続し、大中小を実画像に適用。明示クリックで未保存作品もviewerへ到達させる。保存済み作品は現在の世代pin経路を保持する。R16/R17合格を推薦閲覧全体の代わりにしない |
| 高 | L04：SW L-1の日付from/to＋カレンダー | `ui/library_filter_panel.py` 66行・67行は通常の文字入力で、カレンダーを作成せず `type=date` 等も指定しない。日付フィルタ自体とプリセットはある | 原カレンダー操作を実装・実操作。単なる日付文字入力の成功で原操作を置換しない |
| 高 | L07：SW L-6「ブックマーク数スライダーを動かす」「離した直後に一覧が絞られる」 | 同ファイル69行は `ui.number`、72行は固定プリセット。スライダーはない | スライダーの離上操作と結果更新を実装・実操作。既存数値/プリセット経路の成功とは分ける |
| 高 | S10：通常起動でdirect/link保存配置を変更し、実配置へ適用 | `native_profile.native_kwargs` は account IDを供給しない。`bootstrap.parse_args` 130行の排他集合に `bookmark_account_id` がありprofile併用CLIも拒否。`SaveExports.__init__` 46行の account はNone、`services.preference_consumers` 616行はその場合 direct/linkを無効化。既存保存エンジンはあるが通常profileから利用不能 | 確認済みアカウントと保存ownerの結線を修復し、通常profileの設定→次の保存→実配置→再起動で確認。accountを明示注入する旧fixtureの成功だけで済ませない |

## 2. 実装は確認できるが、現在候補の実操作証拠を閉じる必要がある導線

| 原ID | 実装・既存手段 | 次の受入で確認する内容 |
|---|---|---|
| N01/N02/N03/N04/S15 | `native_tray_exit.py` / `native_lifecycle.py` / `TrayController` は限定独立承認済み。`check_native_window.py` と `check_native_profile_window.py` がある | 欠落修復後の新配布物で通常起動、×取消と確認、実トレイ格納ON/OFF、トレイ復帰・終了、復帰GDI画素、所有PID/子/portの正常解放。CPU48/35件は実トレイ操作の代替にしない |
| N06/N07、N01のURI入口 | `check_protocol_registration_windows.py` native-r3は専用scheme・Python probeで型付き元3値＋追加枝の復元、ShellExecute argv/exit0、owned cleanupまで成功 | 現配布exeの設定UI→登録/解除とURI起動へ結び付ける。cold起動と既に起動済みの時の単一起動・画面復帰を分ける。native-r3は `packaged_product_window=false`。本物のpixivvaultキーへ未承認の試験変更をしない |
| N09/N10 | `ExtensionApplication`、`ExtensionBridge`、設定画面の状態表示はある。URI登録commandは `--extension-bridge` を付与する | 原PC拡張の実payload/status経路から合成managedジョブまでを通し、受理と保存完了を分離。通常起動中のURI呼出、port競合、disabled/unavailable/runningの画面表示を確認。単なるloopback API unit成功で代用しない |
| S01/S04、L01の併用入口 | NativeProfileViewに実OS picker・CAS保存・次回起動適用がある。`check_native_profile_window.py` は空Cookieによる合成chooser/再起動を扱う | 新配布でCookie参照・保存先・凍結旧DB/旧画像rootの選択/取消/保持と通常profileへの反映。認証成功は承認済み実GETの別証拠、失効・参照喪失は復旧UI。実Cookie/作品は画像やログへ出さない |
| S03 | 合成製品Edge4runと最新following-provider CPU証拠がある | 保存した同期状態の画面内表示も撮れる位置で、公/非公開→既知0→再起動を確認。既存画像はsummaryがフレーム外なので「文字が見える」証拠にしない。実GET scopeは別 |
| S05/S06/T01/T02 | `SchedulerView`に起動時・0/6/12/24/48h・CAS保存、ApplicationServicesが既存設定を再開する経路がある | 同じ通常profileでON/OFF・次回起動・履歴・deferred/停止/再開。操作設定と実dispatch時刻・受理/verified保存の対応を残す。既存設定の無断変更や実作者への反復GETはしない |
| S07/S08/S10/S14、M01/M07、U11/U12 | 実保存owner、`SaveExports`、作者override、CPU codec、`check_p3_save_exports_browser.py` / `check_packaged_ugoira_native.py` がある | 設定変更後の次回保存、TXT/EPUB/BOTH・frames/GIF/MP4、direct/link・作者ZIPを実ファイル/receiptで照合。S10修復後の普通のprofileを通す。古いexeのcodec成功を新候補へ継承しない |
| S09 | `NotificationDispatcher` とWindowsToastBackend、scheduler保存確定からの通知が接続されている | 新配布で合成新着1件の実Windows通知とOFF時抑止、再開/二重通知防止を観測。依存の存在とCPU fake backend成功だけではWindows通知の証拠にならない |
| S11/S12/S13、S16/S17/S18/S19/S20 | request policy、FIFO/LRU、owned disk cache、設定から次操作へ適用するconsumerがある | 設定UI→次の静止画/小説/うごイラ/画像GET、失敗/429/取消を同一合成transportで観測。cacheは表示/先読みと実budget/排出/clearを結び付け、保存作品の不変を確認。既存の未接続fallback文言だけで機能不在と判定しない |
| L02/L03/L05/L06/L08/L09/L10/L11/L12/L13/L14/L15/L16/L17 | LibraryView/filter/bookmark/card-size/bulk/capacity機能と専用checker群がある | 原条件を1回の連続製品操作へ対応付け、即時更新・0件/未知値・同条件共有・タブ/2client分離・保存後更新・再起動を現候補で照合。L04/L07の原部品を先に直す |
| L18、S22、X09 | 外部起動adapter、headerリンク、ごみ箱固定owned-path導線がある | 合成の画像/ZIP/フォルダで実OSアプリ起動と消失先のエラー表示。S22はWebView内タブが増えただけでOS browser起動としない。X09は元画像移動/容量解放を意味しない。`check_protocol_registration_windows.py` の成功はこれら別導線を覆わない |

## 3. 台帳を完成させる際の前提

- v3はN/S各行の原期待配列が空で、UG99–114やnative受入文を参照するIDが残る。SW/RC抽出だけでUG原期待を補ったことにはならない。原文を追加抽出してから開始条件・実操作・期待・観測を埋める。
- 既存R16/R17 Edge r2やURI native-r3等の後続証拠はv3に未反映。限定証拠欄へ追記する際、根拠範囲と現在sourceへの適用可否を記し、正式集計は加点しない。
- 旧証拠内の `source_changed`、旧frozen exe、画像を省いたfixture、画面外のsummary、合成POSTと実GET、registry probeと配布exeは混同しない。
- 作業順は「上記欠落修復→原期待を満たす製品操作→source固定→新Windows配布/同条件UX→残る原ID照合」。現進行中の全CPU回帰は修復前の回帰基準として保存する。
