# 合成データの学習永続接続

基準は main `bf1b04bb10881ac65d515376e230680a839f36a3`。専用worktreeの `phase3/learning-persistence` で実装する。原版・共有worktree・本番DB・実画像・Cookie・実アカウント/APIは変更・利用しない。

## 原版と照合した仕様

- 原版 `discovery_tab.py:146-165` は「メモリ上の直前1手」のUndoを明示し、複数手への巻き戻しを対象外としている。同 `1988-1994` もUndo時に直前判定を空にする。再起動後のDB履歴からUndoを再構成しない。
- 原版 `discovery_tab.py:4661-4682` はDBから既判定IDを取得して候補から除外する。読取失敗時は候補生成を中止し、空の履歴へフォールバックしない。移植デモは同じ規則を固定の合成120作品へ適用し、元の合成作品順を維持する。実API候補取得や推薦順の移植とは区別する。
- 原版 `discovery_tab.py:1771-1811` は保存結果の成否を区別し、非UIスレッドでDB処理する。同作品の再判定は旧集計を逆再生して置換する。旧SQLのUndoは現在の判定の削除であり、置換前の判定の復元ではない。原版の画面送りは `1861-1873` で保存完了より先に行うが、移植デモでは既存の「保存成功後だけ進む」契約を維持する。失敗を表示し同じ作品から再試行できるようにするための差である。

従来のデモ `LearningState` は複数手Undoを許していた。原版仕様との差を `test_original_one_step_undo_preserves_earlier_judgments` で再現（1 failed）し、直前の判定だけを保持する形へ修正した。従来テストの多段Undoという誤った期待も原版に合わせ、二度目のUndo拒否と以前の判定保持を明示的に確認する。台帳自体のLIFO・重複operation ID検査は独立した試験で維持する。

## 接続契約

既定は画面内のメモリ台帳。`--learning-sandbox <絶対パス>` 指定時だけ、合成デモ専用の保存先を使用する。`--check` は保存先を作成・接続しない。初期化失敗時にメモリへ切り替えて保存成功を装わない。

DB資源はアプリが所有し、各画面が別のRepositoryセッションを持つ。DB I/Oは専用executorで実行する。受付を返す前に同期的にexecutorへ登録し、UIのawaitをキャンセルしても受理済み書込みをキャンセルしない。画面のcloseは同じFIFOへsessionの解放を登録する。アプリのDBを画面ごとに閉じず、終了時は受理済み処理を待って資源を解放する。サービス終了開始後の遅い完了通知から画像を再要求しない。

画面は保存commandを受理した時点で判定・Undo・画像再読込を抑止する。DBのcommit成功後だけ判定位置と直前Undoを更新する。保存失敗時は同じ作品・画像・判定位置を保持し、同じoperation IDを使って再試行する。タブ離脱は保存中も可能。画面削除後の完了通知は画面を復活させない。

別セッションが更新した判定へのUndoは、既存の単調増加世代と`BEGIN IMMEDIATE`による原子的検査で拒否する。画面へ説明し、DBの最新状態から候補を読み直して古いUndoを解消する。再読込に失敗した場合も判定を再開しない。

operation IDの永続的な冪等性ジャーナルは今回の範囲外。単一画面の連打・同一操作の重複・失敗後の再試行を扱い、画面再生成やアプリ再起動ではDBに残った判定を正本として候補を再構築する。

## 検証記録

実装と独立コードレビュー、単体・Edgeの限定受入を完了。Windows配布と公開SHA/CIは後続の検証対象とし、未実行を合格に含めない。

- 保存commandの二重受理、偽の完了command、失敗後の同一ID再試行、close後完了を対象にした5件は実装前REDを確認。
- 保存状態の分離後、既存の学習画面を含め26件が成功。直前1手Undoの仕様修正後は28件成功。
- 起動オプション、`--check`非接続、初期化失敗時の起動拒否、既定メモリ経路は45件成功（上記Undo修正前の別実行）。
- 独立レビューで、task作成からexecutorへの登録までの間にshutdownされると、受付済み判定が登録されない経路を指摘。同期submitへ修正し、受付直後のclose/cancelとアプリ終了後の完了通知を試験対象にした。
- 独立レビューで、保存状態の再読込失敗後、遅い画像load/errorが復旧案内を消す経路を指摘。両イベントの回帰2件が修正前失敗（`recovery-red-confirmed.xml`）、保存状態のguard追加後成功（`recovery-green.xml`、2 passed / 0.88秒）。
- 保存先フォルダーの識別だけでは、DB本体を別SQLiteへ差替えた際に旧constructorが外来DBを変更する問題を独立レビューで発見。foreign SQLite/別の合成instanceの2反例が修正前失敗（`db-identity-red.xml`）。DB内v2 identityの事前照合後、service/repository 39件成功（`service-identity-green.xml`）。識別改変・DB欠落・identity作成/検査の非UI実行も追加。
- 統合pytestは **266 passed / 8.33秒**（`integrated-pytest.xml`）。実SQLiteロック中もevent loopが応答し、受理直後closeで保存をdrainすること、await取消後もqueued writeが完了すること、同値/ABAの古いUndo拒否、成功receipt・失敗後同ID再試行を確認。
- `tools/check_learning_persistence_browser.py --channel msedge --output artifacts/learning-persistence-20261022/browser-first` は10受入項目が成功。4判定/集計、直前1手Undo、reload/restart後の除外、実SQLiteロック中のランキング検索、失敗時の画像/世代/件数不変、retry後の一度だけの保存、2画面の古いUndo拒否、ページclose後の保存を実操作。console/page error 0、source hash不変、2回の自サーバー停止とポート解放を確認。**このサーバー停止はrunnerのterminateであり、正常shutdown/drainの受入とは区別する。**
- primeが`real-lock-failure.png`と`learning-persistence-final.png`の全画面を目視。保存失敗の説明と再試行操作、判定済み6件から作品0007へ再開する画面を確認した。
- 既存の一覧・メモリ学習・画像競合3scriptも実Edgeで全てexit 0（`browser-regression/runner-result.json`）。source hash不変。新しい永続経路だけを確認して既存経路の受入へ代用していない。
- 原`database.py`のSHA-256は既知値と一致。constructor以外115メソッドのAST一致、専用worktreeのimport先、pip check、git diff --checkを確認（`source-check.json`）。
- 実装担当と別のレビュー担当がUI/state/services・v2 identity・ブラウザ検証/CIを再確認し、上記修正後の対象にblocking指摘なし。workerのexec/apply呼出が応答しなかったため、変更の適用と全コマンド実行はprimeへ集約し、レビューは別担当のreadと証拠照合で行った。未実行のworker独立pytestを成功に含めない。
- Windows配布物の通常終了・再起動受入と公開SHA/CIは、このコード単位のコミット後に実施して追記する。

生のXML・JSON・画面画像は ignored `artifacts/learning-persistence-20261022/` に保存する。保存機能の限定受入と、全画面・全DPI・性能・本番接続を含むフェーズ全体の完了は分けて記録する。
