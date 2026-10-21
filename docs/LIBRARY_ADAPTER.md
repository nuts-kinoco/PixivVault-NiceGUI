# 既存ライブラリDB読取アダプタ

`ReadonlyLibraryRepository(db_path)` は既存PixivVaultから独立して動くSQLite読取専用アダプタ。明示パスが必須であり、既定DBや環境変数を参照しない。`file:` URI `mode=ro` で接続し `PRAGMA query_only=ON` を設定する。`immutable=1` はWALの変更を見失うため使用しない。`close()` または `with` で接続を閉じる。SQL検索値はバインドし、`%`・`_`・バックスラッシュは文字として扱う。`offset` は0〜1,000,000、`limit` は1〜100。

## 静的確認した根拠

- `/projects/PixivVault/database.py:267-275`: `works.work_id`, `title`, `page_count`, `is_deleted` の初期DDL。
- `/projects/PixivVault/database.py:278-290`: `works.user_id` と `tags` の後付け列。
- `/projects/PixivVault/database.py:332-340`: `following_users.user_id`, `name` のDDL。
- `/projects/PixivVault/database.py:903-919`: ライブラリ一覧で削除済みを除く `is_deleted=0` と `works` の選択列。
- `/projects/PixivVault/database.py:1806-1816`: `following_users` の読取と表示名 `name`。
- `PixivVault/database.py:1561-1579,1698-1708`: `upsert_work()` と `set_work_tags()` が `works.tags` へJSON配列を保存する実装を確認。文字列リストとして読める行だけ変換し、不正なJSONは空タグとする。

`Artwork.author` は `following_users.name` を `works.user_id` と結合して作る。対応するフォロー作者行が存在しない場合は `user_id` を代替表示にする。実際のPixiv作者名と常に一致する保証はない。`page_count` がNULL/0の場合は `Artwork` の制約に合わせて1を割り当てる。未知の説明文・縦横比は取得しない（`Artwork` の既定値を使う）。新規カラム、スキーマ更新、外部API、画像取得、GUI連携は実装しない。

テストは `tests/test_library_repository.py` の一時DBだけに接続する。WAL中の未checkpoint行が見えることも確認する。本番DBに対する読取実証は行っていない。
