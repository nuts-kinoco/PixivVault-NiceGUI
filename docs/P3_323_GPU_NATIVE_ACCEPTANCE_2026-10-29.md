# P3-323: GPU配布・通常profileの受入回収と継続

## 対象と判定

2026-10-29、圧縮後の正規CoS経路で保存証拠を回収し、未実施だった通常profile再起動を継続した。全移植Goalは未達。本書は固定候補の限定受入であり、後続P3-324のGPU修正へ無条件に継承しない。

- worktree: `S:/Projects/PixivVault-NiceGUI-p3-vault-cleanup-race-20261028`
- branch: `phase11/p3-vault-cleanup-race-20261028`
- HEAD: `2cf4a7a7286dd728e8df8125094498a3622babba`
- 再開時dirty: 493件。後続編集後の件数とは異なる。
- 固定元: `artifacts/p3-321-native-gpu/frozen-01`
- 固定169ファイル集合SHA: `1f7467974c52765ef718ac87492b5963df1411a047f72f9e39684b39a26e9a05`
- 固定manifest SHA: `6f893ad53b602dfabf37e73ba52a4f938e93ee27f6d8af063bfdd5c0a83c146e`
- 別コピーexe: `artifacts/p3-321-native-gpu/standalone-copy-r1/PixivVault-NiceGUI/PixivVault-NiceGUI.exe`
- exe SHA: `b9c9e73520192ee8c67c05e6d5678c1024ff5f9620640739c5a3f0e2fc11adc3`

## 1. 修正後全回帰は既に完了していた

`artifacts/p3-321-final-revalidation/full-source-r2/run.json`を回収。

- 開始: 2026-10-29 21:39:13 JST。
- 終了: 2026-10-29 21:47:40 JST。
- 3877 PASS / 0 FAIL / 0 ERROR / 4 SKIP。3881件、exit0。
- SKIP4件はWindows symlink権限。既設OpenCV 5.0.0でcodec関連9件も実行済み。
- 540ファイルの実行前後SHA一致。再開後にもsource-after.jsonの全540ファイルと現物の一致を照合した。
- 同時点で固定元169ファイルと作業ツリー製品ファイルも一致。
- JUnit SHA: `81897806306781779e778e5858b6bee6e29717501afd3eaca9893eed70928033`。

修正前full-source-r1の6FAILと限定93PASSは過去の証拠として保持する。P3-324製品修正後の全回帰は別に必要。

## 2. 自然起動した配布WebViewでCUDA解析と再起動検索

証拠: `artifacts/p3-321-native-gpu/packaged-natural-cuda-r1/result.json`

SHA: `ba9fce3c6a1ccddfd66a99b52bf64666abcb1d320fe748a5776296adfdc06bb4`

- 初回・再起動とも自然起動targetを使用。`explicit_cdp_navigation=false`。
- 初回は明示CUDA解析→保存台帳style/composition各3ページ→類似結果→2ページ目表示。
- 再起動は保存済み特徴量から検索→2ページ目。provider観測はnullで、新たなGPU推論成功とは記録しない。
- 両roundで確認OK、exit0、全子プロセス終了、app/CDP両port解放。
- 合成旧DB/画像・元ONNX不変。Cookieなし、外部リクエスト記録なし。

旧r5の追加CDP遷移ありの成功を自然起動証拠に置換せず、別の記録として保持した。自然起動checkerはsync Playwrightのイベントを既存page待機で処理し、代替page生成・navigate・reloadを行わない。

## 3. 通常profile: 実model picker保存から引数なし再起動

初回証拠: `artifacts/p3-322-native-profile-gpu/profile-r1/result.json`

SHA: `dd8ec61cab4aad687657c599d763d8b0d69e10f4d768b4de0a16499d0a45f19a`

実OS pickerで元ONNXを選択し、通常設定UIでCUDA必須・自動解析ONを保存。profile revision2を読み戻し、保存だけでは解析台帳作成/推論を始めないことを確認済み。初回のGDIはトレイON既定に対して通常OS restoreを使用し、`restore_state_not_restored`で失敗。終了確認も未観測だったがroot exit0/子0/ports解放。初回FAILを保持し、後からPASSに書き換えていない。

継続証拠: `artifacts/p3-323-profile-gpu-restart/run-r1/result.json`

SHA: `2ec9a2671fe6014119bd43f7b5ce2eb5011a70c89138562ef2e694c53b60de08`

- 成功済みpicker・初期profile作成を再実行せず、同じrevision2を使用。
- 製品引数なしで配布exeを起動。専用profile/temp/WebViewフォルダーを子環境へ指定。
- 保存されたCUDA/autoを実UIで確認。自動解析でstyle/composition各3頁。
- 実行先GPUを表示。類似結果から合成作品の2頁目を表示。
- 通常設定のトレイを明示OFFへ変更・保存・再表示して確認。
- 物理client2240×1260、DPI168、同monitor、origin40,60。
- 既存GDI gateを変更せず最小化/復帰PASS。平均画素差 `0.000027399848828420258`。
- 表示中の所有windowから通常終了確認OK、root exit0、全子終了、app/CDP両port解放。強制終了なし。
- profile revision2、合成旧DB/画像、元ONNX、exeの前後SHA/内容不変。

トレイOFFの成功をトレイON経路の成功とは扱わない。初回の確認未観測の原因も断定せず、今回の表示状態での確認操作を別証拠として成立させた。

## 4. 独立レビューの回収と実用修復

正規agents statusが回復し、既存worker-3/worker-5を再利用した。新しい重複runは作成していない。

- worker-5: GPU profileの画像参照元なし拒否/既存不整合修復/modelなしauto拒否/clear時auto解除/URI provider保持を限定APPROVED。対象製品SHAは上記固定元と一致。
- worker-3: 旧P2「managed少ページ時に旧余剰ページを隠す」は、全世代receipt・作品単位managed優先・隔離tombstone契約を再照合して撤回。修正済み扱いではなく、原契約に整合するための撤回。旧5頁→新2頁で旧3〜5頁を復活させない。
- 別の保存済み独立レビュー `artifacts/p3-322-independent-gpu-review/r1/stdout.json` はCHANGES_REQUESTED。F1個別破損画像で一括解析が停止、F2検索毎の全画像再decode、F3idle scanの不要再読取、F4新しい凍結DBのCPU特徴量再利用をP3-324で修復中。
- F4は同じ凍結DBで画像identityが変わるケースと、別の凍結DBへ切り替えたケースを区別する。失効したGPU行が消えたという理由だけで旧CPU行を再公開しない。
- 全体GPUレビューAPPROVED、正式152完了、実データ/現候補性能、PR/CI/Goalは本書では宣言しない。

## 継続事項

実トレイONの所有アイコン/実menu/GDI/終了の受入、P3-324修正後の限定回帰・独立再監査・全回帰・別freeze/buildを続ける。原Flet/本番DB/画像/ZIP/Cookie不変、実API GETのみ、nana7mi別承認、main直push/force/reset/clean/stash禁止を維持する。
