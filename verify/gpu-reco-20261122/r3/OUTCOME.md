# GPU 作業6　第3回レビュー対応

`gpu-reco` に、起動時 Busy 修正と GPU 作業7を含む `pv-dev-baseline` の `854f331` を取り込んだ。マージは `ca4e517f79f3dd613431a81a67cfacdc97111775`。Neon の再レビュー正本は `S:/Projects/PV-DEV-20261117/tmp/rx_gpu6r2.md`。

## 原受入

- キャンセル、Escape、外側クリック、プログラムによる閉鎖を、理由ダイアログの値変更で処理した。確認ボタンは選択を取り出してから閉鎖する。通常の OFF 再読込で閉鎖済みカードが固定されないことを照合した。 [保護経路の棚卸し](../r2/SWEEP.md)
- 観測ハッシュを削除 epoch に結び付けた。同一世代の観測だけを再利用し、通常メディアキャッシュの残存や旧世代の準備完了から観測履歴が復活する経路を遮断した。リーダーの観測索引は弱参照を使う。epoch 変化を確認した時点で旧索引を消去する。
- 作業7の既存設定ゲートをおすすめのバインディングに接続した。統合時はシャドー観測器が既に接続される一方、補正側の設定が届いていないことを先行試験で再現した。統合候補の ON→OFF は現行順に完全一致し、偽 transport で追加 GET 0。
- Chromium で長い作品名を含む合成6件の棚を開き、通常の lazy 画像取得、温キャッシュでの ON→OFF、保持画像背景と元画像の opacity、ドラッグ抑制、Escape・外側クリック・取消の実 DOM を検証した。Chromium での通信結果はこの表示条件に対応する。冷、温、欠損、追い出し前後と表示拡張は専用 pytest で偽 transport 比較を行った。
- 取得経路は従来の表示スロットの内部メディア URL。通信棚卸しの AST 指紋と行番号を更新した。作業5の公開入口、作業7の所有ファイル、通常の取得順と外部経路への変更はない。

## 原証拠

| 証拠 | 結果 |
| --- | --- |
| [red.log](red.log) | 5 failed、1 passed。閉鎖後の元順、設定未接続、同一／再作成メディアでの purge 後の来歴残存を再現。理由確定は先行時点から合格 |
| [green.log](green.log) | 専用 44 passed。追加の遅延準備ケースは後続の全体実行対象 |
| [browser.log](browser.log) | 隔離 Chromium 1 passed。初回の Harness 起動障害は NiceGUI の pytest 用ポート指定と初期インスタンス参照を修正して解消 |
| [viewer-red.log](viewer-red.log) | 1 failed。補正準備のない再読込で、既存ビューアの描画窓口に新しい引数を付けていた回帰 |
| [final-isolated.log](final-isolated.log) | 最終差分で 58 passed。専用、別プロセスの Chromium、ビューア回帰、P6 通信棚卸し |
| [baseline-first-failure.log](baseline-first-failure.log) | 作業6を含まない `854f331` の一時コピーで学習タブの失敗を再現。既存の `FakeServices` に、統合された `apply_preferences` が呼ぶ `sync_visual_preference` がない |
| [baseline-browser.log](baseline-browser.log) | 同じマージ元の設定ブラウザー試験で未初期化の `Server.instance` 参照を再現 |
| [full-pytest.log](full-pytest.log) | 4,476 passed、5 failed、3 skipped、3 deselected。34分31秒。失敗内訳は下記 |
| [bulk-isolated.log](bulk-isolated.log) | 一括選択20,000件の単独再実行で 1 passed。0.566秒、400チップ |
| [cleanup.log](cleanup.log) | この担当の試験一時物56項目、57,377ファイル、365,306,604バイトを削除。起動したプロセスの残存0 |

独立 Sol が閉鎖時の保護解除、旧 purge epoch と遅延準備の境界、作業7の設定バインディングを静的確認した。実 GPU・実 API と実機プロファイルは使用していない。

全体実行は専用 venv の `-B -m pytest -p required_network_guard -p no:cacheprovider -k "not dpapi"` で行い、TEMP/TMP と basetemp は S: に置いた。設定ブラウザーのループバック試験用ポートは OS 割当。全体実行の5失敗は以下。

- 学習タブと設定ブラウザーの既存試験2件は、作業6を含まないマージ元で再現した。
- ビューアの再読込回帰は、補正準備がない場合に従来の引数だけで描画するよう修正した。おすすめの Chromium 試験は、他試験でグローバルに生成された NiceGUI の要素が原因でサーバーが起動しなかったため、ループバック専用の別プロセスへ分離した。両方とも最終差分で再実行して合格した。
- 一括選択20,000件は、全体実行時に1.065秒で1秒上限を超えた。単独再実行では0.566秒。

全体実行には、上記のビューア互換修正と Chromium 試験の別プロセス化より前に読み込まれたコードを含む。最終差分の合格結果は、影響範囲を再実行した `final-isolated.log` に記録した。

## 残課題

製品受入は別判定。マージ元の既存試験2件の対応と、その後の最終候補での全体試験が残る。Neon の推奨項目は、おすすめ露出キューのサービス終了時ドレインと `capture_gap` 連携、描画直前に保存画像を失効させた場合のプレースホルダー。観測 epoch と設定ゲートには公開時の同期メタデータ参照があり、本番環境の遅延は未測定。

担当一時領域 `S:/Work/_tmp/PV/gpu-reco` の CV 補助ファイル2項目は追加削除コマンドが端末の実行ポリシーで起動前に拒否された。残ったのは空の `Importer_0_4` と492バイトの `cv_debug.log`。削除後の読戻しで、担当の Python・Chromium・ドライバの残存は0件。証拠と監督所有の作業ツリーは回収用に保持した。
