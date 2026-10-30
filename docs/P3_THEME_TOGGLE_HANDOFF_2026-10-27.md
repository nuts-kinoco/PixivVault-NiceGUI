# P3 S21 ダーク／ライト切替 引継ぎ（未実行）

固定220 `S:/Projects/PixivVault-NiceGUI-p3-card-style-fixture-20261027` SHA `a7593546e7c9989604dd52b286012bd901ce446723a09d261b16a2205ad8aba0` を基点に分離。元 `3ac3aa0d47a64785251593bcb09bb2d5f6df374b:docs/UI_GUIDE.md` ヘッダーの明暗切替、`gui.py:464–466,2749–2779` の起動時ダーク・ヘッダーアイコン・明暗遷移を照合。原コード上はテーマの保存処理がなく、ページ再作成/再起動時はダーク。今回もテーマをDBに保存しない。テーマ切替でカードサイズ `grid_card_size_mode` を変更しない。

ヘッダーに切替ボタンを追加しNiceGUIのclient別dark modeへ接続、アイコン・aria-label・tooltipを現在の状態から更新。CSSの固定暗色を共通変数にし body.body--light で明色へ切替。固定dark propsを指定していた入力欄はテーマを継承し、うごイラ再生背景も共通surfaceに接続。既存ページ、サイズ設定、ジョブ/永続保存サービスの処理は変更しない。設定ファイルに新キーは追加していない。配色・全画面視認性、入力欄、ダイアログの実画面結果は未確認。

CoS: 新候補の全manifest/固定220不変・所有processを照合。S隔離fixtureで `python -B -m pytest tests/test_p3_theme_toggle.py tests/test_p3_card_size_shared.py tests/test_preferences.py tests/test_p3_ui_state.py -q` を実行。合成画面を1280×720/1920×1080で起動し、ヘッダーでダーク→ライト→ダーク、表示・アイコン・tooltipを確認。ランキング/ライブラリ/おすすめ/学習/保存/キュー/設定/小説・うごイラ閲覧の文字・入力欄・選択・dialogが明暗に追従し、スクロールや操作が継続できることを確認。ライトのままタブを再訪して保持し、アプリ再起動時に原版どおりダークへ戻ること。L13の小サイズ選択を保持したまま両テーマで3入口の選択・表示・再訪・再起動を確認し、テーマ切替で設定revision/値が変化しないことも検証。別clientのテーマが独立することと通常終了・port解放、外部通信なしを記録。

旧220のCoS L13 30/30とEdge証拠は変更前の証拠で、CSS/shell・一部入力画面が変わるため新候補へ無条件継承しない。旧binding・manifest・作者隔離の製品ファイルは変更なしだが共通UIの影響は未検証。自己点検は独立レビューではない。Codexのpytest/import/構文検査/build/GUI/実APIは未実行。S21とP3の正式受入は未完。
