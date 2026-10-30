# P3-340 GPU・源泉保護 独立静的監査（2026-10-30 JST）

## 判定と範囲

**Scope verdict: REQUEST_CHANGES（P2 1件、性能／可用性）**。対象は現製品の
`managed_similarity.py`、`legacy_similarity_inputs.py`、
`frozen_source_pin.py`、`similar_saved.py`、
`similarity_worker.py`、`egara_inference.py`、
`similarity_configuration.py`、`services.py` の類似検索接続、
`ui/similar_view.py` のみ。P0/P1を裏付ける現物根拠は見つからなかった。
静的なコード経路の判定であり、新規pytest、GPU、GUI、Flet原DB/媒体/Cookie/ONNX内容読取は行っていない。
製品・テスト・Gitは変更していない。本書だけを新規作成した。

固定根拠は `artifacts/p3-340-native/frozen-01/source-manifest.json`（SHA256
`d045e51078b06f12eb6341b4ca75acaa64cb3e3b61ae4345733f055b40689987`）。
同manifest記載のsource treeは **175 files**、
`f42fafe1f2f2e95a161f853225c3f54790cc54800bbc913d2fb19d851f26f796`。
監査対象9ファイルの現行SHA256は、manifest内のそれぞれの行と全件一致した。
全175件のツリーを今回独自再計算したという意味ではない。

## 指摘 P2：媒体再認証のindex再走査に時間制限が適用されない

- **位置**：`src/pixivvault_nicegui/managed_similarity.py:37-39`
  （`MEDIA_REFRESH_SECONDS = 30.0` と「private indexの再走査をrate-limitする」契約）、
  同 `:578-609`、特に `:590-595`、
  `src/pixivvault_nicegui/legacy_similarity_inputs.py:84-88`。
- **現行の実行経路**：旧GPU台帳に対応する新凍結DBのCPU pairについて、
  媒体不在・壊れたZIP・一時的読取不能があると
  `prepare_legacy_cpu_proofs()` は `retry` 集合を返し、
  `_legacy_cpu_retry` に残る。以後の明示 `search()` は
  `_prepare_legacy_cpu()` を呼び、retry非空なら毎回
  `legacy.refresh_media_index()` → `catalog.replace_snapshot()` へ入る。
  `MEDIA_REFRESH_SECONDS` の利用は定義行以外に見つからず、
  再走査の最短間隔を制限する実装がない。
- **影響**：不達媒体が1件残る状態で同一セッションの利用者が検索を繰り返すと、
  全private index再構築が毎回発生し得る。大規模媒体では検索遅延・不要I/Oが
  増える。これは検索失敗や媒体破壊の実測ではなく、コードで確定する頻度制御の欠落。
- **合成再現案（未実行）**：
  `tests/test_similarity_preparation_lifecycle.py:34-54` の
  `_legacy_cpu_retry` が残る既存合成fixtureを利用。
  `legacy.refresh_media_index` をspyし、同じ現存する別作品への
  `search('102',1)` を短時間で3回行い再走査呼出回数を測る。
  現状は3回、30秒window中の期待は初回1回。
  改修時は「失効した個別媒体のtokenは毎検索で再検証」と
  「全private index再構築のみ30秒抑制」を分離し、
  既存の回復再試行と同世代adoption tokenを保持すること。

## 確認した保護・互換経路

1. **旧SQLiteの読み取り保護**：
   `frozen_source_pin.py:81-101` でWindowsの
   `CreateFileW(GENERIC_READ, FILE_SHARE_READ, OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT)`、
   `:104-123` でリンク・hardlink・sidecar拒否、
   `:210-269` で開閉前後のidentity照合と初回SHA、close/cancel時の失効処理。
   非Windowsではcheck毎のfull SHA。公開された制限は同ファイル冒頭の
   既存memory mapping／raw volume等であり、万能な外部改変阻止とは評価しない。
   `legacy_similarity_inputs.py:90-119` が有限一括走査をpinで包む。
   `similar_saved.py:98-110,122-155` は凍結DBのfull-SHA、
   read-only immutable SQLiteとquery-onlyを使う。
2. **既解析CPU pairと世代変更**：
   `managed_similarity.py:307-431` は新凍結DBの完全なstyle/composition
   pairを二つとも1024次元で検証後、媒体token付きでowned ledgerへ採用。
   不完全なpairは採用せず、不達媒体はretryを残す。
   `:275-301,433-454,455-497,671-683` は新世代の証明・現媒体tokenを
   照合して旧GPU所有行を抑制する。検索時の新旧ベクトルの合成と
   index書込みの準備段階を分離している。
3. **managed同一作品ID**：
   `managed_similarity.py:627-683` は現在のmanaged receiptが存在するwork IDを
   `replaced` へ収め、旧DBの同work ID・全ページを結合対象から除外する。
   `:499-537` はreceipt digest・保存画像SHA・可視性・世代・取消を確認してから
   owned indexへcommitする。
