# P3-345 X07/X08 ごみ箱移動と managed quarantine の契約読取監査

**判定：現管理rootの「退避コピー＋通常一覧除外」は X07/X08 の主要な利用者効果を限定的に達成。原版の「物理的に移動する」完全一致は未達（原152のX07/X08を一括PASSにしない）。**  
2026-10-30 JST／worker-11。保存済み原本・現在のsource・tests・既存Edge証拠だけを読取監査した。新GUI/pytest/API/ネットワーク、Claude、Git、ファイル移動・削除は実行していない。変更はこの新規docのみ。

## 照合対象・基準

- 機能台帳：`docs/P3_343_REMAINING_85_FUNCTIONAL_EVIDENCE_2026-10-30.md:1089-1153` はX07/X08をともに **C** とし、根拠を「copyであり、original physical move/容量解放でない」と記載。両IDのv8 `expected_full` は **NULL** であり、これから新しい逐語要件を創作しない。正式152全体の判定も本レビューでは変更しない。
- 原コードは `S:/Projects/PixivVault/gui.py` にあり、原リポジトリの `rg --files -g '*dedup_tab.py' -g '*dedup*.py'` では `dedup_tab.py` を発見できなかった。実動作比較は `gui.py:5093-5118,5500-5593,5826-5838,5595-5702` と原docsを採用した。原 `docs/design/DUPLICATE_DELETE_SPEC_2026-08-15.md:17-43,49-115,123-195,308-348`、`docs/DEDUP_COMPARE_UNIFIED_LIST_SPEC_2026-08-29.md:95-131,152-169` を参照。
- 現UIファイル名は **`src/pixivvault_nicegui/ui/duplicate_view.py`**。 `ui/managed_quarantine_view.py` ではない（UIディレクトリ一覧、`ui/shell.py:201-206` の生成を確認）。実行エンジンは `managed_quarantine.py`、`managed_generations.py`、`duplicate_inspection.py`、`managed_downloads.py`。
- 本日時点の読取専用SHA256：`ui/duplicate_view.py=ec3893561b753201a53d2901091459ff6a9eaf320f337b08124b231ffa8b42de`、`managed_quarantine.py=b1db903a1de41d7a1cd46ba79e126d67a4ff3d4989dbdeb55b1c1f45fbb14567`、`duplicate_inspection.py=1f5469053bae2f3474703727117ab463fe029abd6f98b7c5060635f27b15d7c8`、`managed_generations.py=b54057c873e2a74199da8d0ae9f4d37198c9beaa4b50c9392a25bac059d8d43b`。いずれも現ファイルの `Get-FileHash` 読取結果。

## 原機能の効果：文字上の「move」だけではない

1. **退避保存・元からの除去**：原 `gui.py:5093-5117` はフォルダ保存画像を `shutil.move`、ZIPエントリを `core.remove_entry_from_zip` で元ZIPから除去し、隔離先へ元bytesを書き出した後に `db.mark_quarantine_excluded(work_id,page,path)` する。原仕様§3-2/3-3は `Images/` 外の sibling quarantine、ZIPの元物理構成からの除去を確定（`DUPLICATE_DELETE_SPEC:49-115`）。
2. **通常一覧・再取得への影響**：原 `gui.py:5540-5589` は成功分だけ直近スキャンから落として再描画。原設計§4（`DUPLICATE_DELETE_SPEC:123-195`）はwork/page別の永続除外マークをDBに残し、再取得判定にも反映する。隔離されたページのみを除外し、残りのページを誤って全部欠損扱いしない。原 `gui.py:5604-5620,5672-5700` はごみ箱を空にして実物がなくなっても除外記録を残す。
3. **X07/X08の操作**：原 `gui.py:5500-5538,5540-5593,5826-5838` と統合仕様 `DEDUP_COMPARE_UNIFIED_LIST_SPEC:104-131,152-169` は、1組操作と複数組操作の共通確認、組見出し付き選択の表示、1組1件以上の生存、安全な部分成功件数、成功対象だけの一覧除去を要求。**原仕様 `DUPLICATE_DELETE_SPEC:345-348` は「戻す」ボタンをX07/X08の必要項目から明示的に外している**。原の「あとから戻せる」は隔離保存の性質であり、現UIに復元ボタンがないこと単独ではX07/X08のP2ではない。

## 現製品の実装された効果（a/bの判断）

