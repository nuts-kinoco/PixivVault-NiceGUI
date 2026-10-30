# P3 L13 card-style fixture 修正（CoS実行待ち）

固定219: `S:/Projects/PixivVault-NiceGUI-p3-card-size-20261027` / SHA `c7636ead6da44ad2e667adb1454869411bb0877226459ae6b0a4619d4c2d4180`。CoS focused 29 PASS / 1 FAIL、Edge 1280/1920 初回・再起動4/4 PASS、36操作・保存失敗時保全を報告。L13正式受入は回帰FAILにつき保留。

原票: `artifacts/cos-p3-219-20261027/logs/l13-focused.xml` SHA `054e0345b23a6c34e1b1591cc27d33cd14c95aa37b11ff35643e57c90ae4f8d4`、`P3_COS_219_LIMITED_VERIFICATION.json` SHA `429f830fd8ee21274f21d713332d43ce864ea9cb351fc4c7e9574f5bf0d1ab2b`、`gui-r2/l13-edge-result.json` SHA `d4a5c3ae75eb523f75a688ea5011d9cba1bba966f00fb075408659206bc932e5`。3原票のbytes・hashを照合。

原因: 製品は実際のおすすめグリッド要素の `style('--pv-max-cols:...')` を設定。導入済み NiceGUI `element.py:320` の style は `Style`、`style.py` の `Style` は辞書であり parse は CSS名と値を分離する。したがって `str(e.style)` に元のCSSテキストを探すテスト前提は不正。CoSの実DOM確認とも整合するが、修正後の試験結果ではない。

テストは本物のRecommendViewとrender/change_card_sizeを維持し、pv-gridを一つだけ特定して medium の8、操作後 small の12を辞書キー `--pv-max-cols` から検証し、双方でグリッドの子孫におすすめ作品があることを検証。既存のページ数保持とボタン選択assertも維持。製品・toolsは変更しない。

CoS限定再確認: 新候補manifest・固定219不変・process0を確認し、S隔離環境で `python -B -m pytest tests/test_p3_card_size_shared.py::test_recommend_control_changes_real_render_without_resetting_page -q` を実行。対象が mediumからsmallへの実render、作品の存在、列数8→12、選択状態とページ状態を通過すること。次に同じS隔離条件で `python -B -m pytest tests/test_p3_card_size_shared.py tests/test_preferences.py tests/test_p3_ui_state.py -q` の影響範囲30件を再確認し、結果と原票SHAを記録。候補 src/tools の219との全ファイルSHA同一性を確認し、既存Edgeの証拠再利用は検証担当が判断する。

Codexは試験、製品import、構文検査、build、GUI、実通信を実行していない。独立レビューとL13/P3正式受入は未完。未採用ZIP更新・作者改名・物理隔離の契約には変更なし。
