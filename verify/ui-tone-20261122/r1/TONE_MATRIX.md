# UI トンマナ一覧 第1回

NiceGUI `0ff4d7572221485833fa4438628256e4a9c46fed` / Flet `3ac3aa0d47a64785251593bcb09bb2d5f6df374b`

Chromium（Edge）、ダーク、1280×800 と 1920×1080、通常と空。すべて隔離データ。写真の左は Flet、右は NiceGUI。

主画面の NiceGUI 124 枚、Flet 56 枚、並置 56 枚。 設定の全体 4 枚、グループ別 44 枚。

各行の高さは「初期表示の本体 / 内側スクロール込みの本体（後者のビューポート高比）」。ポートの本体はタブ領域、ダイアログまたは指定領域。 Flet の比較値は Chromium のセマンティクス座標の範囲。CanvasKit の実スクロール高・描画済みフォント・枠の CSS 値は取得できないため、宣言値と DOM 座標を別々に記録した。 差分の符号は NiceGUI − Flet。専用 Flet 画面がない場合の参照は設計上の比較先。

ツールバーの行はコントロール座標の縦方向の重なりから算出。NiceGUI は DOM コンテナごと、Flet はセマンティクスの操作要素を同じ高さの行にまとめた。 Flet 側でロールなしの入力やチップ型ボタンがあり、行・要素数は観測できた範囲の目安。

設定の全体画像は同じ画面をスクロールし、撮影した実ピクセルをつないだもの。グループ画像も同じ撮影から切り出した。

設定グループの Flet 参考値は直後のグループの見出しまでの間隔。設定タブ全体の比較とともに、実際の画素境界を見ながら確認できる。

## 縦方向の差が大きい10画面

| 画面 | 条件 | Flet の参照 | 縦方向の差 | ビューポート比の差 |
|---|---|---|---:|---:|
| 設定 | normal/1280x800_dark | 設定 | 2480 → 4923 px (+2443 px) | +305.38 pp |
| おすすめ | normal/1280x800_dark | おすすめ | 613 → 1290 px (+677 px) | +84.62 pp |
| 重複整理 | normal/1280x800_dark | 重複整理 | 440 → 1029 px (+589 px) | +73.62 pp |
| メタデータ台帳（展開） | normal/1280x800_dark | 設定・作品メタデータ（参考） | 209 → 761 px (+552 px) | +69 pp |
| 学習 | empty/1280x800_dark | 学習 | 237 → 748 px (+511 px) | +63.88 pp |
| 閲覧履歴 | empty/1280x800_dark | 閲覧履歴 | 617 → 288 px (-329 px) | -41.12 pp |
| メタデータ補完 | normal/1920x1080_dark | 設定・作品メタデータ（参考） | 209 → 555 px (+346 px) | +32.04 pp |
| 自分のランキング（検索結果） | normal/1280x800_dark | 自分のランキング（検索結果） | 458 → 701 px (+243 px) | +30.38 pp |
| 自分のランキング | normal/1280x800_dark | 自分のランキング | 458 → 699 px (+241 px) | +30.12 pp |
| 共有ログ（折りたたみ） | normal/1280x800_dark | キュー内ログ（参考） | 156 → 32 px (-124 px) | -15.5 pp |

## normal/1280x800_dark

