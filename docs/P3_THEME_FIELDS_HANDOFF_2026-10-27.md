# P3 S21 フィールド明暗追従（CoS検証待ち）

固定223 `S:/Projects/PixivVault-NiceGUI-p3-theme-toggle-20261027` / `aa68e8aa8541eefa6bc8e2602fdb479434c1fcf6b12b0f1fc7f5e364a1c396f7` を別候補へ分離。原票 `artifacts/cos-p3-223-20261027/P3_COS_223_THEME_VALIDATION.json` SHA `6f5d7767d7d3d467d1666051fdfc963386300f35b256742f0093bf1ec660fba5` 実読一致。CoSは223の32/32、Edge4/4とL13同期を確認したが、ライト画面の有効入力に `q-field--dark`、白文字と背景250の視認性欠陥が残りS21未達。

原因: 前候補の削除処理は `.props('...')` が単一行のもののみ処理し、複数行文字列の明示 `dark` が6ファイル11箇所残った。指定を除去し、Quasarのグローバルdark modeからinput/selectが継承する既存経路へ統一。CSSで文字色を上塗りせず、テーマの初期値・非永続・L13の設定DBは変更なし。Quasarの実DOMで継承が有効か未実行。うごイラ自身に明示darkはなく、原票 `light_input_audit_r2.py` は媒体クリック直後の待機なしで収集、実際に「ugoira」試料に小説欄があり、うごイラ固有不具合の確定根拠とはしない。

`tests/test_p3_theme_field_inheritance.py` は実MediaDownloadViewの静止画/小説/うごイラ入力とselectのpropsにdarkがないこと、全UIの複数行/動的文字列propsに固定darkが再発しないことを確認する。新 `tools/check_theme_fields_browser.py` はCoSが所有する起動済み合成fixtureのloopbackだけへ接続。各媒体の実根要素の表示を待ってから、ダーク→ライト→ダークで9入力/selectの `q-field--dark` 状態、computed文字・ラベル・背景色、選択値、disabled状態と画面を記録する。checkerのsuccessはDOMと処理の成否であり、視認性の合格判定ではない。

CoS手順:
1. 新候補manifestと固定223の不変、所有processと使用portを照合。自身のS隔離合成fixtureのみを既存正規手順で起動。実データ/API/Cookieは使用しない。
2. `python -B -m pytest tests/test_p3_theme_field_inheritance.py tests/test_p3_theme_toggle.py tests/test_p3_card_size_shared.py tests/test_preferences.py tests/test_p3_ui_state.py -q` を候補cwdと専用S fixtureで実行。
3. 起動済みfixtureの実URLを指定し、 `python -B tools/check_theme_fields_browser.py --url http://127.0.0.1:PORT --output artifacts/p3-theme-fields-20261027/cos-owned-r1/fields` を実行。PORTは所有するfixtureに置換。各mediaで対応rootが表示されてから実サンプルを得る。スクリーンショットとresult.jsonで両テーマの入力文字・ラベル・select値・無効状態を確認し、元の白文字と近白背景が再発していないか実測値と画面を確認。数値だけで新しい合否基準を追加しない。障害なら原票を残す。
4. 既存223のEdge4条件とL13保存・同期、再訪・再起動、非永続・起動時darkを必要範囲で再確認。checkerはfixture serverを停止しないため、CoSが所有processを通常終了しport解放、前後hashと外部request/console errorを記録。

223のCPU/Edgeは変更前の証拠で新候補へ自動継承しない。Codexのpytest/import/構文検査/build/GUI/実APIは未実行。S21の正式受入・独立レビュー・P3完了は未達。
