# P3-346 管理root限定・明示物理移動の状態契約と合成検証

2026-10-30 JST／worker-11。対象は所有marker付き新managed rootの合成データと明示確認済み隔離選択のみ。原Flet・readonly legacy DB/images/ZIP・Cookie・実API・別担当機能は絶対に読取/変更/移動しない。既定は従来の**安全copy＋永続除外**のまま。

## 元の制約と現在の失敗経路

- `managed_quarantine.py:282-400` は検証済みcopy、immutable manifest、v3 CAS exclusionsと論理restore。`managed_generations.py:369-489` が有効なindex/manifest/receipt SHAを結合し、`526-587` がcommit/restoreする。
- `managed_downloads.py:242-307` の `_receipt()` はreceiptに記載された全ページのbytes/名前を元フォルダに要求し、`332-355` のcat snapshot、`517-538` のsource proof、`584,637-640` の再保存判定にも波及。**元sourceをunlink/renameするだけの案は不採用**。元receiptの上書きや不存在を黙認する案も不採用。
- UI実体は `ui/duplicate_view.py`（`ui/shell.py:201-206`）、managed専用 `ui/managed_quarantine_view.py` は存在しない。`duplicate_inspection.py:316-345` が現UIからcontrollerと実行を中継する。編集担当境界はprimeへ通知済み。

## 必要最小ファイルと担当合意

| ファイル | 必要な役割 | 現在の編集境界 |
|---|---|---|
| `managed_generations.py` | v3 copy互換を保ったv4移動index、操作state・manifest/source proof・欠損参照の厳格照合、CAS | worker-11所有 |
| `managed_quarantine.py` | 明示move受付、検証済みcopyへの同一ボリュームatomic source→quarantine rename、pending/restore/recover、fail closed | worker-11所有 |
| `ui/duplicate_view.py` | copy既定、moveは別の明示チェック＋確定ボタン、再起動後も管理ごみ箱から確認付きで復元 | prime追加承認受領・worker-11実装済み |
| `duplicate_inspection.py` | UI mode中継、owned move一覧とCAS付きrestore、scan失効を接続 | prime追加承認受領・worker-11限定実装済み |
| `managed_downloads.py` | 元receipt・original page proofを改竄せず、indexが証明した移転先SHA/bytes/寸法と一致する**欠損ページだけ**許容。その他の欠損/改竄・link/reparseは従来拒否 | prime追加承認受領・worker-11限定実装済み |
| `services.py` | 現行の`ManagedDuplicateInspection`接続を利用でき、原則変更不要 | 編集しない |
| 新`tests/test_managed_quarantine_move.py`、必要な新checker | 新managed root合成fixtureに限定したRED/GREEN、安全case | worker-11所有 |

## 安全state契約（実装前に固定）

1. **明示選択の境界**：copyが既定。`mode='move', confirmed_move=True`は所有marker認証済みmanaged root、近似重複の既存plan+revision+1組1枚生存制約、ユーザーが実体移動と復元の差異をチェックして確定した場合のみUIから発火。直接の不明mode/既定操作で移動は生じない。旧ライブラリにはmodeが届かない。
2. **copyを先に確定**：既存`_verified_item`でimmutable receipt、source bytes/SHA/画像寸法/ownerを照合。`quarantine/incomplete`へcopyを置き検証し、immutable operation manifestに紐付けて公開する。コピー失敗の選択項目は除外にも物理移動にも入れない。既定copyの既存v3動作は完全維持。
3. **indexの確定した移転先だけが元の正本**：明示moveでは`active-generations.json`にv4 `operation.mode='move'` と `state='relocating'` をCAS永続化し、選択成功ページへのdurable exclusionsとmanifestを先に束ねる。元receiptの各ページの`name/sha256/bytes/format/width/height`をimmutable manifest証拠と照合。未選択・非所有・元receiptと異なるページを絶対にこの契約で隠さない。
4. **本体のatomic relocate**：`relocating` indexで安全側へ隠れた選択ページごとに、元フォルダ実体とpublished quarantine copyのSHA/bytes/image、path ownership/marker、no symlink/reparse/hardlink、sourceとdestのsame-deviceを直前に照合し、`os.replace(source,destination)`で元sourceを**検証済み隔離copyの場所へ物理移動**。dest内容は実体rename前後に再照合。元保存フォルダの該当ページbytesは減少し、copy modeの二重保存を避ける。ただし**同じドライブ内のごみ箱に1部が残る以上、ドライブ全体の空き容量が元の状態より増える保証はない**。この意味をUIに明示し、実free bytes増加を主張しない。異なるvolumeは安全に拒否。対象全件完了後、CASで`state='moved'`に更新。
5. **クラッシュ許容**：`relocating`は元source「存在すれば元receiptと一致／欠損すれば移転先copyが厳密に存在して一致」を要求し、**表示は常にexclusion**。この中間stateの再起動はfail-closed検証で成立させ、未確認の自動削除はしない。実装済みの再開導線は**確認付き安全復元**であり、残件の物理移動自動継続は未実装。`moved`は元sourceが**存在しないこと**とdestの厳密一致を要求し、予期しない再出現を拒否。manifest欠損、stale revision、他プロセス改変は新状態と無関係に失敗を維持。
6. **復元**：`moved/relocating`操作をCAS`restoring`にしてexclusionを維持。元sourceがなければcopyから検証済みbytesを専用stageに作り、元root pathのno-reparse、所有/競合、元receipt一致を確かめて原ファイル名へatomic renameで戻す。全source復元後だけCAS`restored`としてmaskを除去。`restoring`は一部sourceあり/なしを厳密照合してrestart可能。復元済みcopyは残しても原excluded indexが消えれば通常一覧で有効に戻る。元receipt/metadataは一度も書き換えない。
7. **世代衝突・二重処理**：per-work lock昇順+CAS revision+manifest SHAを維持。旧セッションのpinや元identityは他pageへ対応付けしない。未完了のmove/restore中は同一workの保存・更新・再生成を拒否し、意図しないindex置換で原証拠を落とさない。処理中の例外時は誤って成功を返さず、pendingを可視化し安全復旧へ。
8. **証拠・禁止対象**：元Flet、readonly legacy、Cookie、実Pixiv、他のowner root、任意URL、リンク/reparse、外部volumeに触れる実装は組み込まない。未確認の物理削除、全target一括削除、receipt内容偽装、skip/弱体化によるGREENは認めない。

