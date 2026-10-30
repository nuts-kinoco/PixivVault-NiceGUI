# P3 ZIP作者skip経路の追加回帰（未実行）

基線: p3-export-admission-fix-20261027 / dirty195 / SHA a9110caa09a790db73485a976475f078b0344e4ecf9f26e77d6740816ae0f98e。新候補 S:/Projects/PixivVault-NiceGUI-p3-zip-author-skip-20261027、branch phase11/p3-zip-author-skip-20261027、HEAD 2cf4a7a7286dd728e8df8125094498a3622babba。元193の73PASS/4codecSKIPは親から受領した基線結果であり、新候補の実行結果ではない。旧195/193/191/189は編集・process操作なし。

既存195を照合し、legacy_membershipのZIP拒否はscan_authorsの作者行を囲む_itemに捕捉され、次の作者を継続することを確認。製品コードは二重変更しない。

tests/test_export_admission_stability.pyへ test_zip_author_in_scan_authors_is_skipped_and_next_author_completes を追加。合成保存済み静止画receiptと検証済み作者受付境界を使い、最初の作者23が旧ZIP所在で拒否されても作者24のauthor-projectionがdoneになること、再scanで作者24のjobを増やさないこと、元ZIP bytes/旧root内容不変、作者23の新folderを作らないこと、scan_errorsの当該作者記録を保持することを検査。作者admission自体の実検証は前候補のtest_author_output_connection.pyで担保する構成を維持。ZIP書換え権限、未知データ採用、削除/移動は追加しない。

CoS後継/承認済みClaudeは既存手順の前段に次を含める。既存S venv、PYTHONPATH=<candidate>/src、PYTHONUTF8=1、PYTHONDONTWRITEBYTECODE=1、新しい深いS TEMP/TMP/basetemp/log。原ZIP拒否/最大選択警告/初回intent固定/後続継続の期待を緩めない。

```powershell
& S:/Projects/PixivVault-NiceGUI/.venv/Scripts/python.exe -B -m pytest tests/test_export_admission_stability.py tests/test_author_output_connection.py tests/test_author_projection_layout.py --basetemp <fresh-deep-S-temp> --junitxml <fresh-S-log>/zip-author-skip.xml
```

Codexはpytest/import/構文/GUI未実行。製品runtimeは195と同一で、8件の新admission回帰（195で7件+今回1件）も未実行。前段合格後の関連/GUIはP3_EXPORT_ADMISSION_FIX_HANDOFF_2026-10-27.mdを維持。独立再レビュー、ZIP更新/改名/物理隔離契約、全原受入/P3閉鎖/P4移行は未達。最終hash/差分/旧195保全/書込み終了は専用final。