| 観点 | 現状根拠 | 判定 |
|---|---|---|
| 管理rootのごみ箱保存 | `managed_quarantine.py:50-79,127-147,193-255,282-374`。所有marker検証後に元データを検証して `quarantine/incomplete` へコピー、bytes/SHA/画像寸法を照合、`operations/<operation_id>/` にmanifestとともに公開する。選択分だけ成功／失敗に分ける。 | **機能あり**。コピーは検証済みの隔離保存であり、単なる画面上の非表示ではない。 |
| 通常一覧・保存系から除外 | `managed_quarantine.py:340-364` の成功ページだけを `managed_generations.py:526-564` のv3 `exclusions` にCAS commit。`managed_downloads.py:381-440,508-515` は可視ページ投影と再読込を禁止。`duplicate_inspection.py:236-244,316-345` はスキャンから成功分だけ除外、辺を再構成。`ui/duplicate_view.py:406-454` は同じ確認経路で1組/複数組の成功件数を表示し、`on_changed` によるライブラリ更新を呼ぶ。 | **機能あり**。単純copyだけではなくdurable exclusionと表示の再連結が実装済み。 |
| 再取得抑止・永続性 | `managed_generations.py:369-489,596-658` は排除マスク・manifest・元receipt証拠を検証し、欠落・改変を fail closed。`managed_downloads.py:410-455` は隔離ページの再保存を拒否。`tests/test_managed_quarantine.py:107-120,198-230` と `tests/test_managed_quarantine_consumers.py:25-85,116-170` は再起動・保存・キャッシュ・書出しの回帰を示す。 | **owned managed rootに限って実装**。旧アプリの独立したDB/自動バックアップに同じ除外効果が及ぶという証拠はない。 |
| receiptの更新／物理的可逆移動 | `managed_quarantine.py:214-230,340-374` は**元receipt/元pageを編集・削除しない**。更新するのは `active-generations.json` のv3 exclusions/operations。`managed_generations.py:526-587`。`restore` APIは `managed_quarantine.py:376-400` にあり元bytes/SHAとcopyを検証した後でexclusionsを解除、operationを`restored`へ遷移（`managed_generations.py:566-587`）。テスト `test_managed_quarantine.py:233-241` と `test_managed_quarantine_trash.py:37-58` で復元済み。 | **receipt更新・元bytes物理移動は未実装**。**可逆な論理除外／復帰は実装済み**。内部APIはあるが `DuplicateView` にrestore操作は公開されていない（原X07/X08には必須ではない）。 |
| 容量解放 | `ui/duplicate_view.py:64-69` は「コピーを保存、元証拠保持、空き容量は増えない」と明示。`managed_downloads.py:482-506` は元receiptと元画像のbytesを物理容量に算入する。 | **未達**。原Fletの元ZIP/フォルダからの物理的な除去とは効果が異なる。 |
| 旧ライブラリread-only | `services.py:422-429` の `ManagedDuplicateInspection(self.managed_downloads, undecidable_db=undecidable_db)` と `duplicate_inspection.py:202-244`。旧DBを使うのは明示された曖昧台帳の読み取り側だけで、近似候補スキャンは `owner.saved_receipts()` 由来。`ui/shell.py:201-206` はこれをX画面へ渡す。 | **旧ライブラリ向け隔離は未接続**。read-only旧DB/Images/ZIP保護を破らずに「旧版同等の物理移動」を実行することは許されない。 |

**(a) 許容代替の限界**：readonly旧ライブラリは元DB/Images/ZIPを一切変更できないため、専用managed rootへ検証済みコピーを置き、**新アプリ側の全関係consumerで永続的に非表示・再取得抑止**する方式は、旧データ保護を守る「利用者効果の限定代替」として設計上合理的。ただし**現実装で旧ライブラリの近似候補から同じ機能が実行可能とは言えない**（現X画面はmanaged receipts専用）。さらに原Flet自身や別アプリのバックアップ判断まで除外できるとは主張できない。代替と原仕様同等の最終認定は分ける。

**(b) managed rootの可逆性**：正規のUIユーザー確認→owner検証→コピー検証→durable `exclusions` CAS更新→通常一覧から除外までは製品で実装済み。`receipt`はimmutableのままであり、**元ページを移動してからreceiptを書き換える機能は存在しない**。復旧は`restore(operation_id,expected_revision)`による論理復帰で実装済み。ただし明示的な通常UIからの復元導線は未接続（原X07/X08の必須条件ではない）。

