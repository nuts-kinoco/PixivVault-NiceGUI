# P3-324: GPU解析の実用修復と配布候補の受入

この記録は候補 `2da83a…` の限定受入である。正式152操作、旧Fletとの同条件性能、実データGET、残るWindows操作、PR/CI/統合を完了したという意味ではない。原DB・画像・ZIP・Cookie・ONNXは変更していない。

## 利用上の修復

- 旧保存画像の欠損・破損・曖昧な参照を1件検出しても、後続の正常な保存ページの一括解析を継続する。モデル/実行バックエンドの障害と未知の連続障害は停止する。一時停止・終了後の古い世代の結果は公開しない。
- 類似検索と完了済みページの待機走査では、保存画像の全本文を毎回読み直さない。実推論の入力では画像SHA・receipt世代・可視状態を引き続き確認する。
- 旧DB AのGPU解析後に旧DB BのCPUベクトルを採用した場合、BのDB SHAと画像世代を別の所有台帳に保存する。同じBの画像が変われば古いCPU値を抑止し、新しいDB Cは別の採用として扱う。元GPU行の証明を付け替えず、旧DBへ書き込まない。
- 実行先と希望実行先を分け、CPUへの切替理由は固定の安全な文言で表示する。再走査をまたぐ件数は「ワーカー累計」と表示する。

主な製品変更は `similarity_worker.py`、`managed_downloads.py`、`managed_similarity.py`、`legacy_similarity_inputs.py`、`services.py`、`ui/similar_view.py`。

## 固定候補と配布

| 項目 | 証拠 |
|---|---|
| 固定入力 | `artifacts/p3-324-gpu-review-fixes/frozen-01/source-manifest.json`、169ファイル |
| 固定集合SHA256 | `2da83a944f6bd19c7a0fcd6b5416bae78d002de2c0603dc6c71916e7e82e62b0` |
| manifest SHA256 | `3ec18a952ddde8f3a8b53d034af02abcc52b7b2a7423f6c51410b0fc63700c9c` |
| ビルド | `built-01`、PyInstaller onedir/noconsole、CUDA依存同梱、exit 0 |
| 独立コピー | `standalone-copy-r1/PixivVault-NiceGUI/`、2,523ファイル、2,251,958,105 bytes |
| コピー照合 | `standalone-copy-r1/result.json` と `sha-manifest.json`。生成元前後と全コピーのSHA一致 |
| 配布木SHA256 | `579c6d6be97765909725254cd6ee717495a3b48f0e7abbdba333a47d3b33756b` |
| exe SHA256 | `29b57a905be586e7ea1bcd31f6ad5d1a4479c9539f481783ebafe85129abf060` |

ONNXは原ファイルを明示的に読み取り参照する。配布へのモデルコピー、GPUドライバー更新、本番アプリ置換はしていない。固定後のchecker変更はこの製品候補の変更に数えない。

## 全回帰

`artifacts/p3-324-gpu-review-fixes/full-r2/run.json` は exit 0、519.41秒。3,912 tests のうち **3,908 PASS、0 FAIL、0 ERROR、4 SKIP**。codecは既存CPU interpreterのOpenCV 5.0.0を明示した。

- `src/tests/tools` とpyprojectの541ファイルは実行前後で不変。
- JUnit SHA256: `ccb1b3aa83afc2882ba65567eef0c6f234dda7b13fe7ca8b39e16188f1663081`。
- 4 SKIPはWindows symlink権限に関する既存条件。codec未設定によるSKIPはない。OS権限設定は変えていない。
- 先行 `full-r1` の3,901 PASS / 7 FAIL / 4 SKIPは保持する。6件は `catalog_snapshot(verify_pages=...)` を受ける試験double/wrapperの更新漏れ、1件は累積件数の旧表示期待だった。
- 試験4ファイルを新契約へ合わせ、世代・隔離・ZIP未公開等のassertは保持した。限定26 PASS後に上記全回帰を実施した。

この全回帰後に追加した配布modeやUI操作checkerの変更は、別の構文確認と実受入に結合する。全回帰時の541ファイルが現在もすべて同一だとは主張しない。

## 新exe自身のCUDA解析・検索・再起動

証拠: `artifacts/p3-324-gpu-review-fixes/packaged-natural-cuda-r1/result.json`

SHA256: `de958b4d333298c80c89aa424d9ab0870c0109eacfdb4a9b14f902585b93cd31`

生成した合成画像3ページと凍結した合成旧DBを使用。Cookieなし、外部requestなし。実際に自然起動したWebView2 targetを使用し、CDPによる追加navigationはしていない。

| 回 | 実操作と観測 | 終了 |
|---|---|---|
| 初回 | 作者画像右クリック→類似検索→1ページ明示解析→全ページ解析。実行先CUDA、style/composition各3件。別作者の類似結果から元の2ページ目を開き、counter 2/2と画像decodeを確認 | 確認dialog、exit 0、観測子プロセス0、app/CDPの両port解放 |
| 再起動 | 同じ解析台帳から類似検索→2ページ目を開く。providerはnullで、新しい推論の証拠には数えない | 確認dialog、exit 0、観測子プロセス0、両port解放 |

元合成DB・PNGと原ONNXの前後fingerprintは不変。両回のページエラーと外部requestは0。これは配布exeのCUDA解析の証拠であり、GDI最小化復帰やトレイの受入は別に扱う。

## GPUが見えない条件の準備と失敗記録

`packaged-cuda-unavailable-r1` は失敗記録として保持する。子環境の `CUDA_VISIBLE_DEVICES=''` でGPUを隠す想定だったが、この既存Windows runtimeでは実際に1ページ解析が成功した。

別の新しい子プロセスで `cudaGetDeviceCount` を読取診断した結果は `cuda-visibility-probe-r1.json` に保存した。空文字はdevice count 1、`-1` はreturn 100（no device）だった。したがってr1は障害注入の前提が成立しておらず、明示CUDAのCPU fallback不具合の証拠にはしない。r1も正常終了、子0、両port解放、入力不変を確認した。

checkerは子環境値を `-1` に変更して続行する。OS環境・ドライバーは変更しない。期待する失敗経路のPASSと、CUDA推論成功は別集計とする。

## 独立確認の範囲

- Worker 5の不良ページ継続・安全なfallback表示・累積labelは、別Worker 3が限定承認した。関連76 PASSの証拠を保持する。
- F4のA→B採用→同B画像変更の再現はRED1件を保存し、修復後18 PASS。別reviewerが最新コードのCPU採用証明の分離、GPU証明不変、検索だけの採用経路を確認した。
- 同reviewerは4試験の契約修正を限定承認。CoS transportが停止していたため、rootが取得した最新コード・SHA・証拠を渡して確認したものであり、reviewerがWindowsファイルを独自再取得・実行したという記録にはしない。
- 配布mode checkerの追加は別の限定独立確認済み。実機結果は各新namespaceで回収する。

## 継続中の必須受入

トレイの実UI、URI冷温起動とOS連携、正式152操作の全条件対応、承認済み実データGETと不変確認、旧Fletとの実画像面積/DPIを揃えた現候補性能、残る独立確認、PR/CI/統合が必要。旧候補1fのGDI復帰成功や過去のUI速度を新候補へ自動継承しない。Goalは未達。