| 画面 | 見出し様式 | ツールバーの行（各行の要素数） | ボタン様式 | 空・表示状態 | 縦の長さ | Flet 基準との差 |
|---|---|---|---|---|---|---|
| [キュー](normal/1280x800_dark/queue/port.png) [並置](normal/1280x800_dark/queue/paired.png) | 2件 15px / Yu Mincho / 700 | 2 / 8 / 1 / 4 / 4 / 2 | filled-square 1 / outlined 8 / text-link 11 | 通常の合成データ | 418.52 / 518 px (0.65) | キュー Δ初期 +108.52 px・Δ縦 +71 px・Δ行 +2・Δ最大行 -1 |
| [失敗リスト](normal/1280x800_dark/failed/port.png) [並置](normal/1280x800_dark/failed/paired.png) | 1件 15px / Yu Mincho / 700 | 1 / 1 / 9 / 2 / 2 / 2 / 2 / 2 / 2 / 2 / 2 / 2 | filled-square 1 / icon-only 12 / outlined 6 / pill 8 / text-link 1 | 通常の合成データ | 418.52 / 814 px (1.02) | 失敗リスト Δ初期 -63.48 px・Δ縦 +94 px・Δ行 +4・Δ最大行 +0 |
| [重複整理](normal/1280x800_dark/dedup/port.png) [並置](normal/1280x800_dark/dedup/paired.png) | 1件 15px / Yu Mincho / 700 | 5 / 4 / 2 / 1 / 1 / 1 / 1 | filled-square 2 / outlined 3 / text-link 7 | 1組・3枚を確認／読取失敗0枚／上限超過0作者 | 418.52 / 1029 px (1.29) | 重複整理 Δ初期 -21.48 px・Δ縦 +589 px・Δ行 +3・Δ最大行 +1 |
| [学習](normal/1280x800_dark/learning/port.png) [並置](normal/1280x800_dark/learning/paired.png) | 1件 20px / Noto Sans JP / 400 | 2 / 1 / 4 / 4 / 1 / 1 / 1 / 1 | filled-square 1 / outlined 3 / text-link 11 | 画像を読み込めませんでした。再読込してください。 / 次の候補を取得しています。 | 418.52 / 778 px (0.97) | 学習 Δ初期 -14.48 px・Δ縦 +345 px・Δ行 +2・Δ最大行 +0 |
| [ランキング](normal/1280x800_dark/ranking/port.png) [並置](normal/1280x800_dark/ranking/paired.png) | 検出0 | 10 / 3 / 5 / 1 | filled-square 3 / icon-only 2 / outlined 3 / pill 4 / text-link 1 | 公式ランキング · 1 件 | 418.52 / 492 px (0.62) | ランキング Δ初期 -39.48 px・Δ縦 +34 px・Δ行 +1・Δ最大行 +0 |
| [ライブラリ](normal/1280x800_dark/library/port.png) [並置](normal/1280x800_dark/library/paired.png) | 1件 15px / Noto Sans JP / 700 | 13 / 3 | filled-square 3 / icon-only 2 / outlined 2 / text-link 12 | 通常の合成データ | 418.52 / 419 px (0.52) | ライブラリ Δ初期 -42.48 px・Δ縦 -43 px・Δ行 -2・Δ最大行 +4 |
| [マイブックマーク](normal/1280x800_dark/bookmarks/port.png) [並置](normal/1280x800_dark/bookmarks/paired.png) | 2件 15px / Noto Sans JP / 700 | 7 / 3 / 1 | filled-square 2 / icon-only 1 / outlined 1 / text-link 7 | 通常の合成データ | 418.52 / 419 px (0.52) | マイブックマーク Δ初期 -42.48 px・Δ縦 -43 px・Δ行 -1・Δ最大行 +1 |
| [おすすめ](normal/1280x800_dark/recommend/port.png) [並置](normal/1280x800_dark/recommend/paired.png) | 7件 20px / Noto Sans JP / 400・15px / Noto Sans JP / 700 | 6 / 3 / 3 / 3 / 1 / 2 / 2 / 2 / 2 / 2 / 2 / 2 / 3 / 3 / 1 | filled-square 2 / outlined 2 / text-link 35 | 保存結果を確認できない項目があります：もっと見る登録中の作者の新着 | 418.52 / 1290 px (1.61) | おすすめ Δ初期 -41.48 px・Δ縦 +677 px・Δ行 +10・Δ最大行 +0 |
| [直近のバックアップ](normal/1280x800_dark/history/port.png) [並置](normal/1280x800_dark/history/paired.png) | 1件 15px / Noto Sans JP / 700 | 4 | filled-square 1 / outlined 4 / text-link 1 | 2 作者 | 418.52 / 419 px (0.52) | 直近のバックアップ Δ初期 -43.48 px・Δ縦 -43 px・Δ行 -1・Δ最大行 +0 |
| [設定](normal/1280x800_dark/settings/port.png) [並置](normal/1280x800_dark/settings/paired.png) | 14件 20px / Yu Mincho / 500・16px / Noto Sans JP / 400・18px / Noto Sans JP / 600・16px / Yu Mincho / 400 | 1 / 2 / 2 / 1 / 1 / 1 / 2 / 1 / 2 / 1 / 2 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 3 | outlined 2 / pill 20 | 未確認の更新結果はありません。必要なときだけアカウントを更新できます。 / 状態 無効 | 720 / 4923 px (6.15) | 設定 Δ初期 +56 px・Δ縦 +2443 px・Δ行 +33・Δ最大行 +2 |
| [自分のランキング](normal/1280x800_dark/personal/port.png) [並置](normal/1280x800_dark/personal/paired.png) | 検出0 | 8 / 1 / 1 / 2 / 1 / 5 | filled-square 3 / icon-only 1 / outlined 3 / text-link 3 | 借用プレミアムをONにして、借用アカウントを更新してください。 | 418.52 / 699 px (0.87) | 自分のランキング Δ初期 +197.52 px・Δ縦 +241 px・Δ行 +2・Δ最大行 +2 |
| [定期チェック](normal/1280x800_dark/scheduler/port.png) | 1件 20px / Noto Sans JP / 500 | 3 / 1 / 5 | filled-square 6 | 通常の合成データ | 236.53 / 369 px (0.46) | 設定・保存ポリシー・通知（参考） Δ初期 —・Δ縦 -78 px・Δ行 —・Δ最大行 — |
| [メタデータ補完](normal/1280x800_dark/metadata/port.png) | 1件 20px / Noto Sans JP / 500 | 3 / 1 | filled-square 3 / outlined 1 | メタデータ: 1 / 3 件（残り 1 ／ 3回試しても取れなかった 1） / 取得処理: 未実行 | 236.53 / 317 px (0.4) | 設定・作品メタデータ（参考） Δ初期 —・Δ縦 +108 px・Δ行 —・Δ最大行 — |
| [ダウンロード](normal/1280x800_dark/download/port.png) [並置](normal/1280x800_dark/download/paired.png) | 検出0 | 1 / 2 / 1 / 6 / 1 / 1 / 1 / 5 / 1 / 1 / 1 | filled-square 3 / icon-only 5 / outlined 1 / pill 5 / text-link 1 | 左の一覧でチェックすると、ここに積まれます / 待機中 | 418.52 / 434 px (0.54) | ダウンロード Δ初期 -0.48 px・Δ縦 +15 px・Δ行 +2・Δ最大行 +0 |
| [閲覧履歴](normal/1280x800_dark/view-history/port.png) [並置](normal/1280x800_dark/view-history/paired.png) | 1件 20px / Noto Sans JP / 400 | 2 / 2 / 1 | outlined 1 / text-link 4 | 1件 | 330.5 / 331 px (0.41) | 閲覧履歴 Δ初期 -286.5 px・Δ縦 -286 px・Δ行 +1・Δ最大行 -1 |
| [メタデータ台帳（展開）](normal/1280x800_dark/metadata-ledger/port.png) | 1件 20px / Noto Sans JP / 500 | 3 / 1 / 1 / 1 / 1 / 4 / 1 | filled-square 10 / outlined 1 | メタデータ: 1 / 3 件（残り 1 ／ 3回試しても取れなかった 1） / 取得処理: 未実行 | 236.53 / 761 px (0.95) | 設定・作品メタデータ（参考） Δ初期 —・Δ縦 +552 px・Δ行 —・Δ最大行 — |
| [共有ログ（展開）](normal/1280x800_dark/shared-log/port.png) | 検出0 | 1 | text-link 1 | 通常の合成データ | 171.98 / 172 px (0.22) | キュー内ログ（参考） Δ初期 —・Δ縦 +16 px・Δ行 —・Δ最大行 — |
| [共有ログ（折りたたみ）](normal/1280x800_dark/shared-log-collapsed/port.png) | 検出0 | 1 | text-link 1 | 通常の合成データ | 31.98 / 32 px (0.04) | キュー内ログ（参考） Δ初期 —・Δ縦 -124 px・Δ行 —・Δ最大行 — |
| [永続429停止の回復ダイアログ](normal/1280x800_dark/stop-recovery/port.png) | 1件 20px / Noto Sans JP / 500 | 1 / 2 | filled-square 1 / text-link 1 | 過去のHTTP 429（アクセス制限）によるSTOPが記録されています。この回復では解除できません。今回の429の設定待機・再試行とは別の永続停止履歴で、再起動しても残ります。停止履歴・所有情報を削除 | 372 / 372 px (0.47) | 対応なし |
| [中断GETの回復ダイアログ](normal/1280x800_dark/stop-uncertain/port.png) | 1件 20px / Noto Sans JP / 500 | 1 / 2 | filled-square 1 / text-link 1 | 前の取得の結果が不確かなため、新しい取得を停止しています。画面上部の「取得停止の確認・回復」を開き、回復手順を確認してください。 | 330 / 330 px (0.41) | 対応なし |
| [起動設定・参照先不可](normal/1280x800_dark/startup-reference/port.png) | 4件 24px / Noto Sans JP / 400・16px / Noto Sans JP / 400・18px / Noto Sans JP / 600 | 2 / 2 / 1 / 1 / 1 / 2 / 1 | outlined 3 / text-link 7 | 設定した参照先を利用できません。接続と保存先を確認し、旧DBは使用中でない凍結コピーを選択して再起動してください。 | 800 / 838 px (1.05) | 対応なし |
| [起動設定・Cookie読込不可](normal/1280x800_dark/startup-cookie-rejected/port.png) | 4件 24px / Noto Sans JP / 400・16px / Noto Sans JP / 400・18px / Noto Sans JP / 600 | 2 / 2 / 1 / 1 / 1 / 2 / 1 | outlined 3 / text-link 7 | 選択したCookieファイルを読み込めません。ファイルを選び直して保存し、アプリを再起動してください。 | 800 / 838 px (1.05) | 対応なし |
| [起動設定・停止記録使用中](normal/1280x800_dark/startup-lock-busy/port.png) | 4件 24px / Noto Sans JP / 400・16px / Noto Sans JP / 400・18px / Noto Sans JP / 600 | 2 / 2 / 1 / 1 / 2 / 1 | outlined 3 / text-link 6 | 通信記録が使用中のため確認できません。ほかの処理が終わってからアプリを再起動してください。確認できるまで通信は停止しています。 | 786.39 / 786 px (0.98) | 対応なし |
| [起動設定・旧形式判別不可](normal/1280x800_dark/startup-legacy-uncertain/port.png) | 4件 24px / Noto Sans JP / 400・16px / Noto Sans JP / 400・18px / Noto Sans JP / 600 | 2 / 2 / 1 / 1 / 2 / 1 | outlined 3 / text-link 6 | 保管領域の状態を確認できません（旧形式の判別不能）。バックアップから復元してください。 | 765.39 / 765 px (0.96) | 対応なし |
| [起動設定・旧DB複製不可](normal/1280x800_dark/startup-legacy-copy-error/port.png) | 4件 24px / Noto Sans JP / 400・16px / Noto Sans JP / 400・18px / Noto Sans JP / 600 | 2 / 2 / 1 / 1 / 1 / 2 / 1 | outlined 3 / text-link 7 | 旧DBの複製作成に失敗しました。保存先・容量・旧DBの使用状態を確認して再起動してください。 | 800 / 838 px (1.05) | 対応なし |
| [起動設定・停止履歴移行要](normal/1280x800_dark/startup-stop-migration/port.png) | 4件 24px / Noto Sans JP / 400・16px / Noto Sans JP / 400・18px / Noto Sans JP / 600 | 1 / 2 / 2 / 1 / 1 / 2 / 1 | filled-square 1 / outlined 3 / text-link 6 | 認証・通信の停止履歴と保存先の所有状態を安全に照合できません。通信は停止しています。保存済みデータを削除せず、停止履歴の確認と正式な移行を行ってください。 | 800 / 838 px (1.05) | 対応なし |
| [停止履歴の確認と移行案内](normal/1280x800_dark/stop-history-guide/port.png) | 1件 20px / Noto Sans JP / 500 | 1 / 1 / 1 | filled-square 3 | まだ確認していません。 | 673 / 673 px (0.84) | 対応なし |
| [正式移行のダイアログ](normal/1280x800_dark/stop-migration-dialog/port.png) | 4件 24px / Noto Sans JP / 600・18px / Noto Sans JP / 600 | 1 / 1 / 2 / 1 / 1 / 1 / 1 / 2 | filled-square 2 / outlined 4 / text-link 1 | 移行はまだ始まっていません。 | 752 / 1088 px (1.36) | 対応なし |
| [今回の429・再試行待機](normal/1280x800_dark/request-wait/port.png) | 検出0 | 2 | text-link 1 | × [未認証/期限切れ] Cookieファイルが設定されていません。設定から選んでください。 / HTTP 429のため再試行待機中: 試行 1/3・残り約5秒（待機設定 5秒）。今回の429は設定した総試行回数（初回を含む）の範囲で再試行します。共有STOP履歴には記録しません。 | 178.98 / 179 px (0.22) | 対応なし |
| [今回の429・試行上限と共有停止](normal/1280x800_dark/request-limit/port.png) | 検出0 | 2 | text-link 1 | × [未認証/期限切れ] Cookieファイルが設定されていません。設定から選んでください。 / 今回のHTTP 429: この操作の試行 3/3。この操作自身が429の試行上限に達したため、共有停止しました。このポリシーを共有する取得をこの起動中は停止しています。共有STOP履歴には記録しません | 262.98 / 263 px (0.33) | 対応なし |
| [自分のランキング（検索結果）](normal/1280x800_dark/personal-results/port.png) [並置](normal/1280x800_dark/personal-results/paired.png) | 検出0 | 8 / 2 / 1 / 2 / 1 / 5 | filled-square 4 / icon-only 2 / outlined 3 / text-link 3 | 借用アカウント・1件表示 / 0件を条件で除外・これで全部 | 418.52 / 701 px (0.88) | 自分のランキング（検索結果） Δ初期 -39.48 px・Δ縦 +243 px・Δ行 +1・Δ最大行 +2 |

[設定全体](normal/1280x800_dark/settings/port-all-groups.png)

