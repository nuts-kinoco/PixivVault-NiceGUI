# P3 X04 重複サムネイルサイズ（CoS検証待ち）

固定231 `S:/Projects/PixivVault-NiceGUI-p3-field-locator-20261027` / `bde53e0dc92dc80336942c29a946a115b2d133a53833d6cb6894e4bf04036fa8` から分離。原版固定commit `3ac3aa0d47a64785251593bcb09bb2d5f6df374b:gui.py:5208–5215`、`docs/DEDUP_COMPARE_UNIFIED_LIST_SPEC_2026-08-29.md:63–69` を読取照合。小88px・中144px・大200px、初期中、表示変更で選択を失わない。5枚超の非表示項目を選択せず、各組1枚以上残す。

`DuplicateView` のtoolbarに小中大を追加。変更時は既存画像・画像なし代替のサイズだけを変更し、再描画やselection_revision変更を行わない。検索・再描画後は現在サイズを適用。スキャン/表示終了中の変更は抑止。既存validate_selection、low_quality_keys、移動不可、receiptや保存データの処理は変更しない。新設定・永続化は追加しない。

合成実View回帰 `tests/test_p3_duplicate_size.py` は初期中、小→大→中の幅高さ・選択・revision・要素同一性、サムネイル欠落の代替表示、検索再描画、非表示6枚目拒否、2枚組の全選択拒否、検索除外後の亡霊選択なし、スキャン前のサイズ指定と終了時を確認する。既存 `tests/test_duplicate_inspection.py` も維持。実行結果は未取得。

CoS: 専用fixture領域で `python -B -m pytest tests/test_p3_duplicate_size.py tests/test_duplicate_inspection.py -q` を実行。合成重複結果を使う現行画面で、1280×720と1920×1080、dark/lightの必要条件で小中大の寸法・選択状態・検索再描画・非表示6枚目・各組1枚残存を通常操作で確認。サムネイル欠落、busy/画面離脱、横溢れや視認性も確認し、元データへ移動/削除を行わない。前後manifest/固定231保全・owned process通常終了・port解放・スクリーンショットと結果を記録。

実装者はpytest/import/構文検査/build/GUI/APIを実行していない。X04製品受入・独立レビュー・P3横断受入は未完。231のCoS checker27/27・DOM1PASS・S21/L13は変更前の証拠。L12/D09/D10、物理隔離、ZIP/DB/receiptは今回の範囲外。