## 試験ゲートと判定

新設合成CPU試験は最低、既定copy不変、moveは明示確認のみ、1ページ移転による元source不在＋他ページ/receipt intact、全target実体除去の単純化防止、再起動catalog/visibleページ不変、sourceとcopyの両方SHA検証、強制途中失敗とpending、pending復元、クリーン復元、CAS衝突・owner/reparse/link・非所有拒否、managed再保存・既存consumer回帰を扱う。REDは新挙動未実装で落ちること、GREENは同じassertを満たすことを残す。GUIcheckerは**作成のみ**で起動しない。新CPUコマンドはS専用 `--basetemp` を明示し、外部・原データなし。実装者とは別workerによる独立監査が最終判断条件。

## 実装したファイル・保存済みCPU証拠

primeは`managed_downloads.py`・`duplicate_inspection.py`・実体UI `duplicate_view.py`の限定変更を明示承認。加えて所有範囲の`managed_quarantine.py`・`managed_generations.py`、新`tests/test_managed_quarantine_move.py`、新`artifacts/p3-346-managed-move/gui_checker_prepared.py`のみ変更した。既存`services.py`や作者機能、旧Flet/legacyのファイル・DB/images/ZIPは変更も読取も行っていない。Git/実Cookie/外部通信/GUI起動/Claudeなし。GUIcheckerは**準備のみで未実行**。

| 現製品source／テスト | SHA256（read-only `Get-FileHash`） |
|---|---|
| `managed_generations.py` | `069a48d7e138e22b9392a4d144ca5bcf4de7138d1d1f250242812f5cd7217025` |
| `managed_quarantine.py` | `54cfe2df23527f6e2954ba3dbba1af57277e7478aa4028a0300c0180d02d98c1` |
| `managed_downloads.py` | `855f5fb575eb905f462be2cb99d042aff32c78fe4b69e9bfe6eedd69879dc52c` |
| `duplicate_inspection.py` | `9e52507e798e2c7116fc7200bfa6e28531673971112abf9da1e1a1ba06f3b79a` |
| `ui/duplicate_view.py` | `351d5f4bd1c7165f387f09711aab6c3429e4c60d07f004347f8db29f96b70611` |
| `tests/test_managed_quarantine_move.py` | `8081c771ee916f192238367ae2212170cb7279237cc4c8ff3f53507931b3c991` |
| `gui_checker_prepared.py` | `8df74014bb9addc87b55647ff67e4f67153335179fc1840aac911c67800ba0da` |

専用S合成fixtureのみでCPU試験。初回 `p3-346-move-red-r1` は`--basetemp`親フォルダ未作成のため5 ERRORで無効、再構成した **`p3-346-move-red-r2/result.xml` が1 PASS / 4 FAIL**（move未実装の期待RED）。修正後 `p3-346-move-impact-r3/result.xml` は9 PASS、`r4/result.xml`は127 PASS/1 SKIP、`r5/result.xml`は新UI復元込み26 PASS、**`r6/result.xml`は128 PASS/1 SKIP/0 FAIL**。r6 result.xml SHA256 `cab45a4900db4bfafd35ef13309603f4f600619744d5e34d83e382b66e158e8e`。SKIPは既存`tests/test_managed_downloads.py:252`のsymlink作成権限未保有。`r7/result.xml`はUI容量文言の最終修正後の新規move専用10 PASS。**`r8/result.xml`は無関係な作品の世代更新を許容する追加caseを含む26 PASS**、SHA256 `2035d0d682ac78af99006354a44b177feb897f0f8f320587ad4ddb1be1a54014`。新GUIcheckerは `ast.parse` の構文確認のみ成功、実行なし。いずれも合成managedroot専用、実データ操作なし。これらは実GUI/Edge/native/performance/正式152の完了認定ではない。

新v4 indexは既存v3 copy recordをモード`copy`として引き継ぎ、後続copyも既定で同じ物理動作を維持。状態`relocating`→`moved`と`restoring`→`restored`で元receipt SHA不変、missingを受容するのはexact proof一致の移動ページのみ。元pageが独立に欠損/改変した場合・他者作成リンク・復元先の不一致はfail closed。復元確認はごみ箱のowned operation一覧から再起動後も選択可能。移動中の例外では`relocating`を永続化し、自動削除・成功誤報を避ける。

独立した再読取で`managed_generations.publish`の初回実装が「いずれかの作品が移動中なら全作品の公開を禁止」になっていたことを発見。**移動中の`work_id`に該当する新世代の公開のみ禁止**するよう絞り込み、無関係な`224`の世代公開を合成fixtureで確認した（`test_unrelated_owned_work_generation_can_publish_while_move_is_active`、r8 PASS）。これで同じmanaged root内の他作者保存を不要に止めない。移動対象自身のrevision/証拠照合は維持。

**残る受入**：実装者とは別の独立コード監査、GUIcheckerの承認後Edge受入、Windows native・性能ゲート。コピー隔離と物理移動の選択を実製品GUIで確認できたとは今回のworkerは主張しない。全target一律削除・原データへの拡大・元receipt偽装なし。