| 設定グループ | 見出し様式 | 行ごとの要素数 | ボタン様式 | 空・表示状態 | 縦の長さ | Flet 基準との差 |
|---|---|---|---|---|---:|---|
| [Pixiv Cookieファイル](normal/1280x800_dark/settings/group-01.png) | 3件 16px / Noto Sans JP / 400・18px / Noto Sans JP / 600 | 2 / 2 / 1 / 1 / 1 / 2 / 1 | outlined 2 / pill 8 | 設定フォーム | 710 px | アカウントの見出し間隔 508 px と比較 +202 px |
| [フォロー中リストの同期](normal/1280x800_dark/settings/group-02.png) | 1件 16px / Noto Sans JP / 400 | 2 | pill 2 | 設定フォーム | 296 px | アカウントの見出し間隔 508 px と比較 -212 px |
| [借用プレミアムの状態](normal/1280x800_dark/settings/group-03.png) | 1件 16px / Noto Sans JP / 400 | 1 / 2 / 1 | pill 3 | 設定フォーム | 272 px | 対応なし |
| [ローカルタグ補完モデル（任意）](normal/1280x800_dark/settings/group-04.png) | 1件 16px / Noto Sans JP / 400 | 1 / 1 | 0件 | 設定フォーム | 256 px | 対応なし |
| [保存](normal/1280x800_dark/settings/group-05.png) | 1件 16px / Yu Mincho / 400 | 1 / 1 / 1 / 1 / 1 / 1 | 0件 | 設定フォーム | 666 px | 保存ポリシー・通知の見出し間隔 447 px と比較 +219 px |
| [通信](normal/1280x800_dark/settings/group-06.png) | 1件 16px / Yu Mincho / 400 | 1 / 1 / 1 / 1 / 1 / 1 / 1 | 0件 | 設定フォーム | 650 px | 詳細設定の見出し間隔 629 px と比較 +21 px |
| [作品メタデータ](normal/1280x800_dark/settings/group-07.png) | 1件 16px / Yu Mincho / 400 | 1 | 0件 | 設定フォーム | 182 px | 作品メタデータの見出し間隔 209 px と比較 -27 px |
| [絵柄・構図の推論](normal/1280x800_dark/settings/group-08.png) | 1件 16px / Yu Mincho / 400 | 1 | 0件 | 設定フォーム | 238 px | 絵柄・構図の推論の見出し間隔 267 px と比較 -29 px |
| [キャッシュ](normal/1280x800_dark/settings/group-09.png) | 1件 16px / Yu Mincho / 400 | 1 / 1 / 1 / 1 | 0件 | 設定フォーム | 342 px | 詳細設定の見出し間隔 629 px と比較 -287 px |
| [Windows](normal/1280x800_dark/settings/group-10.png) | 1件 16px / Yu Mincho / 400 | 1 / 1 | 0件 | 設定フォーム | 270 px | 詳細設定の見出し間隔 629 px と比較 -359 px |
| [エクスポート](normal/1280x800_dark/settings/group-11.png) | 1件 16px / Yu Mincho / 400 | 1 / 1 | pill 2 | 設定フォーム | 338 px | エクスポートの見出し間隔 144 px と比較 +194 px |

## normal/1920x1080_dark

