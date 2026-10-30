# D15 / LearningView fixture 修正の CoS 引継ぎ

固定257の原票 `L12_D15_CPU_257.xml` SHA256 117a85f37df89f659b3dc51df63645cf02539f9e41fcc1ad205a46d6794bdd37。親からの確定報告は L12 62 PASS、D15・既存 LearningView を含む集合 94 PASS / 2 FAIL / 11 setup ERROR。D15 の2件は保存表示assertに到達前の NiceGUI loop 不足、既存11件は Client slot 不足。製品上のD15達成をこの原票だけでは判断しない。

修正はテストのみ。D15は実 asyncio loop を NiceGUI core.loop に一時設定し、製品の `learning restore` coroutine と timer を実行可能にし、復元の実 task を待ってから保存状態のassertを行う。既存 LearningView fixture は実 Client と slot を持つ async fixture で、所有 timer task を cleanup する。callbackやassertは省いていない。loop の monkeypatch はテスト終了時に復元。前候補のL12テスト・製品/D15製品は不変更。

CoS は S 隔離 fixture で257の同じ影響集合を一括再実行する：

```
pytest -q tests/test_capacity_cache_injected.py tests/test_capacity_cancel_nonblocking.py tests/test_capacity_display.py tests/test_capacity_lease_sqlite.py tests/test_capacity_normal_configuration.py tests/test_legacy_capacity_connected.py tests/test_library_filters.py tests/test_library_view.py tests/test_managed_capacity.py tests/test_discovery_view.py tests/test_learning_review.py tests/test_learning_view.py tests/test_p3_residuals_fixtures.py tests/test_p3_review_saved.py
```

原2件が全ての保存・失敗・フィルター・遅延応答assertへ到達し、既存11件が Client slot 内で本来のイベントassertへ到達すること、未await warning と残存 task/process の有無を記録する。D15の実receipt、Retry、filter、再訪の合成GUIは `P3_REVIEW_SAVED_2026-10-28.md` に従う。結果はこの候補で新規回収し、L12/D15/P3受入と独立監査は別判定。
