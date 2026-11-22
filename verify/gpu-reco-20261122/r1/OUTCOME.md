# GPU 作業 6 おすすめへの適用

対象は `gpu-reco`。基点は `254b70ef9bd5cc3893db47514b22ff9fa6d61529`。コードと以下の証拠を同じローカルコミットに収録した。

## 原受入

| 条件 | 確認結果 |
| --- | --- |
| 既定 OFF とシャドー露出 | 合成サービスで現行順と一致。表示した候補、節、グループ、元順位、提示順位、画像可用性と変種を `ranking_events` に追記 |
| ON の確実枠 | 絞り込み済みの同一表示グループ内だけで補正。表示時点でサムネイルを全件保持でき、元の lazy スロットの配置を維持できる場合に適用 |
| 通信同値 | 偽 transport の宛先列と件数が 5 条件で一致。冷、温、欠損、確保前の追い出し、確保後の追い出し。それぞれ追加 GET 0 |
| 取得対象と既存挙動 | 元スロットの媒体 URL、取得順の入力、初期 6 件の集合、7 件目の未取得、SuperLike 順と既存おすすめ回帰を確認 |
| 保護と失効 | 最初の ON で操作中カードを固定。OFF に戻した次の描画では選択中のビューアと位置を保持。Undo、OFF、GPU 失敗、モデル不一致、アカウント切替、公開直前のカタログ差し替えとアクセス失効で古い結果を拒否 |

製品受入は保留。作業 7 の起動・設定配線をつないだ候補とブラウザーの lazy 読み込みは未確認。実 GPU と実 API は今回の試験範囲外。

## 変更

- `recommendation_media.py` は LRU の順序を変えずに既取得バイト列を捕捉する。補正は独立ストアの公開済みモデルとハッシュが一致する作品信号 A を優先し、作品信号の未取得時は作成済みの作者信号 D を使う。元の確実枠は数値スコアを持たないため、補正重みの 4 分の 1 の範囲に元順位を割り当てた。作業 5 の入口をつなぐ境界は `recommendation_visual_score`。
- `ui/recommend_view.py` は元の lazy 画像 URL を元の位置に残して、移動するカードに保持済み PNG をインライン描画する。ボタン状態による高さ変動を排除するため、Pixiv 操作が有効な場合や移動元と移動先の操作列が同じ形にならない場合はシャドー記録を続ける。
- `tools/p6_network_review.json` は元の画像呼び出しの AST 指紋を更新した。呼び出しに渡す URL は元順位のスロットが作る内部メディア URL で、補正画像の追加取得経路はない。
- 設定とライフサイクルの配線は作業 7 の所有。受け口は `services.recommendation_visual = RecommendationVisual(worker, enabled=...)`。学習の既存 `shadow_observer` が接続済みなら、設定配線前もおすすめの露出を記録できる。

## 実行証拠

専用 venv の Python で `-B -m pytest -p required_network_guard -p no:cacheprovider -k "not dpapi"` を実行。TEMP、TMP、各 basetemp は S:。D6 の負荷制限中は下表を単一ファイルずつ実行した。

| 原ログ | 実測 |
| --- | --- |
| [red.log](red.log) | 先行試験 5 failed。補正・露出の入口未実装 |
| [owned-pin-red.log](owned-pin-red.log) | 1 failed。棚ピン未取得時にビューア専用ピンから画像を記録していた |
| [green.log](green.log) | 専用 19 passed |
| [p6-network.log](p6-network.log) | 通信棚卸し 7 passed |
| [reco-certain.log](reco-certain.log) | 確実枠 5 passed |
| [reco-context.log](reco-context.log) | おすすめの操作回帰 9 passed |
| [reco-races.log](reco-races.log) | 取得・ビューア競合 10 passed |
| [remote-variants.log](remote-variants.log) | リモート画像変種 9 passed |
| [cleanup.txt](cleanup.txt) | 担当が起動した pytest 残存 0。S: の再生成可能な一時領域は削除ドライランで確認済み |

## 逸脱と次工程

- 独立 Sol が複合カタログの LRU 参照、初回 ON の位置保護、公開直前の世代と画像ソースの失効、旧リーダーと専用ピンの遅延結果、操作列の高さ変動を指摘した。修正後の回帰を実行し、Sol がデータ保全・並行処理に関わるカタログとリーダーの境界を静的に再確認した。
- 最初の P6 実行で画像呼び出しの旧指紋が残っていると判明し、現行指紋と理由をレビュー表へ反映した。作品画像の来歴を検証する試験では、偽クライアントが区別できる合成サムネイル URL に修正した。
- `D6_QUIET.flag` が終了時も存在したため、全体 pytest と Chromium は未実行。作業 7 と接続後、制限解除時に統合候補で全体試験とブラウザーの lazy 取得同値を確認する。Pixiv 操作有効時の補正については、行高と取得同値を確保できるまでシャドー記録を維持する。
