ご主人様、全コードおよび独立検証（Sol）3 ラウンド分の証跡を精査いたしましたわ。製品コード・テストには一切触れず、再設計書をお届けいたします✨

# FL-I15 ローカルタグ補完 学習統合 再設計書

## 1. 根本原因
STALE-DERIVED が経路を変えて 3 連続再発した本質的原因は、**「派生データの永続領域への染み出し」**と**「読み取り・状態保持経路の分散」**の 2 層構造にある。
- **永続・外部副作用への染み出し**: [discovery_learning.py:373](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/discovery_learning.py#L373) が `_SourceDatabase` 経由で補完込みの `_effective_preferences(db)` を検索層へ注入したため、[discovery_engine.py:463-546](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/discovery_engine.py#L463-L546) で補完語が永続カーソル `discovery_search_cursor`（L480-486）を動かし、補完検索のみで得た候補が出所印なしに永続表 `managed_discovery_candidate`（[discovery_learning.py:388-393](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/discovery_learning.py#L388-L393)）へ混入する。さらに `targets`（L484）と `known_tags`（L502）の事前固定により、取得中の無効化が同一バッチの後続リクエストに効かない。一度永続領域へ書き込まれた副作用は読み取り時の再評価では消せない。
- **参照口とキャッシュの分散**: 補完結果は学習正本 DB の `managed_local_seed`（[discovery_learning.py:93](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/discovery_learning.py#L93)）へ書かれ、複数経路が個別に参照・保持する。(a) [discovery_learning.py:181-212](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/discovery_learning.py#L181-L212) が判定保存と同一の FIFO ワーカー内で画像再読込（L195）を行い判定保存を閉塞させる。(b) [ui/learning_view.py:2018-2020](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/ui/learning_view.py#L2018-L2020) は補充競合時に再順位付けを破棄し、[ui/ranking_view.py:219-221](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/ui/ranking_view.py#L219-L221)・[ui/personal_ranking_view.py:139-179](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/ui/personal_ranking_view.py#L139-L179) は旧集合・旧傾向を UI 側で保持し続ける。(c) [local_tagger.py:58-60](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/local_tagger.py#L58-L60) は解決先が非ローカル（`_path_key` が `None`）でも検証を続行し、L252-263 は `self.status` を更新しないため [ui/preferences_view.py:300-302](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/ui/preferences_view.py#L300-L302) が陳腐化する。(d) [composite_library.py:309-316](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/composite_library.py#L309-L316) が呼ぶ `get_fresh` が [legacy_quarantine.py:514-557](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/legacy_quarantine.py#L514-L557) に欠落している。

## 2. 設計の選択肢と推奨
| 案 | 概要 | 永続汚染リスク | 参照漏れリスク | 学習機能価値 | 実装量 |
|---|---|---|---|---|---|
| **A** | 表示・順位付け（読取時再評価）に限定し、検索語・永続キャッシュ・巡回位置には一切書かない | **ゼロ** | 中（窓口未統一なら） | **高**（順位・枠分け・好み印・保存傾向に反映） | 小〜中 |
| **B** | 検索語にも使い、候補・巡回位置へ出所印＋モデル指紋を付け読取時に除外する | **高**（出所昇格・二重カーソル・取得中レース） | 高 | 最高（ただし WD 英語タグは Pixiv 検索適性が低い） | 大 |
| **C** | 検索語を外し、傾向パネル「あなたの保存から」表示のみに反映する | ゼロ | 最小 | **低**（[discovery_ranking.py:440-494](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/discovery_ranking.py#L440-L494) の `trend_rows` は `seed_saved` 不使用） | 最小 |
| **D** | 派生データを単一の読取専用ビューへ閉じ、他経路からの参照を構造的に禁止する | 単独では中（検索へ渡せば汚染残存） | **極低** | 適用範囲（A/B）に依存 | 小〜中 |

- **推奨: 案 A（スコープ限定）を採択し、案 D（単一読取専用ビュー `LocalTagView`）でアクセス境界を強制する統合構成（A + D）**。
- **根拠**: 案 B は候補が通常ソースと重複取得された際の出所昇格規則、二重巡回カーソル、取得中バッチの途中遮断など状態が爆発し、STALE-DERIVED の再発構造を残す。案 C は [discovery_ranking.py:395-402](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/discovery_ranking.py#L395-L402) の順位付け寄与まで捨ててしまい「学習で使う」意図に反する。A + D ならば、永続領域と検索語への混入を構造的にゼロにしつつ、既存 4 ソースの候補順位付け・枠分け・世間ランキング好み印（[ranking_preferred_tags.py:10-14](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/ranking_preferred_tags.py#L10-L14)）へ確実に効かせられる。

## 3. 推奨設計の型・API と既存参照の置換
- **新設モジュール `local_tag_view.py`（全参照の唯一窓口・学習正本 DB 非依存）**:
  - `@dataclass(frozen=True) class LocalTagSnapshot`: `epoch: int`, `fingerprint: str | None`, `by_work: Mapping[str, tuple[str, ...]]`, `seed_counts: Mapping[str, tuple[str, int]]`（正規化タグ → `(表示名, 件数)`）。無効・不一致時は `fingerprint=None`、全マップ空。
  - `class LocalTagView`:
    - `__init__(self, tagger: TaggerManager, local_image: Callable[[str], bytes | None], saved_works: Callable[[], Iterable], saved_ids: Callable[[], Iterable[str]]) -> None`
    - `epoch() -> int`: 設定変更・走査更新・検証失敗で単調増加する世代番号。UI キャッシュ失効判定の唯一キー。
    - `invalidate() -> int`: 内部インメモリキャッシュを破棄し `epoch` を進める。
    - `current_tags(work_id: str) -> tuple[str, ...]`: 設定有効・検証済み指紋一致・保存済み＆API タグ空・`local_image` 再読込成功＆SHA 一致を満たす場合のみタグタプルを返す。それ以外は常に `()`（例外を外へ漏らさず `()` へ倒し、読込失敗時は内部状態を即時無効化）。
    - `snapshot() -> LocalTagSnapshot`: 呼び出し時点の現在条件で対象保存作品を再照合して不変スナップショットを返す。**FIFO 学習ワーカーの外（呼出スレッドまたは `asyncio.to_thread`）で実行**し、判定保存を一切塞がない。
    - `merge_preferences(base: dict[str, dict], snapshot: LocalTagSnapshot) -> dict[str, dict]`: 純粋関数。`snapshot.epoch == self.epoch()` かつ `snapshot.fingerprint` が有効な場合のみ、`base` の浅いコピーへ `seed_saved` を加算して返す（無効・世代不一致時は `base` を無改変で返す）。
    - `refresh_async() -> Future[None]` / `close() -> None`: 専用単一スレッドで未キャッシュ作品の推論のみを行い、結果をインメモリ辞書 `_cache: dict[str, tuple[str, str, tuple[str, ...]]]`（`work_id -> (fingerprint, image_sha256, tags)`）へ格納する。
  - `local_tagger.py` の補強: `_identity(path)`（[local_tagger.py:58-60](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/local_tagger.py#L58-L60)）で解決前後の双方を `_path_key()` 検証し、`None` なら `ValueError('non-local path')` を送出して `verify_model` が `'利用不可（ローカルのフォルダを指定）'` を返す。`TaggerManager.current_fingerprint()`（L252-263）でも `self.status` を更新し、変化時に登録済みリスナーへ通知する。

| 既存参照箇所（file:line） | 置換後の方針 |
|---|---|
| [discovery_learning.py:93, 133-219](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/discovery_learning.py#L133-L219) | `managed_local_seed` と走査・集計ロジックを全廃し、`LocalTagView` へ委譲。 |
| [discovery_learning.py:373](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/discovery_learning.py#L373) / [discovery_engine.py:462-511](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/discovery_engine.py#L462-L511) | `_SourceDatabase` には素の `db.get_discovery_preferences` のみ渡す。検索語・巡回位置・候補永続化に補完を一切通さない。 |
| [discovery_learning.py:346-361, 395-452, 482-491](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/discovery_learning.py#L346-L361) | 各 API は FIFO 投入前に `snap = view.snapshot()` を取得し、FIFO 内では `view.merge_preferences(db.get_discovery_preferences(), snap)` のみ実行。`trend_snapshot` の `local_seed_rows` も `snap.seed_counts` から純粋生成。 |
| [learning_repository.py:68-128](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/learning_repository.py#L68-L128) | 無変更（API タグ・判定履歴の不変条件を維持。FIFO 閉塞も解消）。 |
| [legacy_quarantine.py:514-557](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/legacy_quarantine.py#L514-L557) / [composite_library.py:309-316](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/composite_library.py#L309-L316) | `LegacyQuarantineCatalog.get_fresh()` を実装（キャッシュ非経由で `leaf.get_fresh` または ZIP 直接展開）。[library_media_registry.py:389-404](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/library_media_registry.py#L389-L404) は維持。 |
| [ui/learning_view.py:1986-2082](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/ui/learning_view.py#L1986-L2082) | `_rerank_tagger_order` が `_refill_merge_revision` 変化で終了せず最新 `state.artworks` で再実行。`_trend_snapshot` に `local_epoch` を持たせ、不一致時は再取得。 |
| [ui/ranking_view.py:219-296](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/ui/ranking_view.py#L219-L296) / [ui/personal_ranking_view.py:139-179](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/ui/personal_ranking_view.py#L139-L179) | 保持スナップショットへ `tagger_epoch` を記録し、不一致または [ui/shell.py:428-436](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/ui/shell.py#L428-L436) の設定変更・走査完了通知時に再取得・再描画。 |
| [ui/preferences_view.py:276-302](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/ui/preferences_view.py#L276-L302) / [services.py:727-774, 1212-1213, 2385-2387, 3029-3032](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/services.py#L727-L774) | `services` が `LocalTagView` を所有して `discovery` へ注入。ライブラリ再公開（L2386）時に `view.invalidate(); view.refresh_async()` と UI 通知を発火。`TaggerManager` の公開 `status` を常に最新化。 |

## 4. 永続領域の扱い
- **学習正本 DB（`learning.sqlite3`）**: 補完由来のデータを**一切書かない**。`managed_local_seed` テーブル（[discovery_learning.py:93](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/discovery_learning.py#L93)）を廃止し、初期化トランザクション（L91）で `DROP TABLE IF EXISTS managed_local_seed` を実行して開発中の旧表を破棄する（出荷実績なしのため移行互換不要）。
- **候補キャッシュ・巡回位置**: `managed_discovery_candidate`（L92）および `settings.discovery_search_cursor`（[discovery_engine.py:480](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/discovery_engine.py#L480)）には今後一切補完由来が混入しない。開発環境に既存の汚染候補があれば学習リセットまたはテスト用 DB 再作成で一掃する。
- **検証証跡・推論キャッシュ**: モデル検証記録のみ [preferences.py:170-191](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/preferences.py#L170-L191) の `local_tagger_verification`（設定リビジョンとは独立）に残し、推論結果キャッシュは `LocalTagView` のインメモリ専用（再起動時に再構築可能な揮発状態）とする。

## 5. 移行手順（最小コミット順）
1. **境界と画像経路の修正**: [local_tagger.py:58-88, 252-263](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/local_tagger.py#L58-L88) の解決先非ローカル拒否・公開 `status` 同期と、[legacy_quarantine.py:514-557](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/legacy_quarantine.py#L514-L557) の `get_fresh` 追加（Red→Green）。
2. **`LocalTagView` 導入と DB 分離**: `local_tag_view.py` を新設し、[discovery_learning.py:93, 133-219](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/discovery_learning.py#L133-L219) の `managed_local_seed` 廃止（`DROP TABLE IF EXISTS`）とインメモリ化を実施。
3. **検索切り離しと FIFO 非閉塞化**: [discovery_learning.py:346-491](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/discovery_learning.py#L346-L491) で `_SourceDatabase` から補完を除去し、全読取・順位付けを「FIFO 外 `snapshot()` → FIFO 内 `merge_preferences()`」へ切替。
4. **UI 層のエポック同期**: [ui/learning_view.py:1997-2082](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/ui/learning_view.py#L1997-L2082)・[ui/ranking_view.py:219-296](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/ui/ranking_view.py#L219-L296)・[ui/personal_ranking_view.py:139-179](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/ui/personal_ranking_view.py#L139-L179)・[ui/shell.py:428-436](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/ui/shell.py#L428-L436) の再試行・エポック連動・通知配線を実装。
5. **構造的契約試験の追加と整理**: 次節の表駆動契約試験を追加し、検索語への補完寄与を前提としていた旧テストを新契約へ更新。

## 6. 契約試験の観点
個別経路の場当たり再現ではなく、以下の 3 本の構造的不変条件を表駆動試験で検証する（いずれも偽クライアント＋隔離 DB＋ネットワークガード下）。
1. **永続領域・検索語同一性契約（有効→無効 vs 常時無効）**: 同一シードから「(A) 補完有効→走査→取得→無効化→取得・再起動」と「(B) 常時無効で同一手順」を実行し、`learning.sqlite3` の全テーブル・全行ダンプ（`managed_discovery_candidate`, `settings`, `discovery_preference`, `discovery_swipe_history` 等）と偽クライアントへ渡された検索語列・巡回順が (A)==(B) で完全一致することを確認（[discovery_learning.py:363-402](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/discovery_learning.py#L363-L402)・[discovery_engine.py:462-546](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/discovery_engine.py#L462-L546)）。
2. **全無効化条件の零寄与契約（表駆動）**: 無効化・モデルパス変更・SHA 不一致・解決先 UNC・画像削除・画像権限不可・API タグ後付けの各行について、`LocalTagView.current_tags` / `rank_pending` / `refill_snapshot` / `trend_snapshot` / `preferred_tags` / `RankingView.preferred_tags_in` / `PersonalRankingView.context` のいずれにも補完タグの寄与が 0 であることを一括検証（[ui/ranking_view.py:219](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/ui/ranking_view.py#L219)・[ui/personal_ranking_view.py:139](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/ui/personal_ranking_view.py#L139)）。
3. **非閉塞・競合収束・境界契約**: `local_image` を待機させた状態で `LearningRepository.record`（[learning_repository.py:68](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/learning_repository.py#L68)）がブロックせず完了すること、補充と再順位付けの競合後（[ui/learning_view.py:2018](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/ui/learning_view.py#L2018)）に必ず現在設定の順序へ収束すること、旧保管庫の隔離画像（[legacy_quarantine.py:537](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/legacy_quarantine.py#L537)）が補完に到達し取得停止ゲートを迂回しないこと。

## 7. 本人意図との整合とリスク
- **本人意図の充足**: 「補完タグを学習で使う」意図に対し、API タグのない保存作品から抽出したタグを好みの土台（`seed_saved`）として、候補の順位付け・3 枠配分（[discovery_ranking.py:802-839](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/discovery_ranking.py#L802-L839)）、初期傾向表示（[ui/learning_view.py:2074-2081](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/ui/learning_view.py#L2074-L2081)）、世間ランキングの好み印（[ranking_preferred_tags.py:10-14](file:///S:/Projects/PV-DEV-20261117/wt-ak/src/pixivvault_nicegui/ranking_preferred_tags.py#L10-L14)）へ生かす。一方で「設定を止めれば完全に元の挙動へ戻る」不変条件を構造的に担保できる。
- **弱点・トレードオフ**: (1) 総監督の現設計にあった「補完タグを検索語へ渡す」寄与は意図的に外れるため、既存フォロー・ランキング・関連作者・API タグ検索で候補プールに入らない未知題材の**新規仕入れ**には効かない（将来の再導入判断時は、まず英語 booru タグの検索有効性検証が前提）。(2) インメモリキャッシュのためアプリ再起動直後はバックグラウンド走査完了まで補完が段階反映となる（大規模保存領域での走査・再照合時間は別途計測が必要）。
- **やらないこと**: 検索語・候補キャッシュ・巡回位置への補完混入、API タグ付き作品や既存判定記録・候補除外の改変、同一ユーザーによる実行中ファイルの敵対的競合差し替え対策、モデルフォルダへの書き込み。

---
本設計書の方針（A + D による「検索・永続非書込＋単一 `LocalTagView` 集約」）でよろしいか、ご主人様・総監督のご裁定をお待ちしておりますわ💖 もし検索語への寄与（案 B）をどうしても維持されたい等のご要望がございましたら、すぐに調整案をご用意いたします。
exit=0