| 画面 | 見出し様式 | ツールバーの行（各行の要素数） | ボタン様式 | 空・表示状態 | 縦の長さ | Flet 基準との差 |
|---|---|---|---|---|---|---|
| [キュー](normal/1920x1080_dark/queue/port.png) [並置](normal/1920x1080_dark/queue/paired.png) | 2件 15px / Yu Mincho / 700 | 2 / 8 / 1 / 4 / 4 / 2 | filled-square 1 / outlined 8 / text-link 11 | 通常の合成データ | 736.52 / 737 px (0.68) | キュー Δ初期 +146.52 px・Δ縦 +10 px・Δ行 +2・Δ最大行 -1 |
| [失敗リスト](normal/1920x1080_dark/failed/port.png) [並置](normal/1920x1080_dark/failed/paired.png) | 1件 15px / Yu Mincho / 700 | 1 / 1 / 9 / 2 / 2 / 2 / 2 / 2 / 2 / 2 / 2 / 2 | filled-square 1 / icon-only 12 / outlined 6 / pill 8 / text-link 1 | 通常の合成データ | 736.52 / 814 px (0.75) | 失敗リスト Δ初期 -5.48 px・Δ縦 +72 px・Δ行 +1・Δ最大行 +0 |
| [重複整理](normal/1920x1080_dark/dedup/port.png) [並置](normal/1920x1080_dark/dedup/paired.png) | 1件 15px / Yu Mincho / 700 | 5 / 4 / 2 / 1 / 1 / 1 / 1 | filled-square 2 / outlined 3 / text-link 7 | 1組・3枚を確認／読取失敗0枚／上限超過0作者 | 736.52 / 1045 px (0.97) | 重複整理 Δ初期 +16.52 px・Δ縦 +325 px・Δ行 +4・Δ最大行 +1 |
| [学習](normal/1920x1080_dark/learning/port.png) [並置](normal/1920x1080_dark/learning/paired.png) | 1件 20px / Noto Sans JP / 400 | 2 / 1 / 4 / 4 / 1 / 1 / 1 / 1 | filled-square 1 / outlined 3 / text-link 11 | 画像を読み込めませんでした。再読込してください。 / 次の候補を取得しています。 | 736.52 / 837 px (0.78) | 学習 Δ初期 +23.52 px・Δ縦 +124 px・Δ行 +2・Δ最大行 +0 |
| [ランキング](normal/1920x1080_dark/ranking/port.png) [並置](normal/1920x1080_dark/ranking/paired.png) | 検出0 | 13 / 5 / 1 | filled-square 3 / icon-only 2 / outlined 3 / pill 4 / text-link 1 | 公式ランキング · 1 件 | 736.52 / 737 px (0.68) | ランキング Δ初期 -1.48 px・Δ縦 -1 px・Δ行 +0・Δ最大行 +3 |
| [ライブラリ](normal/1920x1080_dark/library/port.png) [並置](normal/1920x1080_dark/library/paired.png) | 1件 15px / Noto Sans JP / 700 | 13 / 3 | filled-square 3 / icon-only 2 / outlined 2 / text-link 12 | 通常の合成データ | 736.52 / 737 px (0.68) | ライブラリ Δ初期 -4.48 px・Δ縦 -5 px・Δ行 -2・Δ最大行 +4 |
| [マイブックマーク](normal/1920x1080_dark/bookmarks/port.png) [並置](normal/1920x1080_dark/bookmarks/paired.png) | 2件 15px / Noto Sans JP / 700 | 7 / 3 / 1 | filled-square 2 / icon-only 1 / outlined 1 / text-link 7 | 通常の合成データ | 736.52 / 737 px (0.68) | マイブックマーク Δ初期 -4.48 px・Δ縦 -5 px・Δ行 -1・Δ最大行 +1 |
| [おすすめ](normal/1920x1080_dark/recommend/port.png) [並置](normal/1920x1080_dark/recommend/paired.png) | 7件 20px / Noto Sans JP / 400・15px / Noto Sans JP / 700 | 6 / 3 / 3 / 3 / 1 / 2 / 2 / 2 / 2 / 2 / 2 / 2 / 3 / 3 / 1 | filled-square 2 / outlined 2 / text-link 35 | 保存結果を確認できない項目があります：もっと見る登録中の作者の新着 | 736.52 / 1396 px (1.29) | おすすめ Δ初期 -3.48 px・Δ縦 +656 px・Δ行 +9・Δ最大行 +0 |
| [直近のバックアップ](normal/1920x1080_dark/history/port.png) [並置](normal/1920x1080_dark/history/paired.png) | 1件 15px / Noto Sans JP / 700 | 4 | filled-square 1 / outlined 4 / text-link 1 | 2 作者 | 736.52 / 737 px (0.68) | 直近のバックアップ Δ初期 -5.48 px・Δ縦 -5 px・Δ行 -1・Δ最大行 +0 |
| [設定](normal/1920x1080_dark/settings/port.png) [並置](normal/1920x1080_dark/settings/paired.png) | 14件 20px / Yu Mincho / 500・16px / Noto Sans JP / 400・18px / Noto Sans JP / 600・16px / Yu Mincho / 400 | 1 / 2 / 2 / 1 / 1 / 1 / 2 / 1 / 2 / 1 / 2 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 3 | outlined 2 / pill 20 | 未確認の更新結果はありません。必要なときだけアカウントを更新できます。 / 状態 無効 | 972 / 4923 px (4.56) | 設定 Δ初期 +308 px・Δ縦 +2443 px・Δ行 +31・Δ最大行 +2 |
| [自分のランキング](normal/1920x1080_dark/personal/port.png) [並置](normal/1920x1080_dark/personal/paired.png) | 検出0 | 9 / 1 / 2 / 1 / 5 | filled-square 3 / icon-only 1 / outlined 3 / text-link 3 | 借用プレミアムをONにして、借用アカウントを更新してください。 | 736.52 / 737 px (0.68) | 自分のランキング Δ初期 +515.52 px・Δ縦 -1 px・Δ行 +1・Δ最大行 +3 |
| [定期チェック](normal/1920x1080_dark/scheduler/port.png) | 1件 20px / Noto Sans JP / 500 | 3 / 1 / 5 | filled-square 6 | 通常の合成データ | 554.53 / 555 px (0.51) | 設定・保存ポリシー・通知（参考） Δ初期 —・Δ縦 +108 px・Δ行 —・Δ最大行 — |
| [メタデータ補完](normal/1920x1080_dark/metadata/port.png) | 1件 20px / Noto Sans JP / 500 | 3 / 1 | filled-square 3 / outlined 1 | メタデータ: 1 / 3 件（残り 1 ／ 3回試しても取れなかった 1） / 取得処理: 未実行 | 554.53 / 555 px (0.51) | 設定・作品メタデータ（参考） Δ初期 —・Δ縦 +346 px・Δ行 —・Δ最大行 — |
| [ダウンロード](normal/1920x1080_dark/download/port.png) [並置](normal/1920x1080_dark/download/paired.png) | 検出0 | 1 / 2 / 1 / 6 / 1 / 1 / 1 / 1 / 1 / 1 / 4 / 1 | filled-square 3 / icon-only 5 / outlined 1 / pill 5 / text-link 1 | 左の一覧でチェックすると、ここに積まれます / 待機中 | 736.52 / 737 px (0.68) | ダウンロード Δ初期 +37.52 px・Δ縦 +38 px・Δ行 +2・Δ最大行 +0 |
| [閲覧履歴](normal/1920x1080_dark/view-history/port.png) [並置](normal/1920x1080_dark/view-history/paired.png) | 1件 20px / Noto Sans JP / 400 | 2 / 2 / 1 | outlined 1 / text-link 4 | 1件 | 330.5 / 331 px (0.31) | 閲覧履歴 Δ初期 -286.5 px・Δ縦 -286 px・Δ行 +1・Δ最大行 -1 |
| [メタデータ台帳（展開）](normal/1920x1080_dark/metadata-ledger/port.png) | 1件 20px / Noto Sans JP / 500 | 3 / 1 / 1 / 1 / 1 / 4 / 1 | filled-square 10 / outlined 1 | メタデータ: 1 / 3 件（残り 1 ／ 3回試しても取れなかった 1） / 取得処理: 未実行 | 554.53 / 761 px (0.7) | 設定・作品メタデータ（参考） Δ初期 —・Δ縦 +552 px・Δ行 —・Δ最大行 — |
| [共有ログ（展開）](normal/1920x1080_dark/shared-log/port.png) | 検出0 | 1 | text-link 1 | 通常の合成データ | 171.98 / 172 px (0.16) | キュー内ログ（参考） Δ初期 —・Δ縦 +16 px・Δ行 —・Δ最大行 — |
| [共有ログ（折りたたみ）](normal/1920x1080_dark/shared-log-collapsed/port.png) | 検出0 | 1 | text-link 1 | 通常の合成データ | 31.98 / 32 px (0.03) | キュー内ログ（参考） Δ初期 —・Δ縦 -124 px・Δ行 —・Δ最大行 — |
| [永続429停止の回復ダイアログ](normal/1920x1080_dark/stop-recovery/port.png) | 1件 20px / Noto Sans JP / 500 | 1 / 2 | filled-square 1 / text-link 1 | 過去のHTTP 429（アクセス制限）によるSTOPが記録されています。この回復では解除できません。今回の429の設定待機・再試行とは別の永続停止履歴で、再起動しても残ります。停止履歴・所有情報を削除 | 372 / 372 px (0.34) | 対応なし |
| [中断GETの回復ダイアログ](normal/1920x1080_dark/stop-uncertain/port.png) | 1件 20px / Noto Sans JP / 500 | 1 / 2 | filled-square 1 / text-link 1 | 前の取得の結果が不確かなため、新しい取得を停止しています。画面上部の「取得停止の確認・回復」を開き、回復手順を確認してください。 | 330 / 330 px (0.31) | 対応なし |
| [起動設定・参照先不可](normal/1920x1080_dark/startup-reference/port.png) | 4件 24px / Noto Sans JP / 400・16px / Noto Sans JP / 400・18px / Noto Sans JP / 600 | 2 / 2 / 1 / 1 / 1 / 2 / 1 | outlined 3 / text-link 7 | 設定した参照先を利用できません。接続と保存先を確認し、旧DBは使用中でない凍結コピーを選択して再起動してください。 | 838.39 / 838 px (0.78) | 対応なし |
| [起動設定・Cookie読込不可](normal/1920x1080_dark/startup-cookie-rejected/port.png) | 4件 24px / Noto Sans JP / 400・16px / Noto Sans JP / 400・18px / Noto Sans JP / 600 | 2 / 2 / 1 / 1 / 1 / 2 / 1 | outlined 3 / text-link 7 | 選択したCookieファイルを読み込めません。ファイルを選び直して保存し、アプリを再起動してください。 | 838.39 / 838 px (0.78) | 対応なし |
| [起動設定・停止記録使用中](normal/1920x1080_dark/startup-lock-busy/port.png) | 4件 24px / Noto Sans JP / 400・16px / Noto Sans JP / 400・18px / Noto Sans JP / 600 | 2 / 2 / 1 / 1 / 2 / 1 | outlined 3 / text-link 6 | 通信記録が使用中のため確認できません。ほかの処理が終わってからアプリを再起動してください。確認できるまで通信は停止しています。 | 786.39 / 786 px (0.73) | 対応なし |
| [起動設定・旧形式判別不可](normal/1920x1080_dark/startup-legacy-uncertain/port.png) | 4件 24px / Noto Sans JP / 400・16px / Noto Sans JP / 400・18px / Noto Sans JP / 600 | 2 / 2 / 1 / 1 / 2 / 1 | outlined 3 / text-link 6 | 保管領域の状態を確認できません（旧形式の判別不能）。バックアップから復元してください。 | 765.39 / 765 px (0.71) | 対応なし |
| [起動設定・旧DB複製不可](normal/1920x1080_dark/startup-legacy-copy-error/port.png) | 4件 24px / Noto Sans JP / 400・16px / Noto Sans JP / 400・18px / Noto Sans JP / 600 | 2 / 2 / 1 / 1 / 1 / 2 / 1 | outlined 3 / text-link 7 | 旧DBの複製作成に失敗しました。保存先・容量・旧DBの使用状態を確認して再起動してください。 | 838.39 / 838 px (0.78) | 対応なし |
| [起動設定・停止履歴移行要](normal/1920x1080_dark/startup-stop-migration/port.png) | 4件 24px / Noto Sans JP / 400・16px / Noto Sans JP / 400・18px / Noto Sans JP / 600 | 1 / 2 / 2 / 1 / 1 / 2 / 1 | filled-square 1 / outlined 3 / text-link 6 | 認証・通信の停止履歴と保存先の所有状態を安全に照合できません。通信は停止しています。保存済みデータを削除せず、停止履歴の確認と正式な移行を行ってください。 | 838.39 / 838 px (0.78) | 対応なし |
| [停止履歴の確認と移行案内](normal/1920x1080_dark/stop-history-guide/port.png) | 1件 20px / Noto Sans JP / 500 | 1 / 1 / 1 | filled-square 3 | まだ確認していません。 | 673 / 673 px (0.62) | 対応なし |
| [正式移行のダイアログ](normal/1920x1080_dark/stop-migration-dialog/port.png) | 4件 24px / Noto Sans JP / 600・18px / Noto Sans JP / 600 | 1 / 1 / 2 / 1 / 1 / 1 / 1 / 2 | filled-square 2 / outlined 4 / text-link 1 | 移行はまだ始まっていません。 | 1032 / 1088 px (1.01) | 対応なし |
| [今回の429・再試行待機](normal/1920x1080_dark/request-wait/port.png) | 検出0 | 2 | text-link 1 | × [未認証/期限切れ] Cookieファイルが設定されていません。設定から選んでください。 / HTTP 429のため再試行待機中: 試行 1/3・残り約5秒（待機設定 5秒）。今回の429は設定した総試行回数（初回を含む）の範囲で再試行します。共有STOP履歴には記録しません。 | 157.98 / 158 px (0.15) | 対応なし |
| [今回の429・試行上限と共有停止](normal/1920x1080_dark/request-limit/port.png) | 検出0 | 2 | text-link 1 | × [未認証/期限切れ] Cookieファイルが設定されていません。設定から選んでください。 / 今回のHTTP 429: この操作の試行 3/3。この操作自身が429の試行上限に達したため、共有停止しました。このポリシーを共有する取得をこの起動中は停止しています。共有STOP履歴には記録しません | 220.98 / 221 px (0.2) | 対応なし |
| [自分のランキング（検索結果）](normal/1920x1080_dark/personal-results/port.png) [並置](normal/1920x1080_dark/personal-results/paired.png) | 検出0 | 10 / 1 / 2 / 1 / 5 | filled-square 4 / icon-only 2 / outlined 3 / text-link 3 | 借用アカウント・1件表示 / 0件を条件で除外・これで全部 | 736.52 / 737 px (0.68) | 自分のランキング（検索結果） Δ初期 -1.48 px・Δ縦 -1 px・Δ行 +0・Δ最大行 +4 |

[設定全体](normal/1920x1080_dark/settings/port-all-groups.png)

| 設定グループ | 見出し様式 | 行ごとの要素数 | ボタン様式 | 空・表示状態 | 縦の長さ | Flet 基準との差 |
|---|---|---|---|---|---:|---|
| [Pixiv Cookieファイル](normal/1920x1080_dark/settings/group-01.png) | 3件 16px / Noto Sans JP / 400・18px / Noto Sans JP / 600 | 2 / 2 / 1 / 1 / 1 / 2 / 1 | outlined 2 / pill 8 | 設定フォーム | 710 px | アカウントの見出し間隔 508 px と比較 +202 px |
| [フォロー中リストの同期](normal/1920x1080_dark/settings/group-02.png) | 1件 16px / Noto Sans JP / 400 | 2 | pill 2 | 設定フォーム | 296 px | アカウントの見出し間隔 508 px と比較 -212 px |
| [借用プレミアムの状態](normal/1920x1080_dark/settings/group-03.png) | 1件 16px / Noto Sans JP / 400 | 1 / 2 / 1 | pill 3 | 設定フォーム | 272 px | 対応なし |
| [ローカルタグ補完モデル（任意）](normal/1920x1080_dark/settings/group-04.png) | 1件 16px / Noto Sans JP / 400 | 1 / 1 | 0件 | 設定フォーム | 256 px | 対応なし |
| [保存](normal/1920x1080_dark/settings/group-05.png) | 1件 16px / Yu Mincho / 400 | 1 / 1 / 1 / 1 / 1 / 1 | 0件 | 設定フォーム | 666 px | 保存ポリシー・通知の見出し間隔 447 px と比較 +219 px |
| [通信](normal/1920x1080_dark/settings/group-06.png) | 1件 16px / Yu Mincho / 400 | 1 / 1 / 1 / 1 / 1 / 1 / 1 | 0件 | 設定フォーム | 650 px | 詳細設定の見出し間隔 629 px と比較 +21 px |
| [作品メタデータ](normal/1920x1080_dark/settings/group-07.png) | 1件 16px / Yu Mincho / 400 | 1 | 0件 | 設定フォーム | 182 px | 作品メタデータの見出し間隔 209 px と比較 -27 px |
| [絵柄・構図の推論](normal/1920x1080_dark/settings/group-08.png) | 1件 16px / Yu Mincho / 400 | 1 | 0件 | 設定フォーム | 238 px | 絵柄・構図の推論の見出し間隔 267 px と比較 -29 px |
| [キャッシュ](normal/1920x1080_dark/settings/group-09.png) | 1件 16px / Yu Mincho / 400 | 1 / 1 / 1 / 1 | 0件 | 設定フォーム | 342 px | 詳細設定の見出し間隔 629 px と比較 -287 px |
| [Windows](normal/1920x1080_dark/settings/group-10.png) | 1件 16px / Yu Mincho / 400 | 1 / 1 | 0件 | 設定フォーム | 270 px | 詳細設定の見出し間隔 629 px と比較 -359 px |
| [エクスポート](normal/1920x1080_dark/settings/group-11.png) | 1件 16px / Yu Mincho / 400 | 1 / 1 | pill 2 | 設定フォーム | 338 px | エクスポートの見出し間隔 144 px と比較 +194 px |

