# P2 うごイラ実用接続 — 未受入候補

## 候補と境界

- 専用候補 `S:/Projects/PixivVault-NiceGUI-p2-ugoira-practical-20261026`。
- branch `phase11/p2-ugoira-practical-20261026`、HEAD `50f8ae658a112f4db260ee979af97ee71e5cce93` (PR35固定基線)。commit/push/公開/main統合なし。
- P1を取り込まない非依存候補。共有 services/bootstrap/shell/job表示の writer はこの候補内だけ。固定P1/M3/M4とPR35元checkoutは変更しない。
- 原契約 `PHASE11_MEDIA_PARITY_CONTRACT_2026-10-24.md` M2/M3/M4、原Flet `3ac3aa0d47a64785251593bcb09bb2d5f6df374b:core.py:937–1065` の形式を照合。単体候補の自己検証でありPhase11正式受入・独立コード監査ではない。

## 接続した原操作

1. 作品ID・作者ID・GIF/MP4/フォルダーの選択→metadata確認→明示的なqueue追加。プレビューだけではZIPを取得しない。原型のidentity/作者/作品/illustTypeを固定し、実行前metadataが変われば失敗する。
2. JobService operation_id/attempt/control_revision、pause/resume/stop/retryを再利用。受付intentと元ZIPを専用rootへ永続化し、signed URL/Cookieを永続intentへ入れない。同じ作品・形式のunfinished競合と古い保存proofをCASで拒否する。
3. PR35のZIP安全検証、元フレーム保存、GIF変換・completion/receiptを再利用。MP4は明示設定された既存CPU OpenCVのmp4vで変換し、出力を復号してフレーム数・寸法・FPSを確認する。別codecへの暗黙fallbackなし。奇数寸法/不揃い寸法は勝手なcrop/resizeを避けて明示失敗する。
4. 保存・失敗を既存queue/failed画面に表示。ライブラリはreceipt/bytesの整合が確認できたdoneのみ表示し、形式ごとの結果を再生・一時停止、閉じると元の操作へfocusを戻す。
5. 再起動は保存済みintent/source/output/receiptから照合し、completed結果の表示で再取得しない。壊れた結果を保存済みに見せず、不確定/失敗は既存JobService復旧契約へ戻す。

## 形式、色、時間と保存の意味

- フォルダー: 元画像bytesとmetadata/delayを保持。ZIPパスを直接展開せずPR35検証後に保存。
- GIF: PR35のpalette/alpha処理を維持。最大255可視色+透明index、部分alphaは二値化、delayは10ms単位切捨て。ライブラリは保存GIFを復号したRGBAと保存durationを使う。0msは即時遷移、全コマ0msは静止表示と明記し、新たなdelayを作らない。
- MP4: 原版と同じCPU OpenCV mp4v、RGBA→BGRでalphaを除去。FPS=`max(1,min(60,1000/max(平均delay,10)))`。可変delayは平均化され、clamp時に総尺も変わる。UIで形式制限を示す。元フレーム自体は保存し続ける。
- MP4ライブラリ再生はブラウザーのmp4v codec対応に依存せず、同じ変換で復号したPNG集合を使う。集合hashをMP4 v2 receiptに結合し、同じsafe-ZIP/画像検査を再利用。再生時間は保存MP4のFPS。元RGBを再生して変換後の色と偽ることはない。
- 既存frames/GIF v1 receiptは変更しない。今回のMP4 v2は新規未受入形式で、今回途中のv1試験出力は過去artifactとして保持する。
- 内部世代はPR35方式の不変tokenディレクトリ、`animation.gif`/`animation.mp4`。旧版のtitle(ID)外部書出し命名を再現するexport UIではない。タイトル/IDはintentとライブラリ表示に保持する。この物理配置差と配布時の互換性は正式受入で確認する。
- 既存有限容量/時間/画素予算を再利用し、新しい製品容量方針は決めていない。codec中間物と復号集合も予算に算入。中断物は削除せず保持するため、長期運用の容量回収方針は未確定。

