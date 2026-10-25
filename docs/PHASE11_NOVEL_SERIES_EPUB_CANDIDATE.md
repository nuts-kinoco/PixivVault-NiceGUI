# Phase11 小説シリーズ複数章 EPUB 候補

## 対象と位置づけ

- 基準: `phase11/media-parity` の `bf731efa78743788059ba803b5ec6f2d75fa90e5`。
- 対象: 呼び出し側が**明示順に渡した、同じ作者・同じシリーズの複数 `NovelDocument`** から EPUB 2 の `bytes` を作る純粋な部品。章タイトル・本文、NCX 目次、OPF manifest と spine は入力順に対応する。
- 既存 `novel_export.py` の `_validated` と `_xml_text` を再利用し、単一小説と同じ ID・XML 文字種・1作品の長さ・行数・作者名検証を受ける。これは private helper への依存であり、将来 DTO/export 側の契約変更時には同時見直しが必要。
- `NovelDocument` は全件取得の証拠を持たない。欠章検出、シリーズの取得・全件保証、挿絵、保存、キュー、GUI、実作品・実 API は対象外。bytes の生成だけで M2 全体、保存機能、PixivVault 完成の受入とは扱わない。

## 入力と拒否条件

`render_novel_series_epub(documents, *, author_name, series_title) -> bytes`。`documents` は `list` または `tuple` で、2章以上。個々の文書は厳密に `NovelDocument` 型、正規の数字 ID、非空の `series_id`、既存の本文・タイトル制約を満たす必要がある。作者 ID とシリーズ ID の不一致、work ID の重複、空入力、不正 XML 1.0 文字、複数行のシリーズ題名を `MediaPayloadRejected` で拒否し、例外文言に入力値を出さない。

本文は XML 文字種を検証してから XHTML のテキストノードとしてエスケープする。題名、作者名、シリーズ題名もエスケープする。入力文字列を XHTML タグや URL として解釈しない。生成する ZIP の全エントリーは固定パスと固定時刻・権限で、参照先は内部の章 XHTML と NCX のみ。XML namespace の識別子は外部取得を行わない。

## 有限予算（追加値は提案）

| 境界 | 値 | 根拠と扱い |
|---|---:|---|
| 1章の本文 | 2,000,000文字以下 | 既存 `MAX_NOVEL_CHARS` を継承。既存の20,000行未満、題名256文字、作者名256文字も継承。 |
| 章数 | 2〜100章 | **新規提案**。目次・manifest・spine・ZIP エントリー数と処理時間を有限化する暫定上限。実シリーズ分布やUXの受入値ではない。 |
| 合計本文 | 2,000,000文字以下 | **新規提案**。既存の単一小説上限をシリーズ全体にも適用し、多数章でのメモリ増加を抑える。 |
| ZIPの非圧縮データ合計 | 32 MiB 以下 | **新規提案**。本文の XML エスケープと段落タグによる展開、目次・manifest の追加分を含めて圧縮前に計数する。2,000,000文字を全て `&` としても約10 MB、全て改行とした場合の段落タグ増分は約14 MBで、固定XML部分も含む余地がある。 |
| EPUB出力 | 32 MiB 以下 | 既存単一小説 EPUB の `_MAX_EPUB_BYTES` と同じ値。ZIP書込み中と完了後の双方で確認する。 |

これらはオフラインの合成入力に対する候補値であり、実データの分布・読みやすさ・性能を確認した受入値ではない。上限超過は切り詰めずに拒否する。入力順の決定性を守るため、章の自動並び替えも行わない。

## CoS への試験・受入引継ぎ

- 今回は依頼に従い pytest、import、アプリ実行を**未実施**。合成テストコードを `tests/test_novel_series_export.py` に追加した。
- CoS で `python -m pytest -q tests/test_novel_series_export.py tests/test_novel_export.py tests/test_media_parity_policy.py` を実行する。期待値は全件 pass、単一小説側の回帰なし。既存CIは PR で `python -m pytest -q --junitxml=artifacts/pytest.xml` により全テストを起動する。新ファイルを含む結果は PR のCIで確認し、手動実行と区別する。
- ZIP の先頭 `mimetype`、固定メタデータ、各 XML の parse、入力順に一致する章本文・manifest・NCX・spine、外部資源なし、同一入力の同一 bytes を確認する。不正型/ID、作者・シリーズ不一致、重複、空入力、不正 XML 文字、章数・文字数・展開量・出力量の境界は合成テストで検査する。
- 接続前にシリーズ全件保証の根拠と失敗時の扱いを決める。保存・キュー・GUI・挿絵の実装、性能とWindows上の閲覧確認は別ゲートとする。