## empty/1280x800_dark

| 画面 | 見出し様式 | ツールバーの行（各行の要素数） | ボタン様式 | 空・表示状態 | 縦の長さ | Flet 基準との差 |
|---|---|---|---|---|---|---|
| [キュー](empty/1280x800_dark/queue/port.png) [並置](empty/1280x800_dark/queue/paired.png) | 2件 15px / Yu Mincho / 700 | 2 / 8 / 1 / 2 | filled-square 1 / outlined 8 / text-link 3 | 現在のジョブはありません | 418.52 / 518 px (0.65) | キュー Δ初期 +108.52 px・Δ縦 +71 px・Δ行 +2・Δ最大行 -1 |
| [失敗リスト](empty/1280x800_dark/failed/port.png) [並置](empty/1280x800_dark/failed/paired.png) | 1件 15px / Yu Mincho / 700 | 1 / 1 / 9 / 2 | filled-square 1 / icon-only 4 / outlined 6 / text-link 1 | 失敗はありません 失敗した取得は0件です | 418.52 / 419 px (0.52) | 失敗リスト Δ初期 -43.48 px・Δ縦 -43 px・Δ行 +1・Δ最大行 +0 |
| [重複整理](empty/1280x800_dark/dedup/port.png) [並置](empty/1280x800_dark/dedup/paired.png) | 1件 15px / Yu Mincho / 700 | 5 / 4 / 1 | filled-square 2 / outlined 3 / text-link 4 | まだスキャンしていません。 / 0 / 0 組を表示（台帳全体 0 組）。旧データベースは読み取り専用です。対応付けを確定するには管理保存先でスキャンしてください。 | 418.52 / 500 px (0.63) | 重複整理 Δ初期 -31.48 px・Δ縦 +50 px・Δ行 +1・Δ最大行 +1 |
| [学習](empty/1280x800_dark/learning/port.png) [並置](empty/1280x800_dark/learning/paired.png) | 1件 14px / Yu Mincho / 600 | 2 / 1 / 2 / 1 / 1 | text-link 7 | 通信に失敗しました。30秒ごとにもう一度試しています。 | 418.52 / 748 px (0.94) | 学習 Δ初期 +181.52 px・Δ縦 +511 px・Δ行 +3・Δ最大行 +0 |
| [ランキング](empty/1280x800_dark/ranking/port.png) [並置](empty/1280x800_dark/ranking/paired.png) | 検出0 | 10 / 3 / 5 | filled-square 2 / icon-only 2 / outlined 2 / pill 4 / text-link 1 | 公式ランキング · 0 件 · これ以上のページはありません / 取得した条件に該当する作品がありません。 | 418.52 / 419 px (0.52) | ランキング Δ初期 +268.52 px・Δ縦 -39 px・Δ行 +1・Δ最大行 +0 |
| [ライブラリ](empty/1280x800_dark/library/port.png) [並置](empty/1280x800_dark/library/paired.png) | 1件 15px / Noto Sans JP / 700 | 13 / 3 | filled-square 3 / text-link 12 | 保存済み作品がありません | 418.52 / 419 px (0.52) | ライブラリ Δ初期 +274.52 px・Δ縦 -43 px・Δ行 +0・Δ最大行 +4 |
| [マイブックマーク](empty/1280x800_dark/bookmarks/port.png) [並置](empty/1280x800_dark/bookmarks/paired.png) | 2件 15px / Noto Sans JP / 700 | 7 / 3 / 1 | filled-square 2 / icon-only 1 / text-link 7 | ブックマークはありません | 418.52 / 419 px (0.52) | マイブックマーク Δ初期 +274.52 px・Δ縦 -43 px・Δ行 +1・Δ最大行 +1 |
| [おすすめ](empty/1280x800_dark/recommend/port.png) [並置](empty/1280x800_dark/recommend/paired.png) | 6件 20px / Noto Sans JP / 400・15px / Noto Sans JP / 700 | 6 / 3 / 3 / 3 / 3 / 3 / 1 | outlined 2 / text-link 20 | 保存結果を確認できない項目があります：もっと見る登録中の作者の新着・好みの傾向から | 418.52 / 741 px (0.93) | おすすめ Δ初期 +276.52 px・Δ縦 +599 px・Δ行 +5・Δ最大行 +0 |
| [直近のバックアップ](empty/1280x800_dark/history/port.png) [並置](empty/1280x800_dark/history/paired.png) | 1件 15px / Noto Sans JP / 700 | 4 | filled-square 1 / outlined 2 / text-link 1 | バックアップ日時が登録された作者はいません。 | 418.52 / 419 px (0.52) | 直近のバックアップ Δ初期 +356.52 px・Δ縦 -43 px・Δ行 +0・Δ最大行 +0 |
| [設定](empty/1280x800_dark/settings/port.png) [並置](empty/1280x800_dark/settings/paired.png) | 14件 20px / Yu Mincho / 500・16px / Noto Sans JP / 400・18px / Noto Sans JP / 600・16px / Yu Mincho / 400 | 1 / 2 / 2 / 1 / 1 / 1 / 2 / 1 / 2 / 1 / 2 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 3 | outlined 2 / pill 20 | 未確認の更新結果はありません。必要なときだけアカウントを更新できます。 / 状態 無効 | 720 / 4827 px (6.03) | 設定 Δ初期 +56 px・Δ縦 +2389 px・Δ行 +33・Δ最大行 +2 |
| [自分のランキング](empty/1280x800_dark/personal/port.png) [並置](empty/1280x800_dark/personal/paired.png) | 検出0 | 8 / 1 / 1 / 2 / 1 / 5 | filled-square 3 / icon-only 1 / outlined 3 / text-link 3 | 借用プレミアムをONにして、借用アカウントを更新してください。 | 418.52 / 699 px (0.87) | 自分のランキング Δ初期 +197.52 px・Δ縦 +241 px・Δ行 +2・Δ最大行 +2 |
| [定期チェック](empty/1280x800_dark/scheduler/port.png) | 1件 20px / Noto Sans JP / 500 | 3 / 1 / 5 | filled-square 6 | 実行履歴0件 | 236.53 / 369 px (0.46) | 設定・保存ポリシー・通知（参考） Δ初期 —・Δ縦 -78 px・Δ行 —・Δ最大行 — |
| [メタデータ補完](empty/1280x800_dark/metadata/port.png) | 1件 20px / Noto Sans JP / 500 | 2 / 1 | filled-square 2 / outlined 1 | メタデータ: 0 / 0 件（残り 0） / 取得処理: 未実行 | 236.53 / 317 px (0.4) | 設定・作品メタデータ（参考） Δ初期 —・Δ縦 +108 px・Δ行 —・Δ最大行 — |
| [ダウンロード](empty/1280x800_dark/download/port.png) [並置](empty/1280x800_dark/download/paired.png) | 検出0 | 1 / 2 / 1 / 6 / 1 / 1 / 4 / 1 | filled-square 3 / icon-only 5 / outlined 1 / pill 5 / text-link 1 | フォロー中の作者がまだ取り込まれていません。設定の「フォロー中リストの同期」を実行してください。 / 左の一覧でチェックすると、ここに積まれます | 418.52 / 434 px (0.54) | ダウンロード Δ初期 -0.48 px・Δ縦 +15 px・Δ行 +2・Δ最大行 +0 |
| [閲覧履歴](empty/1280x800_dark/view-history/port.png) [並置](empty/1280x800_dark/view-history/paired.png) | 1件 20px / Noto Sans JP / 400 | 2 / 1 | outlined 1 / text-link 2 | 0件 | 287.5 / 288 px (0.36) | 閲覧履歴 Δ初期 -329.5 px・Δ縦 -329 px・Δ行 +1・Δ最大行 -1 |
| [メタデータ台帳（展開）](empty/1280x800_dark/metadata-ledger/port.png) | 1件 20px / Noto Sans JP / 500 | 2 / 1 / 1 / 1 / 1 / 4 / 1 | filled-square 9 / outlined 1 | メタデータ: 0 / 0 件（残り 0） / 取得処理: 未実行 | 236.53 / 761 px (0.95) | 設定・作品メタデータ（参考） Δ初期 —・Δ縦 +552 px・Δ行 —・Δ最大行 — |
| [共有ログ（展開）](empty/1280x800_dark/shared-log/port.png) | 検出0 | 1 | text-link 1 | 合成ログ0件 | 171.98 / 172 px (0.22) | キュー内ログ（参考） Δ初期 —・Δ縦 +16 px・Δ行 —・Δ最大行 — |
| [共有ログ（折りたたみ）](empty/1280x800_dark/shared-log-collapsed/port.png) | 検出0 | 1 | text-link 1 | 折りたたみ状態 | 31.98 / 32 px (0.04) | キュー内ログ（参考） Δ初期 —・Δ縦 -124 px・Δ行 —・Δ最大行 — |
| [永続429停止の回復ダイアログ](empty/1280x800_dark/stop-recovery/port.png) | 1件 20px / Noto Sans JP / 500 | 1 / 2 | filled-square 1 / text-link 1 | 過去のHTTP 429（アクセス制限）によるSTOPが記録されています。この回復では解除できません。今回の429の設定待機・再試行とは別の永続停止履歴で、再起動しても残ります。停止履歴・所有情報を削除 | 372 / 372 px (0.47) | 対応なし |
| [中断GETの回復ダイアログ](empty/1280x800_dark/stop-uncertain/port.png) | 1件 20px / Noto Sans JP / 500 | 1 / 2 | filled-square 1 / text-link 1 | 前の取得の結果が不確かなため、新しい取得を停止しています。画面上部の「取得停止の確認・回復」を開き、回復手順を確認してください。 | 330 / 330 px (0.41) | 対応なし |
| [起動設定・参照先不可](empty/1280x800_dark/startup-reference/port.png) | 4件 24px / Noto Sans JP / 400・16px / Noto Sans JP / 400・18px / Noto Sans JP / 600 | 2 / 2 / 1 / 1 / 1 / 2 / 1 | outlined 3 / text-link 7 | 設定した参照先を利用できません。接続と保存先を確認し、旧DBは使用中でない凍結コピーを選択して再起動してください。 | 800 / 838 px (1.05) | 対応なし |
| [起動設定・Cookie読込不可](empty/1280x800_dark/startup-cookie-rejected/port.png) | 4件 24px / Noto Sans JP / 400・16px / Noto Sans JP / 400・18px / Noto Sans JP / 600 | 2 / 2 / 1 / 1 / 1 / 2 / 1 | outlined 3 / text-link 7 | 選択したCookieファイルを読み込めません。ファイルを選び直して保存し、アプリを再起動してください。 | 800 / 838 px (1.05) | 対応なし |
| [起動設定・停止記録使用中](empty/1280x800_dark/startup-lock-busy/port.png) | 4件 24px / Noto Sans JP / 400・16px / Noto Sans JP / 400・18px / Noto Sans JP / 600 | 2 / 2 / 1 / 1 / 2 / 1 | outlined 3 / text-link 6 | 通信記録が使用中のため確認できません。ほかの処理が終わってからアプリを再起動してください。確認できるまで通信は停止しています。 | 786.39 / 786 px (0.98) | 対応なし |
| [起動設定・旧形式判別不可](empty/1280x800_dark/startup-legacy-uncertain/port.png) | 4件 24px / Noto Sans JP / 400・16px / Noto Sans JP / 400・18px / Noto Sans JP / 600 | 2 / 2 / 1 / 1 / 2 / 1 | outlined 3 / text-link 6 | 保管領域の状態を確認できません（旧形式の判別不能）。バックアップから復元してください。 | 765.39 / 765 px (0.96) | 対応なし |
| [起動設定・旧DB複製不可](empty/1280x800_dark/startup-legacy-copy-error/port.png) | 4件 24px / Noto Sans JP / 400・16px / Noto Sans JP / 400・18px / Noto Sans JP / 600 | 2 / 2 / 1 / 1 / 1 / 2 / 1 | outlined 3 / text-link 7 | 旧DBの複製作成に失敗しました。保存先・容量・旧DBの使用状態を確認して再起動してください。 | 800 / 838 px (1.05) | 対応なし |
| [起動設定・停止履歴移行要](empty/1280x800_dark/startup-stop-migration/port.png) | 4件 24px / Noto Sans JP / 400・16px / Noto Sans JP / 400・18px / Noto Sans JP / 600 | 1 / 2 / 2 / 1 / 1 / 2 / 1 | filled-square 1 / outlined 3 / text-link 6 | 認証・通信の停止履歴と保存先の所有状態を安全に照合できません。通信は停止しています。保存済みデータを削除せず、停止履歴の確認と正式な移行を行ってください。 | 800 / 838 px (1.05) | 対応なし |
| [停止履歴の確認と移行案内](empty/1280x800_dark/stop-history-guide/port.png) | 1件 20px / Noto Sans JP / 500 | 1 / 1 / 1 | filled-square 3 | まだ確認していません。 | 673 / 673 px (0.84) | 対応なし |
| [正式移行のダイアログ](empty/1280x800_dark/stop-migration-dialog/port.png) | 4件 24px / Noto Sans JP / 600・18px / Noto Sans JP / 600 | 1 / 1 / 2 / 1 / 1 / 1 / 1 / 2 | filled-square 2 / outlined 4 / text-link 1 | 移行はまだ始まっていません。 | 752 / 1088 px (1.36) | 対応なし |
| [今回の429・再試行待機](empty/1280x800_dark/request-wait/port.png) | 検出0 | 2 | text-link 1 | × [未認証/期限切れ] Cookieファイルが設定されていません。設定から選んでください。 / HTTP 429のため再試行待機中: 試行 1/3・残り約5秒（待機設定 5秒）。今回の429は設定した総試行回数（初回を含む）の範囲で再試行します。共有STOP履歴には記録しません。 | 178.98 / 179 px (0.22) | 対応なし |
| [今回の429・試行上限と共有停止](empty/1280x800_dark/request-limit/port.png) | 検出0 | 2 | text-link 1 | × [未認証/期限切れ] Cookieファイルが設定されていません。設定から選んでください。 / 今回のHTTP 429: この操作の試行 3/3。この操作自身が429の試行上限に達したため、共有停止しました。このポリシーを共有する取得をこの起動中は停止しています。共有STOP履歴には記録しません | 262.98 / 263 px (0.33) | 対応なし |
| [自分のランキング（検索結果）](empty/1280x800_dark/personal-results/port.png) [並置](empty/1280x800_dark/personal-results/paired.png) | 検出0 | 8 / 2 / 1 / 2 / 1 / 5 | filled-square 3 / icon-only 2 / outlined 3 / text-link 3 | 借用アカウント・0件表示 / 0件を条件で除外・これで全部 / 条件に一致する作品は0件です。条件を変更してください。 | 418.52 / 701 px (0.88) | 自分のランキング（検索結果） Δ初期 +196.52 px・Δ縦 +243 px・Δ行 +2・Δ最大行 +2 |

