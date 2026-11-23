# GPU 視覚嗜好学習 作業 8

対象ブランチは `gpu8-20261124`。起点は `fedf4ad5`、実装と試験の候補は `5845dae6d9f5645883e17d4337b316e55a4ced0c`。

## 原受入

| 項目 | 結果 |
|---|---|
| 終了・アカウント切替・補正 OFF | 合成環境で 3 経路とも合格。各経路で直前の露出 2 件が順に永続化され、切替先への混入と閉じた受付からの遅延投入は 0 件。OFF 後の新規表示はシャドー記録を再開 |
| OFF の順位・動作 | おすすめ 6 件とランキング 2 件の順序が視覚学習の接続前と完全一致。既定設定で推論スレッド、予測器の起動、共有 GPU リースの取得は 0。手動開始とアイドル分析の明示設定は既存の動作を維持 |
| 追加 Pixiv GET | 推論ワーカーの成功・例外時はそれぞれ 0。おすすめの適用試験では既存の取得列と一致。GPU 使用中のフォールバック試験では既存の GET 6 件から増加 0 |
| GPU リース | 偽の共有リースで PV 所有者の acquire 後に推論。正常時と推論例外時とも、セッションを閉じてから同じ付与トークンで release。競合中は予測器の起動 0、おすすめは現行順、OFF で待機終了 |
| トークン付き renew | 未達。既存のクライアントは期限前にセッションを閉じて release し、後続ジョブで acquire し直す。renew に必要な共有リース関連コードの変更は保留 |

作業 8 の原受入は一部達成。製品受入の判定、実 GPU・実 API・実機・実プロファイル・D6 の実測、全体 pytest、独立レビューは今回の記録範囲外。

## 変更

- おすすめ露出の追記をサービス所有の既存ワーカーのキューに移した。アプリ終了とアカウント切替で受付を閉じて排出する。補正 OFF では受付を閉じ、推論停止と排出を待ち、新しい受付でシャドー記録を続ける。 [サービス](../../src/pixivvault_nicegui/services.py#L1400) [ワーカー](../../src/pixivvault_nicegui/visual_preference_worker.py#L236)
- purge epoch と受付ごとのトークンを照合する。削除をまたいだ古いキャプチャによる新しい欠落カウンターの上書きを防止した。 [露出の記録](../../src/pixivvault_nicegui/recommendation_media.py#L741) [契約試験](../../tests/test_visual_preference_network_contract.py)
- 描画スコープの変更に合わせて P6 の棚卸しを再登録した。画像参照式の指紋は一致している。取得停止ゲート、共有ロック、共有リースヘルパーに差分は 0。 [通信棚卸し](../../tools/p6_network_review.json#L233)

## 実行証拠

隔離 TEMP/TMP、`python.exe -B -m pytest -p required_network_guard -p no:cacheprovider -k "not dpapi"` を使用。最終候補は関連の 8 ファイル、うち通信棚卸し 1 ファイルとブラウザスモーク 2 ファイル。pytest コマンドの実測累計は約 243 秒。

| 実行 | 主なログ抜粋 |
|---|---|
| 先行試験 | `3 failed in 4.15s`。終了・切替・OFF で露出の実測 0 件、期待 2 件 |
| 初回の関連試験 | `4 failed, 78 passed in 50.87s`。試験側のワーカー変数参照 3 件と棚卸し指紋 1 件を修正 |
| 修正後の関連試験とスモーク | `125 passed in 88.91s` |
| purge 競合の先行試験 | `1 failed, 7 deselected in 3.30s`。新しい露出の `capture_gap` が書かれなかった |
| 最終候補 | `126 passed in 86.38s (0:01:26)`。コマンド 87.71 秒 |

最終実行ファイルは `test_visual_preference_network_contract.py`、`test_visual_preference_wiring.py`、`test_visual_preference_shadow.py`、`test_visual_preference_shadow_review.py`、`test_recommendation_visual.py`、`test_p6_network.py`、`test_recommendation_visual_browser.py`、`test_visual_preference_browser.py`。スモークは既存のローカルループバックと偽の画像応答を使用。

## 逸脱と引き渡し

- 共有リースの `renew` は共有クライアントに未配線と確認したため、変更を保留した。監督が実装候補を回収し、残る `renew` の対応には共有リース変更の範囲確定が必要。 [共有リースクライアント](../../src/pixivvault_nicegui/gpu_arbiter.py#L54)
- 露出キューは保存・並行処理に関係する。今回の判断材料は担当者の試験結果。永続化が継続して失敗する場合は既存の欠落カウンターと警告で扱う。OFF 時は既存の停止・排出タイムアウトの範囲で待つ。
- 起動した試験・ブラウザ経路の残存プロセスは 0。作業専用一時領域 `S:\Work\_tmp\PV\gpu8-20261124` の再帰削除は実行ポリシーが拒否した。残存は 1 ルート、2,744 ファイル、1,060 ディレクトリ、86,979,734 バイト、再解析ポイント 0。監督側の削除対象として確認済み。
- 作業ツリーにはコミットと検証証拠を保管。監督による回収・統合後に片付ける。push は未実施。
