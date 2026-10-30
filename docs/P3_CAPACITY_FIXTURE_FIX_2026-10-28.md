# L12 fixture 修正の CoS 引継ぎ

固定254の `l12-impact.stdout.txt` SHA256 8433edb88a8ce408c378219785ac64230602e22a38ed4c5b08007f5bcafbe293 は 59 PASS / 3 FAIL。取消待機の旧失敗は PASS。今回の候補は256（D15）を基にした未試験候補であり、その成功数を引き継がない。

2件のUIテストは `LibraryView.reload` で実 coroutine を `nicegui.background_tasks.create` に渡す時点で NiceGUI `core.loop is None` となっていた。テストの asyncio ループで本物の coroutine を task に登録し、初期復元と再起動復元の実 task を明示的に待つ。表示失効、SQLite commit決定後の挙動、旧値、再起動の元assertを保持。未await warning はこの失敗で作られた coroutine に対応する可能性があるため、再実行時に警告も確認する。

通常構成テストは設定rootを読取DBの親の下に置き、既存の root 非交差検査に拒否された。合成DBを専用の兄弟ディレクトリへ移し、画像と設定も別の兄弟rootとして元の保存・再生成・復元・原DB/画像不変assertを保持。実保存先やroot検査は変更しない。原票が示す範囲に製品変更を要する根拠はない。

CoS: 隔離 S fixture root で、固定254の原影響集合を新候補に対して次のとおり再実行し、結果・警告・所有process終了を記録する。

```
pytest -q tests/test_capacity_cache_injected.py tests/test_capacity_cancel_nonblocking.py tests/test_capacity_display.py tests/test_capacity_lease_sqlite.py tests/test_capacity_normal_configuration.py tests/test_legacy_capacity_connected.py tests/test_library_filters.py tests/test_library_view.py tests/test_managed_capacity.py
```

3件が実際に本来の後続assertまで到達すること、取消前・決定後・commit失敗・不明の区別、DB再起動とUI失効後の非反映、root交差拒否、warning有無を確認する。D15差分は256から保持しているが、この回帰のみではD15の受入を意味しない。D15のCoS手順は `docs/P3_REVIEW_SAVED_2026-10-28.md` に別記。元旧ZIPまたはfolder一方の容量、managed別表示を維持。実データ・実API、独立監査、GUI、P3全体の受入は未実施。
