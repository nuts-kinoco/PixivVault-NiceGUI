# 作者ID・フォロー選択からの管理ダウンロード（限定受入）

## 動作する範囲

- 元アプリの数値入力は**作者ID**。本移植では、既存の「単一作品ID」欄と別に作者ID欄を用意する。
- 作者ID直接選択とPixivフォロー一覧（公開・非公開、24件ページング）の読取専用GET。
- フォロー検索・48件追加表示・選択元`id|follow`の先行保持、検索後の選択維持、重複防止、個別解除。
- 作者の公開イラスト・漫画一覧を`profile/all`→`profile/illusts`48件単位のGETで取得。原版どおり更新日を受け取る。
- 新管理rootに保存済みの作品は完全検証したreceiptで判定。既存v1には`update_date`が無いため`UNKNOWN`。
- 作者差分候補は**NEWと更新日が比較可能なUPDATED**。v1保存物など日時不明の`UNKNOWN`、`RESTORED/MISSING`は安全側で自動再取得をしない。選択後の明示「キューへ追加」だけで永続ジョブが開始する。
- v2 accepted-intentはorigin、更新日、前世代のreceipt相対パスとSHAを厳格に保持。従来のv1 accepted-intent/JobService SQLiteは互換読込を維持する。
- v2新規・更新は専用`generations/`へimmutable保存し、全ページ検証後にactive pointerをCAS公開。旧世代は残し、再起動のライブラリはactive世代1件のみを参照する。active pointer確定直後のジョブDB失敗は再実行時に検証・再利用する。
- 同じ作業ownerの作者候補一括登録は全件検証後に原子的に公開、上限128。作者由来の新しい作品メタデータを優先し、従来のブックマーク単独選択の契約は維持。
- 既存のキュー停止・一時停止・再開・エラー再試行、ライブラリと共有ビューアを再利用。新しいキュー挙動を勝手に創作していない。

## 合成受入証拠

- Windows実Edge 1280×720 / 1920×1080、初回・同一保存先再起動、計4経路成功。
- 初回は新規作者1件2頁と、合成更新日・画素を変更したUPDATED2頁を原寸GET計4回。再起動ではGET 0。旧世代保持、active新世代、保存receipt・画像SHA・寸法・表示画像decode・キーボード送り・フォーカス復帰を確認。
- 他clientに未選択候補は表示されず、Cookie付きGETの通信先は許可されたPixivホストに限定、原寸画像GETにCookieを付与しない。
- browser console/page errors 0、外部browser通信0、通常終了4/4、port解放4/4。
- `artifacts/author-downloads-20261023/edge-v2-update-second/result.json`（Git追跡外。後続製品変更後は再受入すること）。

## 未実装・未受入・重要な不変条件

- **既存v1保存物の更新日比較**は不能。旧v1に更新日情報を捏造しないため`UNKNOWN`保持であり、原版DBによる`RESTORED/MISSING`の自動判定も未接続。
- **active indexの複数プロセス所有**：JobServiceのロックによって製品インスタンス単位の単一writer前提。Standalone `ManagedGenerations`を外部で複数独立使用する運用は非対応で、全CASをOSファイルロックへ置き換えてはいない。
- **active index欠損**：画像世代が存在するのにindexが消えた場合は起動を拒否する。初回世代公開前に空indexを耐久保存し、旧ライブラリへの無警告フォールバックを禁止。損傷時の自動復旧は未提供で、隔離検証と手動復旧が必要。
- **同日別ジョブ**：更新日だけではcrash replayの証拠にならないため、受理済みジョブID＋v2意図のSHA指紋をactive index version2へ固定。旧version1 indexの読取互換は維持するが、fingerprintのない世代を同一ジョブの再実行と推定しない。
- **停止されたinterruptedジョブ**：再起動時は元のaccepted-intentと現在activeな全頁検証済receipt・ジョブ指紋を照合し、一致時のみSQLiteを`done`へ耐久復元。不一致時は既存世代を変えず`error`へ移して明示再試行可能にする。利用者の手動停止とは区別する。
- **作者保存性能**：各ジョブは対象作品の現在active receiptを検証し、全作品の全頁decodeを毎回繰り返さない。起動・ライブラリrefresh・作者差分一覧のsnapshotでは全保存物を再検証する。大量作者バッチ・高解像度の定量計測はなお未受入。
- **原版DB・ZIP・保存命名の書込互換**、作者全件一括処理の継続ページング・容量超過時の任意範囲処理、作者一覧の大量描画性能、実Cookie作者差分DL、Windows配布exeでの新UIネイティブ受入は未確認。
- 完了済みreceipt、元DB、設定、元Cookie、原版画像、旧worktreeへ書き込み禁止。
- 未保証は電源断直前のWindows directory fsync、renameとSQLite完了commitの原子性、未検証の初回cold・連打・配布exe性能。

この限定マイルストーンはフェーズ5全体の完了を意味しない。上記の未実装を完了扱いにしない。