[設定全体](empty/1280x800_dark/settings/port-all-groups.png)

| 設定グループ | 見出し様式 | 行ごとの要素数 | ボタン様式 | 空・表示状態 | 縦の長さ | Flet 基準との差 |
|---|---|---|---|---|---:|---|
| [Pixiv Cookieファイル](empty/1280x800_dark/settings/group-01.png) | 3件 16px / Noto Sans JP / 400・18px / Noto Sans JP / 600 | 2 / 2 / 1 / 1 / 1 / 2 / 1 | outlined 2 / pill 8 | 設定フォーム | 710 px | アカウントの見出し間隔 508 px と比較 +202 px |
| [フォロー中リストの同期](empty/1280x800_dark/settings/group-02.png) | 1件 16px / Noto Sans JP / 400 | 2 | pill 2 | 設定フォーム | 200 px | アカウントの見出し間隔 508 px と比較 -308 px |
| [借用プレミアムの状態](empty/1280x800_dark/settings/group-03.png) | 1件 16px / Noto Sans JP / 400 | 1 / 2 / 1 | pill 3 | 設定フォーム | 272 px | 対応なし |
| [ローカルタグ補完モデル（任意）](empty/1280x800_dark/settings/group-04.png) | 1件 16px / Noto Sans JP / 400 | 1 / 1 | 0件 | 設定フォーム | 256 px | 対応なし |
| [保存](empty/1280x800_dark/settings/group-05.png) | 1件 16px / Yu Mincho / 400 | 1 / 1 / 1 / 1 / 1 / 1 | 0件 | 設定フォーム | 666 px | 保存ポリシー・通知の見出し間隔 447 px と比較 +219 px |
| [通信](empty/1280x800_dark/settings/group-06.png) | 1件 16px / Yu Mincho / 400 | 1 / 1 / 1 / 1 / 1 / 1 / 1 | 0件 | 設定フォーム | 650 px | 詳細設定の見出し間隔 629 px と比較 +21 px |
| [作品メタデータ](empty/1280x800_dark/settings/group-07.png) | 1件 16px / Yu Mincho / 400 | 1 | 0件 | 設定フォーム | 182 px | 作品メタデータの見出し間隔 209 px と比較 -27 px |
| [絵柄・構図の推論](empty/1280x800_dark/settings/group-08.png) | 1件 16px / Yu Mincho / 400 | 1 | 0件 | 設定フォーム | 238 px | 絵柄・構図の推論の見出し間隔 225 px と比較 +13 px |
| [キャッシュ](empty/1280x800_dark/settings/group-09.png) | 1件 16px / Yu Mincho / 400 | 1 / 1 / 1 / 1 | 0件 | 設定フォーム | 342 px | 詳細設定の見出し間隔 629 px と比較 -287 px |
| [Windows](empty/1280x800_dark/settings/group-10.png) | 1件 16px / Yu Mincho / 400 | 1 / 1 | 0件 | 設定フォーム | 270 px | 詳細設定の見出し間隔 629 px と比較 -359 px |
| [エクスポート](empty/1280x800_dark/settings/group-11.png) | 1件 16px / Yu Mincho / 400 | 1 / 1 | pill 2 | 設定フォーム | 338 px | エクスポートの見出し間隔 144 px と比較 +194 px |