4. **取消・終了・UI世代**：
   `similarity_worker.py:91-129,151-220` はpause/stop時にgenerationを進め、
   `stop()` のjoin timeoutを成功に変換しない。
   `services.py:618-648,650-695,2196-2276` はsource処理とinferenceを
   serializeし、close前にstopを公開してworker drainを求める。
   `ui/similar_view.py:87-115,216-245,244-346` はUI世代・一回限りtoken・
   view close/invalidateを照合し、古い結果とクリックを拒否する。
5. **CPU/CUDAの実行真偽**：
   `egara_inference.py:122-156` のweight SHA、`:158-215` の
   CUDA優先・明示CUDA時のCPUへの暗黙fallback拒否、
   `:217-250` のauto実行失敗後CPU再実行とprovider_active管理を確認。
   `services.py:697-704` と `ui/similar_view.py:169-201` は
   希望providerと実際のproviderを別表示し、fallback理由を固定文字列に制限。
   `similarity_configuration.py:7-30` では、保存画像源なしindex、
   modelなし自動解析を拒否する。旧worker4の両指摘は現行に再掲しない。

## 誤検出として扱わない点

`legacy_similarity_inputs.py:168-182` は凍結DBに完成CPU pairがあり、
まだowned行がない場合に実画像再読取なしで既解析vectorを再利用する。
これは `similar_saved.py:122-151` の**凍結SQLite単独で埋込みベクトルを検索**
する既存方式と整合し、`ui/similar_view.py:277-282` もURLが無い画像の
サムネイルを省略して結果を残す設計。旧CPU pairの画像が後に無くなっても
vector検索が可能な振る舞いを、媒体失効の確証P2とは判定しない。
旧Fletのsource実物と最終的なUI契約との直接照合は今回未実施であり、
互換仕様の確認事項として残す。旧GPU台帳の失効したowned行が復活する
こととは別問題である。

## 既存動的証拠との関係

- `artifacts/p3-319-gpu-runtime/numeric-parity-r1/result.json`：
  合成1件のCPU/CUDAの1024次元整合。style cosine
  **0.9999995231628418**、composition cosine **0.9999995827674866**、
  両方PASS。既定の元ONNX以外を採用する根拠ではない。
- `artifacts/p3-339-native/packaged-natural-cuda-r1/result.json`：
  packaged実行2roundの `passed=true`、初回
  `provider_observed_this_round=cuda`、再起動roundは
  `provider_observed_this_round=null`（モデル未ロード）。
  いずれも通常exit0・子0・port解放・源泉不変と記録。
  `get_providers()` だけではCUDA graph node実行の網羅を証明しないため、
  GPU実ノード・継続負荷・最新340配布版の完成判定には使わない。
- 以上の旧動的PASSは本静的P2の「retry媒体が残ったまま短時間検索を繰返す」
  条件を測った証拠ではない。今回テストは起動していない。

## 監査した現物SHA256（現worktree＝frozen340 manifest）

| ファイル（`src/pixivvault_nicegui/` 下） | SHA256 |
|---|---|
| `managed_similarity.py` | `083d1613f383f6bce6752ab70db5fab423196d0dcec04b96122516dfe367099a` |
| `legacy_similarity_inputs.py` | `4f03f4a0b5e1c8ba4a5873f7e97b508bd8241e58a4f39b53ccca75632771882b` |
| `frozen_source_pin.py` | `7eed99dd82c33a5dd b02cd8c3b9c78c35c1c666b27192f7f3b53b4fcca3fd4e` |
| `similar_saved.py` | `cdc1c8e40df214d61e9e57db1bddca08abe9e904e2754cc463569f8da4503b7` |
| `similarity_worker.py` | `9fdef5b6d268482efe61954d8434e553ddb9bc48d7097c25137d0dbb2e5c6484` |
| `egara_inference.py` | `0d970aafcf9918f429a7e808004bb49264b99aa4ad2ff43d5b9ae20d5c4a5485` |
| `similarity_configuration.py` | `f0b0e0f3267c8f3daf09ad2dad0c4f1e76329f17efcb18fa2159cafbeac6d312` |
| `services.py` | `93b5bb7708547fc129e49903e91a4af256224aacf69c2138b1cd5ab2384753c0` |
| `ui/similar_view.py` | `ab2de5fbb1906a375ad3d857ff7e56bd3be90c97bb6960bb3d6956c5e036f7bf` |

## 次の判断

P2の解消時はsource固定解除後に担当者が30秒間隔を実装し、
合成失効媒体と回復媒体を繰返し検索する**最小試験のみ**を追加する。
既存CPU pairのDB-only検索や毎検索のtoken照合・GPU infer数値互換を
弱めて帳尻を合わせない。変更後SHAと独立検証が得られるまでは
現340の本監査範囲に `APPROVE` を付けない。

## 2026-10-30 追補：P3-341 媒体再走査 P2 修復の独立再レビュー