## 自己検証結果 (合成fixtureのみ)

- `artifacts/p2-20261026/focused-r4.log`: **63 passed**。3形式、operation再送、CAS、metadata変更、ZIP失敗retry、stop、破損拒否、再起動ネットワーク0、変換後色・delayを含む。
- `artifacts/p2-20261026/full-r3.log`: **2018 passed / 1 skipped / 0 failed / 6 warnings / 94.50s / exit0**。skipは既存Windows symlink権限条件、権限変更なし。warningsは既存Starlette/httpx、意図した重複ZIP、Pillow非推奨。
- `artifacts/p2-20261026/browser-r3/result.json`: **success=true、Edge 1280×720/1920×1080 × 初回/再起動 4/4**。3形式queue→保存→再生、合成失敗→retry attempt2、pause/resume/stop、focus復帰、再起動で追加取得0、console/page/外部要求0、normal exit/port release各4/4。source_before/after/live一致。headless使用、共有GUIを操作せず、既存8089を使用せず。
- warm画面切替20回ずつのp50=50.16/60.22/51.44/50.80ms、p95=56.86/108.35/94.53/99.56ms。小fixtureの観測でありFlet比較や大容量保証ではない。ZIP/変換backend時間はresult内で別記。
- RGB 96×64・3コマ40/90/170ms: フォルダー/GIF300ms、MP4は10FPS×3=300ms。元赤255→mp4v復号赤252等の圧縮差を確認。7/19/31msはGIF0/10/30、MP4約19/19/19。MP4短delay1/2/3は60FPS、長delay1000/2000/3000は1FPSになる原版の尺差をassert。複雑な実作品の主観画質評価は未実施。
- 初回失敗も保持: full-r1の4FAILはWindows長い一時pointer名を短いstage名へ修正。browser-r1のfocus失敗をdialog外のtrigger参照へ修正。focused-r3のMP4FAILは復号ZIP検証用内部URLのID形式不整合を修正。full-r2/browser-r2は元フレーム再生時点の旧成功証拠であり、最終はr3/r4を使う。

## 再現手順

既存S venv、既存CPU OpenCV Pythonを明示し、PYTHONDONTWRITEBYTECODE=1、PYTHONUTF8=1、PYTHONPATH=<candidate>/src、TEMP/TMP=<candidate>/artifacts専用temp、PV_TEST_CODEC_PYTHON=<既存CPU OpenCV Python絶対パス>を設定する。新しい空のrun出力名を使用する。導入はしない。

```powershell
& <S-venv-python> -B -m pytest -q -ra --basetemp=artifacts/<new-run>-temp -o cache_dir=artifacts/<new-run>-cache
& <S-venv-python> -B tools/check_ugoira_practical_browser.py --channel msedge --output artifacts/<new-browser-run>
```

fixtureアプリは `--ugoira-fixture-root <新規S専用root> --ugoira-codec-python <既存CPU runtime>`。実用経路の `--ugoira-root` と明示Cookieの配線はコード化・MockTransport試験済みだが、実Cookie/実GETを実行していない。Cookie値や実画像をこの検証へ入れない。

## 影響と残る受入

- JobService本体、小説storage/export、静止画storage自体は変更なし。共有services/shell/bootstrap/reader/job表示の既存fixture回帰は全件成功。固定P1との統合や全媒体同時運用は別候補での設計・差分照合が必要で、ここでは合格としない。
- `frozen-preservation.json`: P1 28/28、M4 18/18、M3 7/7が既存manifestと一致。監査ZIP/Base64等を抽出・変更していない。
- 独立コード監査、Windows配布native/codec同梱・別PC起動、必要最小live GET、exact-head Windows/Ubuntu/Chromium CI、同条件Flet比較と総合UXは全て未実施。
- P1正式受入は保留のまま。P2自己検証成功を製品移行完了にしない。P3への判断はdotへ返す。
- 最終hash・全差分・書込み終了時刻は `artifacts/p2-20261026/final/manifest.json` / `candidate.patch` / `write-end.json`。
