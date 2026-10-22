# 失敗リストの読取・検索・CSV移植

2026-10-23。段階5の限定マイルストーン。最終ゴールはPixivVault全体の移植完了であり、この単位を段階5全体の完了とはしない。

基準mainはPR #21の `5fee5ec91b06b6d9a56d40c75db398799dd10f31`。専用branch `phase5/failed-records-integration` に、既存候補の確認済み19ファイルをSHA-256一致で移した。元候補、jobs-pipeline、queue-state、共有側の未コミット差分は変更していない。

## 接続した操作

- 旧 `failed_queue` の7列を `mode=ro` / `query_only` の専用接続から読む。本文・日時・種別・再試行回数を表示し、読取失敗を正常0件へ変換しない。
- 原版 `database.py:384-392,1940-1972` と `gui.py:4082-4097,4151-4173,4330-4339` のカテゴリ優先順、タイトル/ID検索、保存日時降順、8件ページング、絞込全件CSVを静的照合。
- 検索、カテゴリ、ページ、行DOM、スクロールをタブ往復で維持。別clientは独立。画面離脱・閉鎖後の旧refreshと旧ブラウザ操作を世代で拒否する。
- DB読取はUIループ外。更新失敗時は既存の行とページを保持し、再読込成功時だけ新snapshotを採用してページを範囲内へ補正する。
- CSVは表示ページだけでなく絞込全件、UTF-8 BOM・7列・改行を保持。表計算式になり得る先頭記号はアポストロフィで無害化し、DBや画面の元値は変更しない。
- Windows nativeでは `ALLOW_DOWNLOADS` を当該新規アプリだけに設定し、保存ダイアログで利用者が保存先を選ぶ。ブラウザやOSの既定設定を変更しない。

再試行、個別削除、一括削除、実ダウンロードへの投入は未接続のままdisabled。実API読取だけのmodeで旧DBを指定しない場合は「未取得」と表示し、失敗0件と主張しない。

## 独立レビューと修正

DB/状態担当の独立レビューは原版との一致、合成SQLite/WAL・上限・CSV・世代について阻害指摘なし。担当自身が17pytestを実走した。

別のUI/配布レビューは、native検査がCSVボタンの有効表示だけで成功できる欠陥を指摘。`csv-gate-before.xml` に **1 failed / 3 passed** を保存した。修正では専用の新規保存先へのCSV実クリックと保存結果のBOM・7列・絞込3行全フィールド一致を必須にし、検査関数と最終合否の両方に保存済み条件を追加した。明示の生成SQLite/画像fixtureも必須にした。

保存ダイアログの追加操作時間は明示オプションで0〜120秒に制限し、全体deadlineとWM_CLOSE予約時間を維持する。CSV本体を検査器が代わりに書くことはない。ダイアログ入力は所有HWND/PIDを確認したdesktop agentで実施した。修正コードは別担当の独立再レビューでAPPROVE、関連71pytestを担当自身が実走した。

## 最終ローカル受入

証拠ルートはignoredの `artifacts/failed-records-integration-20261023/`。

| ゲート | 確認結果 |
|---|---|
| 全pytest | `full-final.xml`: **740 passed**、既存依存警告1件。compileall/pip check/diff検査成功 |
| 失敗リスト実Edge | `edge-first/result.json`: 1280×720/1920×1080、検索/分類/8件頁、17件/3件の実CSV保存と全フィールド、式無害化、DOM/scroll、2client、DB故障→復旧→正常0件、通常終了/port解放成功 |
| 既存実Edge | `edge-regression/runner-result.json`: gallery/learning/image races/libraryの4検査成功 |
| 製品・配布の同一性 | 製品48入力、コピー配布1050ファイル、合成fixture4ファイルのhash不変/一致 |
| コピーexe | SHA-256 `c599a927b81e020ef7e645b90944ec186ef69968eb9fb8c3d166b5539532446d` |
| Windows実操作 | `native-final/native.json`: 合成旧DB、保存画像、ブックマーク、失敗リスト、CSV保存ダイアログから新規CSV保存。絞込3行/7列/式無害化を検証 |
| Windows復帰・終了 | DPI168、GDI安定2標本ずつ、平均画素差 `0.000010670543521371293`（上限1）。10 owned processの通常終了、port解放、forced cleanupなし |

`native-dialog-discovery` はinterpreterで先行した実保存成功。最終受入は修正版の標準checkerと別フォルダーのコピーexeで行った `native-final` を使う。

## CIの依存取得問題と未達範囲

旧main run `39828938139` は再実行でも依存取得に8分43秒を要してジョブ10分枠でcancelled。成功とは扱わない。ログは `mirror+file:/etc/apt/apt-mirrors.txt` からHTTP Azure mirrorを選んでおり、既存のsources.list/ubuntu.sourcesだけの置換では効いていなかった。既存の公式Ubuntu HTTPS置換対象へこのmirror-listファイルを1つ追加し、依存・試験・タイムアウト・合格基準は変更していない。新しいPR head/main CIの成功は別途確認する。

最初のbuildログをPowerShellで全streamリダイレクトした試行は、既存PyInstaller警告をNativeCommandErrorへ昇格させ失敗した。OSレベルのsubprocessログ取得に変えて同一buildスクリプトを実行し、exit0と製品ソース不変を確認した。ライブラリの警告を消すための依存変更はしていない。

PR最初のhead `03d3bc90113a37ff85e43b7da0cf77ed91d30bd5` はCIの最小test環境で、fixture生成用importから任意依存Playwrightを読み込んで収集に失敗した。ブラウザの実行関数内だけでPlaywrightをimportするよう分離し、同packageを使用不能にした独立子プロセスでもfixture生成と整合検査が通る回帰を追加した。CIへ不要なブラウザ依存を追加する回避はしていない。製品src・配布exeの入力は変更なし。

修正後は `full-ci-fix.xml` の **741 passed** と `edge-fixture-import/result.json` の旧DB/画像/ブックマーク実操作が成功。別担当が任意依存を遮断する回帰を独立実走し、限定差分をAPPROVEした。

本番DB/実画像/Cookieは今回使用・変更していない。実失敗作品の再試行・記録削除、永続ジョブと旧failed_queueの同期、実ダウンロード、全DPI/別PCは未受入。次は既存ジョブ実装の停止・再開・後続キュー進行を独立レビュー付きで統合する。