**限定再レビューのscope verdict: APPROVE（既報P2を修復済みと判定）**。
上の `REQUEST_CHANGES` は**修復前のfrozen340**に対する歴史的判定として保持する。
この追補は `managed_similarity.py` の現ソースと、新規合成2テスト・既存RED/GREEN原票を独立して読んだ結果であり、
341のfreeze、製品全体Goal、packaged GPUの最終受入を承認するものではない。
今回、新しいpytest・GPU・GUI・Claudeは実行せず、製品・既存テスト・Gitも操作していない。

### 固定基準と現行差分

- frozen340 tree（manifestの記録）：`f42fafe1f2f2e95a161f853225c3f54790cc54800bbc913d2fb19d851f26f796`、
  manifest SHA256：`d045e51078b06f12eb6341b4ca75acaa64cb3e3b61ae4345733f055b40689987`。
- 修復前 `artifacts/p3-340-native/frozen-01/src/pixivvault_nicegui/managed_similarity.py`：
  SHA256 `083d1613f383f6bce6752ab70db5fab423196d0dcec04b96122516dfe367099a`（720行）。
- 修復後 `src/pixivvault_nicegui/managed_similarity.py`：
  SHA256 `764adf7e90faf900b8fc21bece900231a7d94fc2ed0f65c715ab6cfed1d9fb65`（728行）。
  frozen340との読み取り行比較では `_legacy_media_refresh_after = None` の初期化が1行、
  retry時の `monotonic()` 期限確認とrefresh前予約への限定置換が1箇所のみ。
- `tests/test_similarity_media_retry_throttle.py`：
  SHA256 `ad464825ca6511c51ea16fbb40997c9afd0cbf366d7696940c76c90ed04e3f0d`、2ケース。
  監査対象の残り8製品ファイルの現行SHAは、旧frozen340 manifestと **8/8一致**。
  まだ341の新たなsource tree SHA/freezeは存在を確認していない。

### RED→GREEN保存結果と静的判断

| 原票 | 観測 | SHA256（`result.xml`） |
|---|---|---|
| `artifacts/p3-341-similarity-refresh-red-r1` | 2件/2 FAIL、exit1。旧実装で時刻100.0の3検索が全索引scan 3回、失敗したscanを次クリックで再試行 | `3c62043a20b6998a444d430c94dd2a3e1258bea7d8dd0bd452333791c688b0dd` |
| `artifacts/p3-341-similarity-refresh-green-r1` | 新2件を含む36 PASS / 0 FAIL / 0 ERROR / 0 SKIP、exit0 | `d71bf75cf4fa32c37b7e4755ca6e824d07022afae28fa9e135c827f36f3edff4` |

`managed_similarity.py:548-564` の新しい `_legacy_media_refresh_after` はインスタンス固有。
同ファイル `:579-617` は `_prepare_lock` を非ブロック取得し、
retryが残る場合に `now = monotonic()` と30秒deadlineを比較する。
期限到達時には **`refresh_media_index()` を呼ぶ前に** `now + MEDIA_REFRESH_SECONDS` を予約する（`:591-602`）。
refreshが例外を投げても予約が残り、内側の `finally` でロックを解放するため、
次のクリックで全索引を直ちに再走査し続ける経路はなくなった。

一方、`prepare_legacy_cpu_proofs(...tokens=tokens...)` は期限待ちに入らず毎明示検索で実施される
（`:603-615`）。`legacy_similarity_inputs.py:18-49,81-88` の検索ごとに作るtoken cacheを使い、
旧CPU pairの各媒体identityを再点検するため、既知ページの復旧は30秒の全索引更新を待たない。
新試験 `:9-48` は3検索でrefresh1回、30秒後のrefresh2回目、媒体回復直後の検索成功、
`_egara_inference is None` をassert。
`tests/test_similarity_media_retry_throttle.py:51-75` はrefresh失敗後も次検索が通り、
refresh呼出1回、`_legacy_cpu_failure`が偽のままという境界を検証している。

`managed_similarity.py:566-577,579-617,713-728` のactive操作と終了通知、
旧 `legacy_similarity_inputs.py` の読み取り検証・proof採用は維持されており、
今回の差分に新しい源泉書込・画像GET・モデルload経路を確認しなかった。
GREENの既存34件には旧CPU媒体の一時失効・回復、モデル無load、SQLite SHA保護、
管理rootの原子性・世代・取消が含まれるが、これらは既存合成テストの範囲での確認である。

**再監査結論**：先のP2は現行SHA `764adf7e...` に対して解消と判断し、
**限定APPROVE** とする。旧REDの事実と旧REQUEST_CHANGESは保持する。
今後の341 freeze/source固定、GPU実ノード、実媒体の性能、製品全体受入はprimeの別ゲートで判断する。
Q03/Q12確認modalのスクリーンショット時点の問題は本GPU評価の対象外。
