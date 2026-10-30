# P3 L12 managed容量fixture修正（CoS限定確認待ち）

固定237 `S:/Projects/PixivVault-NiceGUI-p3-capacity-managed-20261027` / `768c37f1a9ccbcad2f4c876f99e8ce47fb5b72d1418ffb2a8e7a3247114a2af8` から別候補へ分離。CoS原票 `artifacts/p3-capacity-managed-20261027/cos-owned-r1/P3_COS_237_MANAGED_CAPACITY_LIMITED_VERIFICATION.json` SHA `0279d45cb23c0a6b0ac00406aea402f0b45dbb8dc56e83254ad94e4c558b9f50` を実読照合。影響回帰47 PASS/1 FAIL/1 SKIP、2解像度GUIで作者91の実測1784B・作者92の706Bを確認。実OS symlink権限不足はSKIP、模擬リンク/reparse拒否はPASS。

失敗テストは保存job完了後、画面を作る前に既存の `await services.refresh_library_snapshot()` を呼ばず、公開snapshotに作者91がいないまま `next` へ進む。services.py:1332–1352 の正規更新は管理catalogを検証して公開snapshotを差し替える。既存 `tests/test_managed_download_services.py:99` と `tests/test_direct_download_services.py:91` に同じ更新がある。修正は該当testに更新1行を追加するだけで、対象作者・実bytesのassert・計測・元の失敗条件は変更しない。

CoS: 候補manifest・固定237/233不変・process終了を照合し、S専用合成fixtureで `python -B -m pytest tests/test_managed_capacity.py::test_real_saved_fixture_recalculates_displayed_managed_author -q` を再実行。保存job完了→正規refresh→作者91→実byte比較→画面snapshotへの反映という元assertまで到達してPASSすること。適切なら `tests/test_managed_capacity.py` の同ファイル回帰も確認。製品src/toolsは237と全hash同一のため、既存GUI証拠の再利用はCoS/独立review担当が判断し、実画面全体の再実行を一律に要求しない。前後hashと所有process終了を記録。

Codexのpytest/import/構文/build/GUIは未実行。リンク実OS testは既存権限下でSKIPのまま。旧folder/ZIP・永続cache・独立review・P3横断受入は未完、L12全体/152操作台帳を加点しない。