## empty/1920x1080_dark

| 画面 | 見出し様式 | ツールバーの行（各行の要素数） | ボタン様式 | 空・表示状態 | 縦の長さ | Flet 基準との差 |
|---|---|---|---|---|---|---|
| [キュー](empty/1920x1080_dark/queue/port.png) [並置](empty/1920x1080_dark/queue/paired.png) | 2件 15px / Yu Mincho / 700 | 2 / 8 / 1 / 2 | filled-square 1 / outlined 8 / text-link 3 | 現在のジョブはありません | 736.52 / 737 px (0.68) | キュー Δ初期 +146.52 px・Δ縦 +10 px・Δ行 +2・Δ最大行 -1 |
| [失敗リスト](empty/1920x1080_dark/failed/port.png) [並置](empty/1920x1080_dark/failed/paired.png) | 1件 15px / Yu Mincho / 700 | 1 / 1 / 9 / 2 | filled-square 1 / icon-only 4 / outlined 6 / text-link 1 | 失敗はありません 失敗した取得は0件です | 736.52 / 737 px (0.68) | 失敗リスト Δ初期 -5.48 px・Δ縦 -5 px・Δ行 +1・Δ最大行 +0 |
| [重複整理](empty/1920x1080_dark/dedup/port.png) [並置](empty/1920x1080_dark/dedup/paired.png) | 1件 15px / Yu Mincho / 700 | 5 / 4 / 1 | filled-square 2 / outlined 3 / text-link 4 | まだスキャンしていません。 / 0 / 0 組を表示（台帳全体 0 組）。旧データベースは読み取り専用です。対応付けを確定するには管理保存先でスキャンしてください。 | 736.52 / 737 px (0.68) | 重複整理 Δ初期 +6.52 px・Δ縦 +7 px・Δ行 +1・Δ最大行 +1 |
| [学習](empty/1920x1080_dark/learning/port.png) [並置](empty/1920x1080_dark/learning/paired.png) | 1件 14px / Yu Mincho / 600 | 2 / 1 / 2 / 1 / 1 | text-link 7 | 通信に失敗しました。30秒ごとにもう一度試しています。 | 736.52 / 837 px (0.78) | 学習 Δ初期 +499.52 px・Δ縦 +600 px・Δ行 +3・Δ最大行 +0 |
| [ランキング](empty/1920x1080_dark/ranking/port.png) [並置](empty/1920x1080_dark/ranking/paired.png) | 検出0 | 13 / 5 | filled-square 2 / icon-only 2 / outlined 2 / pill 4 / text-link 1 | 公式ランキング · 0 件 · これ以上のページはありません / 取得した条件に該当する作品がありません。 | 736.52 / 737 px (0.68) | ランキング Δ初期 +586.52 px・Δ縦 -1 px・Δ行 +0・Δ最大行 +3 |
| [ライブラリ](empty/1920x1080_dark/library/port.png) [並置](empty/1920x1080_dark/library/paired.png) | 1件 15px / Noto Sans JP / 700 | 13 / 3 | filled-square 3 / text-link 12 | 保存済み作品がありません | 736.52 / 737 px (0.68) | ライブラリ Δ初期 +592.52 px・Δ縦 -5 px・Δ行 +0・Δ最大行 +4 |
| [マイブックマーク](empty/1920x1080_dark/bookmarks/port.png) [並置](empty/1920x1080_dark/bookmarks/paired.png) | 2件 15px / Noto Sans JP / 700 | 7 / 3 / 1 | filled-square 2 / icon-only 1 / text-link 7 | ブックマークはありません | 736.52 / 737 px (0.68) | マイブックマーク Δ初期 +592.52 px・Δ縦 -5 px・Δ行 +1・Δ最大行 +1 |
| [おすすめ](empty/1920x1080_dark/recommend/port.png) [並置](empty/1920x1080_dark/recommend/paired.png) | 6件 20px / Noto Sans JP / 400・15px / Noto Sans JP / 700 | 6 / 3 / 3 / 3 / 3 / 3 / 1 | outlined 2 / text-link 20 | 保存結果を確認できない項目があります：もっと見る登録中の作者の新着・好みの傾向から | 736.52 / 741 px (0.69) | おすすめ Δ初期 +594.52 px・Δ縦 +599 px・Δ行 +5・Δ最大行 +0 |
| [直近のバックアップ](empty/1920x1080_dark/history/port.png) [並置](empty/1920x1080_dark/history/paired.png) | 1件 15px / Noto Sans JP / 700 | 4 | filled-square 1 / outlined 2 / text-link 1 | バックアップ日時が登録された作者はいません。 | 736.52 / 737 px (0.68) | 直近のバックアップ Δ初期 +674.52 px・Δ縦 -5 px・Δ行 +0・Δ最大行 +0 |
| [設定](empty/1920x1080_dark/settings/port.png) [並置](empty/1920x1080_dark/settings/paired.png) | 14件 20px / Yu Mincho / 500・16px / Noto Sans JP / 400・18px / Noto Sans JP / 600・16px / Yu Mincho / 400 | 1 / 2 / 2 / 1 / 1 / 1 / 2 / 1 / 2 / 1 / 2 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 1 / 3 | outlined 2 / pill 20 | 未確認の更新結果はありません。必要なときだけアカウントを更新できます。 / 状態 無効 | 972 / 4827 px (4.47) | 設定 Δ初期 +308 px・Δ縦 +2389 px・Δ行 +31・Δ最大行 +2 |
| [自分のランキング](empty/1920x1080_dark/personal/port.png) [並置](empty/1920x1080_dark/personal/paired.png) | 検出0 | 9 / 1 / 2 / 1 / 5 | filled-square 3 / icon-only 1 / outlined 3 / text-link 3 | 借用プレミアムをONにして、借用アカウントを更新してください。 | 736.52 / 737 px (0.68) | 自分のランキング Δ初期 +515.52 px・Δ縦 -1 px・Δ行 +1・Δ最大行 +3 |
| [定期チェック](empty/1920x1080_dark/scheduler/port.png) | 1件 20px / Noto Sans JP / 500 | 3 / 1 / 5 | filled-square 6 | 実行履歴0件 | 554.53 / 555 px (0.51) | 設定・保存ポリシー・通知（参考） Δ初期 —・Δ縦 +108 px・Δ行 —・Δ最大行 — |
| [メタデータ補完](empty/1920x1080_dark/metadata/port.png) | 1件 20px / Noto Sans JP / 500 | 2 / 1 | filled-square 2 / outlined 1 | メタデータ: 0 / 0 件（残り 0） / 取得処理: 未実行 | 554.53 / 555 px (0.51) | 設定・作品メタデータ（参考） Δ初期 —・Δ縦 +346 px・Δ行 —・Δ最大行 — |
| [ダウンロード](empty/1920x1080_dark/download/port.png) [並置](empty/1920x1080_dark/download/paired.png) | 検出0 | 1 / 2 / 1 / 6 / 1 / 1 / 4 / 1 | filled-square 3 / icon-only 5 / outlined 1 / pill 5 / text-link 1 | フォロー中の作者がまだ取り込まれていません。設定の「フォロー中リストの同期」を実行してください。 / 左の一覧でチェックすると、ここに積まれます | 736.52 / 737 px (0.68) | ダウンロード Δ初期 +37.52 px・Δ縦 +38 px・Δ行 +2・Δ最大行 +0 |
| [閲覧履歴](empty/1920x1080_dark/view-history/port.png) [並置](empty/1920x1080_dark/view-history/paired.png) | 1件 20px / Noto Sans JP / 400 | 2 / 1 | outlined 1 / text-link 2 | 0件 | 287.5 / 288 px (0.27) | 閲覧履歴 Δ初期 -329.5 px・Δ縦 -329 px・Δ行 +1・Δ最大行 -1 |
| [メタデータ台帳（展開）](empty/1920x1080_dark/metadata-ledger/port.png) | 1件 20px / Noto Sans JP / 500 | 2 / 1 / 1 / 1 / 1 / 4 / 1 | filled-square 9 / outlined 1 | メタデータ: 0 / 0 件（残り 0） / 取得処理: 未実行 | 554.53 / 761 px (0.7) | 設定・作品メタデータ（参考） Δ初期 —・Δ縦 +552 px・Δ行 —・Δ最大行 — |
| [共有ログ（展開）](empty/1920x1080_dark/shared-log/port.png) | 検出0 | 1 | text-link 1 | 合成ログ0件 | 171.98 / 172 px (0.16) | キュー内ログ（参考） Δ初期 —・Δ縦 +16 px・Δ行 —・Δ最大行 — |
| [共有ログ（折りたたみ）](empty/1920x1080_dark/shared-log-collapsed/port.png) | 検出0 | 1 | text-link 1 | 折りたたみ状態 | 31.98 / 32 px (0.03) | キュー内ログ（参考） Δ初期 —・Δ縦 -124 px・Δ行 —・Δ最大行 — |
| [永続429停止の回復ダイアログ](empty/1920x1080_dark/stop-recovery/port.png) | 1件 20px / Noto Sans JP / 500 | 1 / 2 | filled-square 1 / text-link 1 | 過去のHTTP 429（アクセス制限）によるSTOPが記録されています。この回復では解除できません。今回の429の設定待機・再試行とは別の永続停止履歴で、再起動しても残ります。停止履歴・所有情報を削除 | 372 / 372 px (0.34) | 対応なし |
| [中断GETの回復ダイアログ](empty/1920x1080_dark/stop-uncertain/port.png) | 1件 20px / Noto Sans JP / 500 | 1 / 2 | filled-square 1 / text-link 1 | 前の取得の結果が不確かなため、新しい取得を停止しています。画面上部の「取得停止の確認・回復」を開き、回復手順を確認してください。 | 330 / 330 px (0.31) | 対応なし |
| [起動設定・参照先不可](empty/1920x1080_dark/startup-reference/port.png) | 4件 24px / Noto Sans JP / 400・16px / Noto Sans JP / 400・18px / Noto Sans JP / 600 | 2 / 2 / 1 / 1 / 1 / 2 / 1 | outlined 3 / text-link 7 | 設定した参照先を利用できません。接続と保存先を確認し、旧DBは使用中でない凍結コピーを選択して再起動してください。 | 838.39 / 838 px (0.78) | 対応なし |
| [起動設定・Cookie読込不可](empty/1920x1080_dark/startup-cookie-rejected/port.png) | 4件 24px / Noto Sans JP / 400・16px / Noto Sans JP / 400・18px / Noto Sans JP / 600 | 2 / 2 / 1 / 1 / 1 / 2 / 1 | outlined 3 / text-link 7 | 選択したCookieファイルを読み込めません。ファイルを選び直して保存し、アプリを再起動してください。 | 838.39 / 838 px (0.78) | 対応なし |
| [起動設定・停止記録使用中](empty/1920x1080_dark/startup-lock-busy/port.png) | 4件 24px / Noto Sans JP / 400・16px / Noto Sans JP / 400・18px / Noto Sans JP / 600 | 2 / 2 / 1 / 1 / 2 / 1 | outlined 3 / text-link 6 | 通信記録が使用中のため確認できません。ほかの処理が終わってからアプリを再起動してください。確認できるまで通信は停止しています。 | 786.39 / 786 px (0.73) | 対応なし |
| [起動設定・旧形式判別不可](empty/1920x1080_dark/startup-legacy-uncertain/port.png) | 4件 24px / Noto Sans JP / 400・16px / Noto Sans JP / 400・18px / Noto Sans JP / 600 | 2 / 2 / 1 / 1 / 2 / 1 | outlined 3 / text-link 6 | 保管領域の状態を確認できません（旧形式の判別不能）。バックアップから復元してください。 | 765.39 / 765 px (0.71) | 対応なし |
| [起動設定・旧DB複製不可](empty/1920x1080_dark/startup-legacy-copy-error/port.png) | 4件 24px / Noto Sans JP / 400・16px / Noto Sans JP / 400・18px / Noto Sans JP / 600 | 2 / 2 / 1 / 1 / 1 / 2 / 1 | outlined 3 / text-link 7 | 旧DBの複製作成に失敗しました。保存先・容量・旧DBの使用状態を確認して再起動してください。 | 838.39 / 838 px (0.78) | 対応なし |
| [起動設定・停止履歴移行要](empty/1920x1080_dark/startup-stop-migration/port.png) | 4件 24px / Noto Sans JP / 400・16px / Noto Sans JP / 400・18px / Noto Sans JP / 600 | 1 / 2 / 2 / 1 / 1 / 2 / 1 | filled-square 1 / outlined 3 / text-link 6 | 認証・通信の停止履歴と保存先の所有状態を安全に照合できません。通信は停止しています。保存済みデータを削除せず、停止履歴の確認と正式な移行を行ってください。 | 838.39 / 838 px (0.78) | 対応なし |
| [停止履歴の確認と移行案内](empty/1920x1080_dark/stop-history-guide/port.png) | 1件 20px / Noto Sans JP / 500 | 1 / 1 / 1 | filled-square 3 | まだ確認していません。 | 673 / 673 px (0.62) | 対応なし |
| [正式移行のダイアログ](empty/1920x1080_dark/stop-migration-dialog/port.png) | 4件 24px / Noto Sans JP / 600・18px / Noto Sans JP / 600 | 1 / 1 / 2 / 1 / 1 / 1 / 1 / 2 | filled-square 2 / outlined 4 / text-link 1 | 移行はまだ始まっていません。 | 1032 / 1088 px (1.01) | 対応なし |
| [今回の429・再試行待機](empty/1920x1080_dark/request-wait/port.png) | 検出0 | 2 | text-link 1 | × [未認証/期限切れ] Cookieファイルが設定されていません。設定から選んでください。 / HTTP 429のため再試行待機中: 試行 1/3・残り約5秒（待機設定 5秒）。今回の429は設定した総試行回数（初回を含む）の範囲で再試行します。共有STOP履歴には記録しません。 | 157.98 / 158 px (0.15) | 対応なし |
| [今回の429・試行上限と共有停止](empty/1920x1080_dark/request-limit/port.png) | 検出0 | 2 | text-link 1 | × [未認証/期限切れ] Cookieファイルが設定されていません。設定から選んでください。 / 今回のHTTP 429: この操作の試行 3/3。この操作自身が429の試行上限に達したため、共有停止しました。このポリシーを共有する取得をこの起動中は停止しています。共有STOP履歴には記録しません | 220.98 / 221 px (0.2) | 対応なし |
| [自分のランキング（検索結果）](empty/1920x1080_dark/personal-results/port.png) [並置](empty/1920x1080_dark/personal-results/paired.png) | 検出0 | 10 / 1 / 2 / 1 / 5 | filled-square 3 / icon-only 2 / outlined 3 / text-link 3 | 借用アカウント・0件表示 / 0件を条件で除外・これで全部 / 条件に一致する作品は0件です。条件を変更してください。 | 736.52 / 737 px (0.68) | 自分のランキング（検索結果） Δ初期 +514.52 px・Δ縦 -1 px・Δ行 +1・Δ最大行 +4 |