## 保存済み実操作の根拠と判定限界

`artifacts/p3-318-x07-quarantine/edge-product-r3/result.json` SHA256 **`a7cc0915387ca2fcd057489982c9cfe37f40540549f5f74e76c208e0283caae1`**（今回 `Get-FileHash` で照合）。合成managed originalsを接続した現製品のEdge実操作で、`success=true`、**1280×720および1920×1080、それぞれ初回＋再起動**の4 runが成功し、`source_changed=false`。両幅の初回runは1組確認・取消・少なくとも1枚残す制約、複数組1確認、部分成功・失敗分のみ再試行、ライブラリ反映をnamed checksに記録する。

各幅とも初期 `complete_receipts=4`、`excluded_count=0` から、最終 `complete_receipts=4`、`excluded_count=3`、`operation_count=3`。例として合成work 123の表示ページが `[0,1,2]→[2]`、合成work 223が `[0]→[]`、ライブラリ総ページ可視数は初期6→最終3。**original_proofs のハッシュと `original_unchanged=true` は元画像・元receiptが残る事実を支持**する。再起動runでも可視ページ・除外・operationsが変わらず、`startup_gets=0`。page/console errors、external requests、remaining children、forced cleanupは0、exit code 0、port解放が保存される。ここから**物理移動・容量解放**は立証できない。

保存テスト `tests/test_managed_quarantine.py:78-241`、`tests/test_managed_quarantine_ui.py:32-191`、`tests/test_managed_quarantine_consumers.py:25-170`、`tests/test_managed_quarantine_trash.py:24-164` は、元receipt/画像不変、1組/複数組、失敗の部分計上、再保存不可、ライブラリと出力の除外、復帰API、trash copyと除外の別集計を裏づける。ただし本workerは試験を再実行しておらず、旧ライブラリの現物を操作していない。

## (c) 最小限の修正・残る原機能差分

1. **台帳の判定説明をまず分割**：X07/X08を単に「copyだからC」で片づけず、`管理root退避保存/永続UI除外/再取得抑止/1組・一括確認/成功件数/再起動保持` は保存証拠付きで**B相当の達成**、`元ZIPからのエントリ除去・元フォルダ実移動・容量解放` は**C未達**と補助欄に分ける。v8の正式逐語`expected_full=NULL`は補完推測せず、このレビューをもってX07/X08を正式全条件PASSへ変更しない。
2. **最小の画面上の明確化案（今回実装しない）**：`duplicate_view.py:63-73,388-398,450-454` の主要ボタン/確認/結果を「ごみ箱に隔離コピーを保存し、一覧から除外」と揃える。現画面には既にコピー・容量非解放の説明があるため**新しい操作フローや要件は不要**。copyが原版の物理移動だとの誤解だけを防ぐ。
3. **旧ライブラリ対応をする場合の最小安全方向**：readonly原画像/DB/ZIPを保持したまま、新アプリ専用の除外台帳をwork/page単位で持つ設計とし、原dataを写すかどうかの判断は元の保護境界で審査する。旧一覧・重複スキャン・再保存判定への接続を同一スナップショット契約で行う必要がある。**現在は未接続**であり、実旧ライブラリを対象に実行・削除する変更は不可。必要性が確定するまで新機能として製品へ追加しない。
4. **物理移動／容量解放は別の設計判断**：原仕様どおりにするには対象元bytesを消すか元ZIPを作り直す必要があり、旧データ保護と両立しない。owned managed rootでもimmutable receipt・original page proofとの整合を崩すため、単純な `shutil.move` / 元ファイル削除を最小修正として推奨しない。将来、ユーザーから**managed rootの物理容量解放**が必要と明示されたときに限り、元receipt・表示ページ番号・rollback/restoreを含む独立設計と別受入で検討する。現時点で進められる最小修正は**評価文言とUIの正確化**である。

**限定結論**：現隔離実装は単なるcopyではなく、管理rootで選択画像の退避・永続除外・再取得抑止・個別/一括確認・部分成功の可視化・論理復元を成立させている。元ファイルの物理除去と容量解放は残存未達。旧read-onlyライブラリへの同機能接続は未確認で、旧版に対する完全代替と同一視しない。新規P1/P2の根拠をこの静的監査範囲で立証せず、正式152のX07/X08全条件合格、native、性能を認定しない。

