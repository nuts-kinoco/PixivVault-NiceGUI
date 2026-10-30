# P3 S21 field locator（CoS検証待ち）

固定229 `S:/Projects/PixivVault-NiceGUI-p3-ugoira-label-20261027` / `905141c923045ef40c49bfe9a5dec2731a1519f2a9a9a82ceb91ffb957f79e45` から分離。CoS報告 `P3_COS_229_UGOIRA_LABEL_VERIFICATION.json` SHA `75c6b2483e64c6ff4ca3431a6809e79b0dfb00f40bc3f972e93c24d25014dd81`、`GUI_CHECKER_RUN.json` SHA `ca8525c1c5241444bb4a104b61b1c05b97dd4d551a6ab74b6e698a958bc21afb`、失敗ログ SHA `c1bd0ab6c95b097824882c31c68907c008d41b2f2610cd9d111a95736b5e87f3`、CoS専用 `private_checker.py` SHA `33ab47423036270286f476c2ee37c06d64a467ab50eb230d4c56a591c176fb28` を読取り照合。

原因: data-testidがq-fieldの子に置かれるDOMで、元checkerは自身または子孫からq-fieldを探し、親を見つけられず最初のsampleの `text=None` と `dark=False` で停止。CoS成功copyは closestで親を扱い、媒体選択をmedia-download内に限定。新候補はこの2点だけを納品checkerへ反映。コピー専用のROOT変更は不要のため取り込まない。既存の明暗・入力・select3形式・ラベル全文と幅・外部request/page errorのassertは変更しない。

追加 `tests/test_p3_field_locator_browser.py` は実際の納品checker内の両JavaScript式を取得し、合成DOMの親・自身・子孫・不存在を検査する。製品 `src/` は固定229と全ファイルSHA同一。固定229のCoS CPU34/34、既存実機レイアウト、L13、および専用copyによる各解像度27サンプルは変更前の証拠であり、新候補のchecker実行結果としては扱わない。

CoS必要範囲: 前後manifest・固定候補不変・所有processを照合。S隔離環境で `python -B -m pytest tests/test_p3_field_locator_browser.py -q` を実行。所有済み合成fixtureを通常手順で用意し、納品checkerを `python -B tools/check_theme_fields_browser.py --url http://127.0.0.1:PORT --width 1280 --height 720 --output artifacts/p3-field-locator-20261027/cos-owned/1280` で一度実行（PORTは所有fixtureに置換）。出力27サンプルがassert到達、3形式選択とラベル検査が通り、外部request/page errorがないことを確認。1920のCoS成功copy・製品同一証拠の採用判断は検証担当に戻す。成功済みL13操作全体の再実行は要求しない。owned serverをCoSが通常終了しportとhashを確認。

実装者は試験/import/構文/build/GUIを実行していない。S21正式受入・独立レビュー・P3全体は未完。既存証拠や実データは変更していない。