[設定全体](empty/1920x1080_dark/settings/port-all-groups.png)

| 設定グループ | 見出し様式 | 行ごとの要素数 | ボタン様式 | 空・表示状態 | 縦の長さ | Flet 基準との差 |
|---|---|---|---|---|---:|---|
| [Pixiv Cookieファイル](empty/1920x1080_dark/settings/group-01.png) | 3件 16px / Noto Sans JP / 400・18px / Noto Sans JP / 600 | 2 / 2 / 1 / 1 / 1 / 2 / 1 | outlined 2 / pill 8 | 設定フォーム | 710 px | アカウントの見出し間隔 508 px と比較 +202 px |
| [フォロー中リストの同期](empty/1920x1080_dark/settings/group-02.png) | 1件 16px / Noto Sans JP / 400 | 2 | pill 2 | 設定フォーム | 200 px | アカウントの見出し間隔 508 px と比較 -308 px |
| [借用プレミアムの状態](empty/1920x1080_dark/settings/group-03.png) | 1件 16px / Noto Sans JP / 400 | 1 / 2 / 1 | pill 3 | 設定フォーム | 272 px | 対応なし |
| [ローカルタグ補完モデル（任意）](empty/1920x1080_dark/settings/group-04.png) | 1件 16px / Noto Sans JP / 400 | 1 / 1 | 0件 | 設定フォーム | 256 px | 対応なし |
| [保存](empty/1920x1080_dark/settings/group-05.png) | 1件 16px / Yu Mincho / 400 | 1 / 1 / 1 / 1 / 1 / 1 | 0件 | 設定フォーム | 666 px | 保存ポリシー・通知の見出し間隔 447 px と比較 +219 px |
| [通信](empty/1920x1080_dark/settings/group-06.png) | 1件 16px / Yu Mincho / 400 | 1 / 1 / 1 / 1 / 1 / 1 / 1 | 0件 | 設定フォーム | 650 px | 詳細設定の見出し間隔 629 px と比較 +21 px |
| [作品メタデータ](empty/1920x1080_dark/settings/group-07.png) | 1件 16px / Yu Mincho / 400 | 1 | 0件 | 設定フォーム | 182 px | 作品メタデータの見出し間隔 209 px と比較 -27 px |
| [絵柄・構図の推論](empty/1920x1080_dark/settings/group-08.png) | 1件 16px / Yu Mincho / 400 | 1 | 0件 | 設定フォーム | 238 px | 絵柄・構図の推論の見出し間隔 225 px と比較 +13 px |
| [キャッシュ](empty/1920x1080_dark/settings/group-09.png) | 1件 16px / Yu Mincho / 400 | 1 / 1 / 1 / 1 | 0件 | 設定フォーム | 342 px | 詳細設定の見出し間隔 629 px と比較 -287 px |
| [Windows](empty/1920x1080_dark/settings/group-10.png) | 1件 16px / Yu Mincho / 400 | 1 / 1 | 0件 | 設定フォーム | 270 px | 詳細設定の見出し間隔 629 px と比較 -359 px |
| [エクスポート](empty/1920x1080_dark/settings/group-11.png) | 1件 16px / Yu Mincho / 400 | 1 / 1 | pill 2 | 設定フォーム | 338 px | エクスポートの見出し間隔 144 px と比較 +194 px |

## 未取得と実行記録

- Flet の実スクロール高・描画済み CSS 値は CanvasKit のため未取得。 実行中の Flet コントロールの宣言値とアクセシビリティ座標は各 original.json に収録。
- NiceGUI の赤い「本番DB」帯に写るパスは隔離した合成保存先。通常・空は作品等の合成データの区分。 定期チェックは認証待ちの初期表示、メタデータ補完の通常状態は合成の補完結果を入力した。
- 専用 Flet 画面のない項目は NiceGUI 画像。共有ログの参照は Flet のキュー内ログ。 Native のファイル選択画面、実認証・外部送信後の画面、正式な移行実行後の画面は採取対象外。
- 起動した子孫プロセスの終了確認。残存 0 件。 確認ポート 9 件。 [lifecycle.json](lifecycle.json)
- 条件、ペア、ブラウザが遮断した外部宛先とサーバー通信ガードの実測。 [summary.json](summary.json)
- 画面・条件ごとの数値差。 [tone_comparisons.json](tone_comparisons.json)
