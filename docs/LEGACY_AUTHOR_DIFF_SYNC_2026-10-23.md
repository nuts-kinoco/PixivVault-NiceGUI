# Phase8：旧DB・作者差分・物理ZIP・失敗履歴の安全な同期

## 操作と起動条件

旧FletのSQLiteを**静止した完全なオフラインコピー**として用意し、旧画像とZIPのルートを**読取専用**で指定する。元アプリを起動したままDBファイルだけをコピーしてWALを取りこぼしたものは使用しない。旧DBコピーは`works`と`failed_queue`の双方を持つ必要がある。

```text
--pixiv-cookie-file <認証Cookieの絶対パス>
--pixiv-download-root <旧アプリと独立した新管理root>
--failed-queue-readonly-db <静止済み旧DBコピー>
--legacy-diff-image-root <旧画像・作者ZIPの読取専用root>
```

本機能は明示指定時だけ有効になる。既存のブックマーク・単一作品ID・作者差分・管理ジョブ・ZIPエクスポートは指定なしで従来動作する。`--legacy-zip-export-root` は別途指定できる。旧DBコピー・旧メディアroot・Cookie格納ディレクトリと新管理保存先の重複は禁止。

「ダウンロード」画面で作者を指定し「選択作者の差分を確認」を押す。認証済みの作者一覧のillust/manga両bucketと全48件detailを検証し、旧`works`、同じ旧DBコピーの`failed_queue`、旧作者フォルダ/ZIPの既存`ReadonlyLocalMediaCatalog`による物理ページ判定、および管理root内の検証済みactive receiptから各作品を分類する。結果を表示するだけではダウンロードしない。`NEW/UPDATED/RESTORED/MISSING`のうち、保存の安全な証拠があるものだけを選択し、独立した「キューへ追加」で管理ジョブへ渡す。

確認済み`UPDATED`の管理receiptは既存SHA+相対path CASで世代更新し、古い世代を保持する。まだ管理保存のない旧DB由来の`UPDATED/RESTORED/MISSING`は、**新しい管理rootへの初回保存**として扱い、旧DBの`is_deleted`、旧ZIP、旧画像・旧失敗履歴を変更しない。管理root内に完全検証済みactive receiptが存在する作品では、そのreceiptと更新日を優先し、旧DBの削除フラグや欠損ZIPにより再ダウンロードを誤指示しない。

旧v1管理receiptや`updateDate`欠損を`SKIPPED`と推定せず`UNKNOWN`として保留する。旧ファイル名が曖昧、ZIPが壊れている、作者の保存物が全く見えない、ページ数が不明などの場合、欠損を決めつけず`UNKNOWN`にする。うごイラと小説は管理原寸画像ダウンロードの対応外であり、旧失敗履歴があっても通常イラストへ偽装投入しない。

## 削除・復帰の二軸と永続性

`MISSING`は**ローカル実物不足**、`RESTORED`は**旧DBで削除扱いだった作品のPixiv再出現**である。一方、旧DBの作品が現在のPixiv作者一覧に無い状態は別の「遠隔欠落（保留/確定）」として扱う。これらを同一状態に混ぜない。

旧Fletの差分順序は`NEW → UPDATED → RESTORED → ローカル実物判定`。旧DB行なしでNEW、update_dateが異なるとUPDATED（削除扱いと同時でもUPDATED優先）、同じ更新日で旧is_deleted=trueならRESTORED。そのあとでZIP目次＋フォルダページが明確に無い場合のみMISSING。元コードでは「保存先を見失った」可能性がある作者全体空や曖昧名は再取得を抑止する。旧DBに更新日が無い/比較できないときは新側ではUNKNOWNとし、空文字の日付一致による誤SKIPを防ぐ。

旧Fletは認証確認後、完全な一覧との差分から`missing_streak`を数え通常3回、高比率（母集団5件以上かつ欠落40%以上）なら5回で削除確定する。新側は旧DBが読取専用なので、**ユーザーによる明示「旧DBと照合して欠落を確認」**だけを1回の完全GET巡回として新管理root内のsidecar SQLiteにdurable記録する。表示プレビューだけでstreakを増やさない。巡回ごとに固定のscan_idで重複適用を拒否し、SQLiteトランザクションでstreakと巡回受入を同時確定する。複数clientからの同一操作の再試行は同じscan_idを使用する。作品の遠隔再出現はstreakを0へ戻すが、旧削除扱いからの復帰は管理receipt完全検証まで未完のまま残す。

認証失敗、illust/mangaいずれかの一覧欠落、masked、ページdetailの一部欠落、ID/作者/型不整合、GET timeout/403/404のときは完全な作者一覧として扱わず、欠落台帳も進めない。旧Fletの`both`で片側不可視かつ他側にIDがある場合の削除誤検知は継承しない。

全件一覧の検証に失敗し、従来の作者一覧GETだけが成功した場合は、プレビューに「全件一覧の確認不可」を明示し、旧DB由来の候補は`UNKNOWN`として操作を保留する。すでに管理rootで完全検証されたreceiptがある作品だけは通常の`UPDATED/SKIPPED`比較を維持し、保存受理時には既存CASで再確認する。削除・欠落streakの確定には使わない。

## 保全と現在の未受入境界

- 旧DBコピーは`immutable=1`/`query_only`で読み、サイズ・mtime・inode・全ファイルSHA-256を凍結して前後・受理前に再照合。変更時は判定と投入を拒否する。元DB・旧ZIP・旧画像・元設定・Cookie原本への変更は禁止。
- 旧画像/ZIPの所在判定は既存`ReadonlyLocalMediaCatalog`のindex/ファイル同一性チェックを使い、ZIPの中央ディレクトリを読むだけで画像を新たに再decodeしない。判定不能はUNKNOWN。
- sidecarは新管理rootのみ。`JobService`/管理intentの永続化・停止/再開/再試行、世代CAS、実ZIP出力の既存所有境界を維持する。
- 複数アプリ**プロセス**から同じmanaged rootを同時運用すること、旧元DBへの書き戻し・旧ZIPへの直接追記・旧失敗行削除、旧DB全件の安全な本番移行はまだ対応していない。
- このPhaseの合成受入と実Cookieの限定GET受入は区別する。実Cookieは許可済みPixiv GETだけ、新専用rootに限った作者差分DLだけで、実ID・実画像・レスポンス・Cookieのログ/証拠保管は禁止。

レビューSHA、pytest、ブラウザ・Windows EXEの実検証、CIと残るゲートは `docs/PROGRESS.md` のPhase8欄を参照する。未確認項目はGREENとしない。
