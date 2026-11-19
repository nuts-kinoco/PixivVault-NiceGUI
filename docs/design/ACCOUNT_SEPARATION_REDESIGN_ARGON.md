# PV「アカウント分離層」再設計書

## 1. 設計原則：誤りが表現できない構造（Make Illegal States Unrepresentable）

10 ラウンドにわたり経路を変えて再発した主因は、①取得した一覧が裸のタプルや辞書リスト（[author_media.py:350](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/author_media.py#L350), [managed_scheduler.py:782](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/managed_scheduler.py#L782)）として返り、0 件（空一覧）のときに「誰のセッションで取得したか」の証拠が消えること、②状態変更の出口（[managed_scheduler.py:333](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/managed_scheduler.py#L333), [managed_scheduler.py:370](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/managed_scheduler.py#L370), [batch_runs.py:596](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/batch_runs.py#L596)）や開始入口（[services.py:3347](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/services.py#L3347)）が `account_id=None` を許容し照合スキップや最新同期先への暗黙フォールバック（[services.py:3356-3360](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/services.py#L3356-L3360)）を行うことにある。これらを以下の 3 原則で構造的に封じる。

1. **取得結果への所有者束縛（Owned Result）**: 作品・作者一覧の取得結果は、0 件であっても必ず検証済み `AccountScope(account_id, reader)` を内包する不変型 `OwnedListing` として返し、裸の `tuple` では流通させない。
2. **書き込み出口での証憑必須化（Sink Enforcement）**: 永続状態（☆・保留・`last_downloaded`・完了日時）を変更するすべての出口メソッドは、所有者の証憑（`AccountScope` / `OwnedListing` / 必須 `account_id`）なしでは呼び出せないシグネチャとし、トランザクション内で台帳側の所有者と照合する（I1, I4, I7）。
3. **明示操作のみによる削除（Explicit Unstar Only）**: ☆と保留の削除は「現在のフォロー状態（`is_following`）や取得一覧からの欠落」から推定せず、表示アカウントに対する明示的な☆解除（[managed_scheduler.py:627-639](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/managed_scheduler.py#L627-L639)）または検証済み受領証・受付完了のみを根拠とする（I8）。

## 2. 型と API の具体案

### 2.1 [account_scope.py](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/account_scope.py#L1-L20) に導入する不変型と束縛ラッパー

```python
@dataclass(frozen=True, slots=True)
class AccountScope:
    account_id: str          # '[1-9][0-9]{0,19}' 必須。None・空・不正形式は __post_init__ で AccountMismatch 送出
    reader: object | None = None

    def verify_current(self, services, *, error: Exception | None = None) -> str:
        require_current_account(services, self.reader, self.account_id, error=error)
        return self.account_id

@dataclass(frozen=True, slots=True)
class OwnedListing:
    scope: AccountScope      # 0 件 (rows=()) でも取得時の検証済みアカウントと reader を保持
    author_id: str
    target_type: str         # 'both' | 'illust' | 'novel'
    rows: tuple[ object, ...]

    @property
    def account_id(self) -> str:
        return self.scope.account_id

class BoundSchedulerLedger:
    """ディスパッチ 1 回に束縛された ManagedScheduler 書き込み窓口。"""
    def __init__(self, ledger: ManagedScheduler, scope: AccountScope, services): ...
    def defer_work(self, *, author_id: str, work: dict, now: datetime) -> None: ...
    def resolve_deferred(self, author_id: str, work_id: str) -> None: ...
```

### 2.2 取得・実行・書き込み出口の新しいシグネチャ

1. **作者メディア一覧取得**（[author_media.py:232-350](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/author_media.py#L232-L350), [batch_engine.py:113-121](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/batch_engine.py#L113-L121)）
   - `AuthorMediaService.preview_owned(self, author_id: str, target_type: str | None = None, *, expected_account: str | None = None, novel_format='EPUB', ugoira_format='gif', new_only=False) -> OwnedListing`
     - `reader = self.services.live_client` から `account = reader.verify_identity()` を取得し、`expected_account` 指定時は `require_same_account(expected_account, account)` を検証。イラスト・小説の各取得前後で `AccountScope(account, reader).verify_current(self.services)` を実行し、`OwnedListing` を返す（不一致時は `AccountMismatch`）。
   - `ServicesPort.list_author(self, author_id: str, target_type: str | None, *, expected_account: str) -> OwnedListing`
     - `expected_account` を必須とし（`None` は `AccountMismatch`）、`self._author_reader` の可変属性保持（[batch_engine.py:114](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/batch_engine.py#L114)）を廃止して `OwnedListing` を返す。
2. **作者完了と `last_downloaded` 刻印**（[batch_runs.py:596-646](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/batch_runs.py#L596-L646), [batch_engine.py:559-560](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/batch_engine.py#L559-L560)）
   - `BatchRunLedger.complete_author(self, run_id: str, author_id: str, counters: dict | None = None, *, listing: OwnedListing, following: FollowingSyncStore | None = None) -> str | None`
     - `listing: OwnedListing` をキーワード必須とする。`BEGIN IMMEDIATE` 内で `run.kind == 'authors'`、`listing.author_id == author_id`、および `run_account is not None` のとき `require_same_account(run_account, listing.account_id)` を強制する。不一致時は `AccountMismatch` を送出してロールバックし、`run_authors.finished_utc` も `following_authors.last_downloaded` も変更しない（I4, I7）。
   - `BatchRunLedger.complete_bookmark_row(self, run_id: str, row_id: str, counters: dict | None = None) -> None`
     - **アカウントなしで呼べる API を残す理由と安全な使い方**: ブックマーク実行（[batch_engine.py:594](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/batch_engine.py#L594)）の `row_id`（公開 `"1"` / 非公開 `"2"`）は作者ではなく `last_downloaded` を一切刻印しない。内部で `run.kind == 'bookmarks'` を強制検証し、`authors` 実行から呼ばれた場合は `BatchRunError` を送出するため、作者履歴への混入は起きない。
3. **保留の追加・解消**（[managed_scheduler.py:333-374](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/managed_scheduler.py#L333-L374)）
   - `ManagedScheduler.defer_work(self, *, author_id: str, work: dict, now: datetime, account_id: str | None = None) -> None`
   - `ManagedScheduler.resolve_deferred(self, author_id: str, work_id: str, *, account_id: str | None = None) -> None`
     - `self._account_guard` が真（`attach_favourites` 接続済み、[managed_scheduler.py:93-97](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/managed_scheduler.py#L93-L97)）のとき、`account_id is None` または `require_same_account(self._bound_account(), account_id)` 不成立なら即 `ScheduleAccountError('mismatch')` を送出する。ディスパッチ経路からは `BoundSchedulerLedger` 経由でのみ呼び出し、各操作直前に `scope.verify_current(services)` を強制する。
     - **アカウントなし（`account_id=None`）を残す理由**: `attach_favourites` を使わない単体スケジューラ試験（`_account_guard == False`）のみ互換維持する。本番（`_account_guard == True`）では `account_id=None` は無条件に拒否される。
4. **☆の読み書きと定期設定保存**（[favourites.py:17-44](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/favourites.py#L17-L44), [managed_scheduler.py:525-651](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/managed_scheduler.py#L525-L651)）
   - `FavouriteBook.ids(self, account_id: str) -> tuple[str, ...]` / `FavouriteBook.set(self, user_id: str, favourite: bool, *, account_id: str, expected: bool | None = None) -> bool`
   - `ManagedScheduler.favourites(self, account_id: str | None = None) -> tuple[str, ...]`: `self._favourites_source is not None` のとき `account_id is None` を `ScheduleAccountError('mismatch')` で拒否。
   - `ManagedScheduler.configure_all(..., *, account_id: str | None = None)` / `set_favourites(..., *, account_id: str)`: `_settings_transaction`（[managed_scheduler.py:540-555](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/managed_scheduler.py#L540-L555)）で `book is not None` のとき `account_id is None` または `account_id != book.account()` なら `ScheduleAccountError('mismatch')` を送出（I1, I2, I5）。
5. **バッチ開始**（[services.py:3347-3365](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/services.py#L3347-L3365), [ui/unified_download_view.py:908-916](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/ui/unified_download_view.py#L908-L916)）
   - `ApplicationServices.start_batch_run(self, author_ids, *, kind='authors', origin='manual', target_type=None, account_id: str | None)`
     - `account_id` 省略時の `last_synced_account()` への暗黙フォールバック（[services.py:3356-3360](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/services.py#L3356-L3360)）を廃止。
     - `account_id is not None` のとき、`following_sync` があれば `require_same_account(account_id, self.following_sync.last_synced_account())`、さらに `live_client` の検証済み ID と一致を確認する。
     - **`account_id=None` を許容する条件**: 手入力 ID（`origin="id"`）のみでフォロー一覧由来の作者を含まない手動実行に限る。このとき `runs.account_id` は `None` となり、`complete_author` は `following.record_downloaded` を呼ばない（[batch_runs.py:638-639](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/batch_runs.py#L638-L639)）。

## 3. 各経路への適用表

| 経路 | 主な箇所 (`file:line`) | 受け取る型・証憑 | 検証の位置と条件 | 不一致時に保つ不変条件（I1〜I8） |
|---|---|---|---|---|
| ① 直接キュー | [managed_scheduler.py:716-970](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/managed_scheduler.py#L716-L970) | `AccountScope`, `BoundSchedulerLedger` | `verify_identity()` と `_bound_account()` の一致後、全 GET 直後と `defer_work` / `resolve_deferred` 出口 | 新規受付 0、既存 `deferred_work` 不変、`dispatch_history.finished_utc=None`（[managed_scheduler.py:292](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/managed_scheduler.py#L292)） |
| ② 作者メディア経由キュー | [managed_scheduler.py:972-1069](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/managed_scheduler.py#L972-L1069), [author_media.py:232-350](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/author_media.py#L232-L350) | `OwnedListing`, `BoundSchedulerLedger` | `preview_owned(aid, expected_account=bound)` 内、および `BoundSchedulerLedger` の各保留操作出口 | 0 件返却でも `listing.account_id != bound` なら即中止。保留・受領証・完了日時を一切変更しない |
| ③ バッチ（自動・エンジン） | [services.py:3327-3345](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/services.py#L3327-L3345), [batch_engine.py:526-564](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/batch_engine.py#L526-L564), [batch_runs.py:596-646](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/batch_runs.py#L596-L646) | `OwnedListing` (`list_author` 返値) | `list_author` 内、`_download` 各周回、`complete_author` トランザクション内（`run_account == listing.account_id`） | 実行を `failed` (`author_account_mismatch`) で停止、当該作者を `pending` に戻し `finished_utc`・`last_downloaded` 不変 |
| ④ 手動バッチ開始 | [ui/unified_download_view.py:775-833](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/ui/unified_download_view.py#L775-L833), [同:908-916](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/ui/unified_download_view.py#L908-L916), [unified_download_state.py:96-150](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/unified_download_state.py#L96-L150) | target 辞書の `account_id: str \| None` | `add_target` / `add_targets_bulk` でフォロー行の `account_id` を保持し、`run()` から `start_batch_run` へ渡し `last_synced_account()` と照合 | 同期先切替後の古い画面からの開始を拒否し、案内メッセージを表示して run を作成しない |
| ⑤ ☆の保存（画面・行トグル） | [ui/unified_download_view.py:835-856](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/ui/unified_download_view.py#L835-L856), [services.py:2140-2152](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/services.py#L2140-L2152), [favourites.py:27-44](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/favourites.py#L27-L44) | 行が保持する `account_id: str`, `expected: bool` | ` ManagedScheduler._settings_transaction` 内で `require_same_account(account_id, book.account())` と `expected` CAS | 不一致・失敗時は `False` を返し、画面の☆表示を変更せず再読込を案内（I1, I2, I5） |
| ⑥ 定期設定の保存（フォーム） | [ui/scheduler_view.py:92-119](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/ui/scheduler_view.py#L92-L119), [同:227-302](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/ui/scheduler_view.py#L227-L302), [managed_scheduler.py:539-646](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/managed_scheduler.py#L539-L646) | `self._settings_account`, `expected_revision` | `_settings_transaction` 内で `book.account()` と照合。`_store_favourites` は `following or star` を満たす選択を維持 | 別アカウントへの上書き拒否。フォロー解除された既☆作者もフォームで維持されていれば☆・保留を保持（I8） |
| ⑦ フォロー同期 | [following_sync.py:324-388](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/following_sync.py#L324-L388), [同:441-450](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/following_sync.py#L441-L450) | `reader.require_identity()` (前後2回) | 公開・非公開フォロー取得の前後で `require_same_account` と `expected_revision` CAS。`is_following` のみ更新 | 途中切替時は `ROLLBACK`。同期や `record_unfollowed` で既存の `is_favorite`・`last_downloaded` を消さない（I8） |
| ⑧ ブックマーク経路 | [services.py:2798-2943](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/services.py#L2798-L2943), [同:3307-3325](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/services.py#L3307-L3325), [batch_engine.py:300-315](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/batch_engine.py#L300-L315) | `expected_account`, `verified_bookmark_account` | `refresh_bookmark_cache` の取得前・公開直前fence（`check_current_read`）、再開時の `has_verified_bookmark_account` | 不一致時はキャッシュ非公開・再開を `interrupted` (`bookmark_account_mismatch`) で保留し確認を求める |

## 4. 現在の第 10 回の差分（`account_scope.py` 等）との関係

- **活かすもの**:
  - `AccountMismatch` と `require_same_account`（[account_scope.py:4-14](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/account_scope.py#L4-L14)）。
  - `_store_favourites` で未フォローの既☆作者を維持する `bool(following or star)` と、`attached_account == self._bound_account()` のときのみ明示解除された☆作者の保留を消す判定（[managed_scheduler.py:625-640](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/managed_scheduler.py#L625-L640)）。
  - `finish_dispatch`（[managed_scheduler.py:292](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/managed_scheduler.py#L292)）と `set_run_state`（[batch_runs.py:446](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/batch_runs.py#L446)）における `AccountMismatch.code` 時の `finished_utc = None` 保護。
- **置き換えるもの**:
  - 呼び出し側に散在する `check_account()` クロージャと `account_id=None` / `expected_account=None` のオプショナル引数リレー（[managed_scheduler.py:993-1006](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/managed_scheduler.py#L993-L1006), [batch_engine.py:113-121](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/batch_engine.py#L113-L121), [batch_engine.py:520-540](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/batch_engine.py#L520-L540)）を、`OwnedListing`・`BoundSchedulerLedger` および出口メソッドの必須検証に置き換える。
- **`require_current_account` が恒真になる疑い（`getattr` 既定値）の扱い**:
  - 現在の `getattr(reader, '_verified_identity', account)`（[account_scope.py:20](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/account_scope.py#L20), [services.py:3363](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/services.py#L3363)）は、実機 `LivePixivReadClient`（`__init__` で `self._verified_identity = None` を定義、[pixiv_read_client.py:723](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/pixiv_read_client.py#L723)）では属性が存在するため動作するが、`_verified_identity` 属性を持たない偽クライアントやアダプタでは既定値 `account` が返り恒真になる。一方、[services.py:3806](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/services.py#L3806) は既定値 `None` を使っており不統一である。
  - **解決策**: `getattr` の既定値に `account` を渡すことを全面禁止する。`LivePixivReadClient` とテスト用 fake reader の双方で `verify_identity()` / `require_identity()` 実行時に公開プロパティまたは `_verified_identity` が必ずセットされる契約とし、`require_current_account(services, reader, account, *, error=None)` は `observed = getattr(reader, '_verified_identity', None)` を読み、`observed is None` の場合は `'verify_identity'` が未呼び出しでない限り `AccountMismatch` で fail closed とする（fake reader には `AccountScope` 生成時に観測 ID を束縛する）。

## 5. 移行手順（各コミットで既存試験が通る最小コミット順）

互換の一時層（`account_id=None` を黙って通すフォールバック）は置かない。ただし UI の単発プレビュー表示用 `AuthorMediaService.preview()` は `preview_owned(...).rows` を返すラッパーとして残す。

1. **Commit 1（型定義と `require_current_account` の厳格化）**: [account_scope.py](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/account_scope.py#L1-L20) に `AccountScope`、`OwnedListing`、`BoundSchedulerLedger` を追加し、`require_current_account` の既定値 `account` を廃止（fake reader 側の初期属性を補完し既存試験 PASS を確認）。
2. **Commit 2（定期チェック 2 経路と保留・☆出口の束縛）**: [author_media.py:232](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/author_media.py#L232) に `preview_owned` を追加。[managed_scheduler.py:333-374](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/managed_scheduler.py#L333-L374) の `defer_work` / `resolve_deferred` と [同:648](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/managed_scheduler.py#L648) の `favourites` で `_account_guard` 時の `account_id` を必須化し、`submit_author_batch`・`submit_author_media_batch` を `BoundSchedulerLedger` 経由へ移行。
3. **Commit 3（バッチエンジンと `complete_author` 出口の束縛）**: [batch_engine.py:113](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/batch_engine.py#L113) の `ServicesPort.list_author` を `expected_account` 必須・`OwnedListing` 返却に変更。[batch_runs.py:596](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/batch_runs.py#L596) の `complete_author` を `listing: OwnedListing` 必須に変更し、ブックマーク完了を `complete_bookmark_row`（[batch_engine.py:594](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/batch_engine.py#L594)）へ分離。
4. **Commit 4（手動バッチ開始のアカウント束縛と構造契約試験）**: [unified_download_state.py:96-150](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/unified_download_state.py#L96-L150) でフォロー一覧由来ターゲットに `account_id` を保持させ、[ui/unified_download_view.py:908-916](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/ui/unified_download_view.py#L908-L916) から [services.py:3347-3365](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/services.py#L3347-L3365) の `start_batch_run` へ渡して `last_synced_account()` と一致検証。契約試験を追加。

## 6. 契約試験の観点

経路ごとの個別再現に加え、「照合を 1 行忘れても永続状態を変更できない」ことを構造的に検査する試験を新設する。

1. **全書き込み出口のメタ試験（Sink Guard Reflection Test）**:
   - 永続変更メソッド（`FavouriteBook.ids`, `FavouriteBook.set`, `ManagedScheduler.favourites`, `ManagedScheduler.set_favourites`, `ManagedScheduler.set_star`, `ManagedScheduler.configure_all`, `ManagedScheduler.defer_work`, `ManagedScheduler.resolve_deferred`, `BatchRunLedger.complete_author`）をテーブルで列挙し、`account_id=None`・未束縛・別アカウント B の `OwnedListing` を渡したときにすべて `AccountMismatch` / `ScheduleAccountError` で拒否され、SQLite 全テーブルのダンプ（行数・`revision`・`is_favorite`・`deferred_work`・`last_downloaded`・`finished_utc`）が呼び出し前後で完全一致することを一括検証する。
2. **全実行経路×不一致タイミングの表駆動試験（Cross-Path Mismatch Matrix）**:
   - 経路（直接キュー・作者メディア経由キュー・作者バッチ・手動バッチ開始）× 異常レスポンス（アカウント B の 0 件空一覧・アカウント B の非空一覧・作品詳細取得時のアカウント B への遷移）をパラメータ化し、共通の検証関数で「新規ジョブ受付 0 件、アカウント A/B の☆・保留・`last_downloaded` 不変、完了日時 `finished_utc is None`（I7）」を機械的に確認する。

## 7. やらないこと／リスク

- **やらないこと（範囲外）**:
  - 受入済みの保管領域（`managed_generations.py`）の変更、旧 DB への書き込み、旧 DB からの☆の自動取込（I3）。
  - 3 つの SQLite 台帳（`following.sqlite3`, `.pixivvault-managed-scheduler.sqlite3`, `batch-runs.sqlite3`）の単一 DB へのスキーマ統合（既存の `ATTACH DATABASE` による `_settings_transaction` [managed_scheduler.py:554](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/managed_scheduler.py#L554) で☆と定期設定の原子性は確保済み）。
  - 同一ユーザーのローカルファイル直接改変や、単一 I/O ステップの途中でプライベート属性を裏から書き換えるホワイトボックス TOCTOU 注入への対策（脅威モデル外）。
- **この設計の弱点と残るリスク**:
  - `BatchRunLedger.complete_author`（[batch_runs.py:635-646](file:///S:/Projects/PV-DEV-20261117/wt-w/src/pixivvault_nicegui/batch_runs.py#L635-L646)）は `batch-runs.sqlite3` の完了コミット直後に `following.record_downloaded` を呼ぶ 2 段階書き込みであるため、その極小窓でプロセスが強制終了した場合は `run_authors.state='done'` かつ `stamp_written=0` となる（別アカウント混入は起きず、`stamp_written` 列で判別可能）。
  - ダウンロード画面でフォロー一覧由来の作者（`origin="follow"`, `account_id="A"`）と手入力 ID（`origin="id"`）を混在させて選択した場合、実行開始前にアカウント B へ同期されるとバッチ全体の開始が拒否される（安全側への倒れ込みであり、再読込で解消する）。
exit=0
