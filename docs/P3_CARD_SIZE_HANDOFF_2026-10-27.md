# P3 L13 カードサイズ引継ぎ（未実行）

原版 grid_card_size.py の共通キー grid_card_size_mode、既定 large、最大列 6/8/12、170px下限を候補へ接続。既存 PreferencesStore の CAS 更新を使用。ランキング・ライブラリの既存コントロールから shell に集約し、おすすめにも大中小と既存 pv-grid を接続。変更は表示数やページ状態を消さない。設定読取不能/未知値は large、保存失敗は通知して画面変更しない。

CoS: 固定217と候補manifestを照合し、既存S環境の隔離rootで `python -B -m pytest tests/test_p3_card_size_shared.py tests/test_preferences.py tests/test_p3_ui_state.py -q` を実行。さらに実画面のランキング・ライブラリ・おすすめの3入口それぞれから大中小を選び、3画面移動時の選択・列上限・狭幅170px下限、再訪・再起動で復元、保存失敗時の通知と元の選択保持、ページ位置保持を確認。結果・screenshots・process終了・元候補不変を記録。既存 CoS 217 の15 PASSと216のGUI/レビューは基線証拠であり変更後の合格ではない。

実装者はpytest/import/構文検査/build/GUI/native/実APIを実行していない。全画面の実UI操作、同期、再起動は未検証。L13全受入・P3全完了とは扱わない。binding/manifest/作者隔離は変更対象外で、変更後の影響回帰はCoSが判断する。未採用 ZIP 更新・作者改名・物理隔離は変更しない。
