"""Legacy database logic, imported unchanged except the sandbox constructor.
Source SHA256: 4c54e34ef2cf9a082ccd6a1579d676cbdc4a767f1a9558f51abf7cce399ec7e7
Source: PixivVault/database.py. Preserve original SQL and learning algorithms.
"""
import json
import logging
import os
import pathlib
import sqlite3
import threading
import unicodedata
from contextlib import contextmanager
from datetime import datetime
logger = logging.getLogger(__name__)

# --- 発掘（学習タブ）の判定ルーティング ---------------------------------------
# 合意書 DISCOVERY_LEARNING_LOGIC_AGREEMENT_2026-08-06.md §2-2 の表をそのままコードにしたもの。
# 保存する文字列は iOS版 SwipeVerdict.rawValue と1文字違わず揃える（superLike のcamelCase含む）。
# 値は (タグ側で増やす列, 作者側で増やす列)。None はその軸を動かさないことを表す。
#
# 「合わない」は理由によって行き先が変わる:
#   絵柄が合わない  → 作者だけを下げる。題材タグは無傷（別の作者の同じ題材はまた出したい）
#   ジャンル/理由なし → タグだけを下げる。作者は無傷（作者を沈めると出会いの機会自体が失われる）
_JUDGMENT_ROUTING = {
    ("superLike", None):    ("superlike_count", "superlike_count"),
    ("like", None):         ("like_count", "like_count"),
    ("skip", None):         ("skip_count", "skip_count"),
    ("hate", "genre"):      ("hate_count", None),
    ("hate", "style"):      (None, "hate_count"),
    ("hate", None):         ("hate_count", None),   # 理由を答えなかった場合の既定(§4-4)
}

VALID_VERDICTS = ("skip", "hate", "like", "superLike")
VALID_HATE_REASONS = ("genre", "style")

# --- 作品メタデータ（docs/WORK_METADATA_SPEC_2026-08-10.md） -------------------
# upsert_work(meta=...) が書く列。ここが唯一の一覧で、core.extract_work_meta() の
# 戻り値のキーと1対1で対応する。増やすときは _create_table() の ALTER TABLE も足す。
# meta_attempts はここに入れない（成功/失敗で別に動かす列のため）。
META_COLUMNS = (
    "bookmark_count", "like_count", "view_count", "comment_count",
    "x_restrict", "ai_type", "illust_width", "illust_height",
    "upload_date", "user_account", "work_meta", "meta_fetched_at", "meta_source",
)

# 取得を試みて取れなかった作品を、何回目で対象から外すか。
# core.DELETION_CONFIRM_STREAK と同じ 3 に揃えてあるだけで、根拠のある数字ではない。
META_MAX_ATTEMPTS = 3

# 埋め込み（絵柄・構図）で、1ページを何回失敗したら対象から外すか。
# メタデータと同じ 3。振る舞いも揃えてある（docs/DOWNLOAD_TIME_INFERENCE_SPEC_2026-08-11.md §5-3）。
EMBED_MAX_ATTEMPTS = 3


def merge_work_meta(old_json, new_dict):
    """work_meta を「空の値で既存を潰さない」形でマージします（spec §4-1）。

    同じ作品でも経路によって中身が違う。とくに description は
      詳細API   : 本文（生HTML）
      A経路API  : 空文字 ""
    なので、単純に入れ替えると後から走ったA経路が本文を消す。

    「pixiv 側で本当に空になった」は反映されない。これは意図して受け入れた副作用で、
    消えるより残るほうがマシ、という判断（spec §4-1）。
    """
    try:
        old = json.loads(old_json) if old_json else {}
    except (ValueError, TypeError):
        old = {}
    if not isinstance(old, dict):
        old = {}
    for k, v in (new_dict or {}).items():
        # ⚠️ `v in ("", [], {}, None)` と書かないこと。0 や False が巻き込まれる形に
        #    見えて読み手が不安になるため、明示的に書く（0/False は空ではない）。
        is_empty = v is None or v == "" or v == [] or v == {}
        if is_empty and k in old:
            continue
        old[k] = v
    return old


def normalize_tag(tag):
    """タグを `discovery_preference` の主キーとして使える形に正規化します。

    1. Unicode NFKC（全角英数・半角カナのゆれを吸収）
    2. 前後の空白を除去
    3. ASCII英字のみ小文字化（`R-18`と`r-18`、`Fate`と`fate`を同一視する）。
       **日本語には触らない**——「ウマ娘」を加工しても同一視の役に立たず、副作用だけが残るため。

    空になったものは None を返す（呼び出し側で捨てる）。
    表示に使う元の表記は `discovery_preference.display_tag` に別途保存する。
    """
    if not tag:
        return None
    t = unicodedata.normalize("NFKC", str(tag)).strip()
    if not t:
        return None
    # ASCII英字だけを小文字化する（str.lower() は非ASCIIの大文字小文字も動かすため使わない）
    t = "".join(chr(ord(ch) + 32) if "A" <= ch <= "Z" else ch for ch in t)
    return t or None


class Database:
    def __init__(self, db_path, *, sandbox_root):
        if db_path is None or sandbox_root is None:
            raise ValueError("explicit database path and sandbox_root are required")
        root = pathlib.Path(sandbox_root).resolve(strict=True)
        if not root.is_dir():
            raise ValueError("sandbox_root must be a directory")
        path = pathlib.Path(db_path)
        if not path.is_absolute() or not str(path).strip():
            raise ValueError("absolute db_path required")
        actual = path.resolve(strict=False)
        if actual == root or root not in actual.parents:
            raise ValueError("database path must be under sandbox_root")
        self.db_path = str(actual)
        try:
            self.conn = sqlite3.connect(self.db_path, check_same_thread=False, autocommit=True)
        except TypeError:
            self.conn = sqlite3.connect(self.db_path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.conn.execute("PRAGMA journal_mode=WAL;")
            self.conn.execute("PRAGMA busy_timeout=5000;")
        self._create_table()


    def _create_table(self):
        with self.lock, self.conn:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS works (
                    work_id TEXT PRIMARY KEY,
                    title TEXT,
                    page_count INTEGER,
                    create_date TEXT,
                    update_date TEXT,
                    last_backup TEXT,
                    is_deleted BOOLEAN DEFAULT 0
                )
            """)
            try:
                self.conn.execute("ALTER TABLE works ADD COLUMN user_id TEXT")
            except sqlite3.OperationalError:
                pass
            
            try:
                self.conn.execute("ALTER TABLE works ADD COLUMN content_type TEXT DEFAULT 'illust'")
            except sqlite3.OperationalError:
                pass

            try:
                self.conn.execute("ALTER TABLE works ADD COLUMN tags TEXT")
            except sqlite3.OperationalError:
                pass

            try:
                # フェーズB「削除検知」が、今回の一覧に何回連続で見つからなかったかを持つ列。
                # 見つかるたびに0へ戻り、閾値に達したら削除確定する（tools/migrate_add_missing_streak_column.py）。
                self.conn.execute("ALTER TABLE works ADD COLUMN missing_streak INTEGER NOT NULL DEFAULT 0")
            except sqlite3.OperationalError:
                pass

            # 【作品メタデータ】docs/WORK_METADATA_SPEC_2026-08-10.md §3-2-1。
            # pixiv の作品詳細から取れる値のうち、並べ替え・絞り込みに使うものを列に持ち、
            # 残りを work_meta（JSON）へまとめる。
            # ⚠️ try/except は1本ずつ分ける。まとめて1つの try に入れると、途中の1本が
            #    既存だった時点で残りが実行されない（既存の4本が分かれているのも同じ理由）。
            for _ddl in (
                "ALTER TABLE works ADD COLUMN bookmark_count INTEGER",
                "ALTER TABLE works ADD COLUMN like_count INTEGER",
                "ALTER TABLE works ADD COLUMN view_count INTEGER",
                "ALTER TABLE works ADD COLUMN comment_count INTEGER",
                "ALTER TABLE works ADD COLUMN x_restrict INTEGER",
                "ALTER TABLE works ADD COLUMN ai_type INTEGER",
                "ALTER TABLE works ADD COLUMN illust_width INTEGER",
                "ALTER TABLE works ADD COLUMN illust_height INTEGER",
                "ALTER TABLE works ADD COLUMN upload_date TEXT",
                "ALTER TABLE works ADD COLUMN user_account TEXT",
                "ALTER TABLE works ADD COLUMN work_meta TEXT",
                "ALTER TABLE works ADD COLUMN meta_fetched_at TEXT",
                "ALTER TABLE works ADD COLUMN meta_source TEXT",
                # 取得を試みて取れなかった回数。取れたら触らない（成功しないかぎり減らない）。
                # 一時的な障害（429・通信断・未知の例外）では増やさない（spec §6-9）。
                "ALTER TABLE works ADD COLUMN meta_attempts INTEGER NOT NULL DEFAULT 0",
                # 【#52・ライブラリのブックマーク機能】独立したON/OFFフラグ（フォロー中作者の
                # 作品かとは別軸・両方真でもよい）。docs/design/LIBRARY_BOOKMARK_SPEC_2026-08-16.md
                # §2-1。書き込み口は set_work_bookmarked() だが、呼び出し元は1経路に決め打ちしない
                # （マイブックマークからのDL確定時・将来の#46踏み台APIバックフィル等、複数経路を許す）。
                "ALTER TABLE works ADD COLUMN is_bookmarked BOOLEAN DEFAULT 0",
            ):
                try:
                    self.conn.execute(_ddl)
                except sqlite3.OperationalError:
                    pass    # 既に在る（＝2回目以降の起動）

            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS following_users (
                    user_id TEXT PRIMARY KEY,
                    name TEXT,
                    account TEXT,
                    profile_img TEXT,
                    last_downloaded TEXT,
                    is_zipped BOOLEAN DEFAULT 0
                )
            """)
            try:
                self.conn.execute("ALTER TABLE following_users ADD COLUMN is_zipped BOOLEAN DEFAULT 0")
            except sqlite3.OperationalError:
                pass
            
            try:
                self.conn.execute("ALTER TABLE following_users ADD COLUMN is_favorite BOOLEAN DEFAULT 0")
            except sqlite3.OperationalError:
                pass

            try:
                self.conn.execute("ALTER TABLE following_users ADD COLUMN follow_order INTEGER DEFAULT 0")
            except sqlite3.OperationalError:
                pass

            # T-0291: フォロー中かどうかを表す列。⚠️ 解除で行を消さないための列で、
            # 既存行は既定1（＝これまでどおりフォロー中）になる。行を消すと
            # is_favorite / is_zipped / last_downloaded（pixiv側に無く消えたら戻らない
            # 3列・CLAUDE.md §5）が失われるため、削除ではなくフラグで表す。
            try:
                self.conn.execute(
                    "ALTER TABLE following_users ADD COLUMN is_following INTEGER DEFAULT 1")
            except sqlite3.OperationalError:
                pass
            
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)

            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS author_settings (
                    user_id TEXT,
                    key TEXT,
                    value TEXT,
                    PRIMARY KEY (user_id, key)
                )
            """)

            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS failed_queue (
                    work_id TEXT PRIMARY KEY,
                    user_id TEXT,
                    title TEXT,
                    work_type TEXT,
                    error_reason TEXT,
                    failed_at TEXT,
                    retry_count INTEGER DEFAULT 0
                )
            """)

            # --- 発掘機能（学習タブ） ---
            # 方式B: 合成後の重みではなく「何回どう判定されたか」の生カウントだけを保存し、
            # 係数の解釈は読むときに行う（DISCOVERY_LEARNING_LOGIC_AGREEMENT_2026-08-06.md §7）。
            # 合計点を1つ持つ旧方式では、係数の再調整・Undo・Hate台帳のいずれも実現できない。
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS discovery_preference (
                    tag TEXT PRIMARY KEY,
                    weight REAL DEFAULT 0,
                    updated_at TEXT
                )
            """)
            # weight は旧方式（合計点1つ）の名残。判定ボタンからの書き込みが一度も配線されて
            # いなかったため中身は空で、移行で失うデータは無い。SQLiteの DROP COLUMN は
            # バージョン依存のため、残置して未使用とする（実装仕様①「移行」）。
            for _col, _decl in (
                ("display_tag", "TEXT"),           # 最後に観測した表記（正規化前）。表示用
                ("superlike_count", "INTEGER DEFAULT 0"),
                ("like_count", "INTEGER DEFAULT 0"),
                ("skip_count", "INTEGER DEFAULT 0"),
                ("hate_count", "INTEGER DEFAULT 0"),   # ジャンル理由＋理由なしのみ。絵柄理由は数えない
                # ⚠️ **幅（好み度）の計算には入っていない**（`JUDGMENT_WEIGHTS` に無い）。
                # 「何回出したか」は好き嫌いの証拠ではないため、意図的に外してある。
                # いま実際に使っているのは**傾向パネルの「表示◯」の表示だけ**
                # （discovery_ranking.trend_rows → gui の行）。
                # 将来 Hate台帳（合意書§6-2）の分母になる予定だが、台帳は未実装。
                ("shown_count", "INTEGER DEFAULT 0"),
                ("open_count", "INTEGER DEFAULT 0"),
                ("bookmark_count", "INTEGER DEFAULT 0"),
                ("seed_saved", "INTEGER DEFAULT 0"),       # 既存資産。判定カウントと混ぜない(§7-3)
                ("seed_bookmarked", "INTEGER DEFAULT 0"),
            ):
                try:
                    self.conn.execute(f"ALTER TABLE discovery_preference ADD COLUMN {_col} {_decl}")
                except sqlite3.OperationalError:
                    pass

            # 作者の軸。タグと同じ形に、フォロー中である事実（seed_following）を加えたもの。
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS discovery_author_preference (
                    author_id TEXT PRIMARY KEY,
                    display_name TEXT,
                    superlike_count INTEGER DEFAULT 0,
                    like_count INTEGER DEFAULT 0,
                    skip_count INTEGER DEFAULT 0,
                    hate_count INTEGER DEFAULT 0,
                    shown_count INTEGER DEFAULT 0,
                    open_count INTEGER DEFAULT 0,
                    bookmark_count INTEGER DEFAULT 0,
                    seed_saved INTEGER DEFAULT 0,
                    seed_bookmarked INTEGER DEFAULT 0,
                    seed_following INTEGER DEFAULT 0,
                    updated_at TEXT
                )
            """)
            # 「聖域」対策（実装仕様③-3-2）。フォロー中作者は見送りで作者軸が下がらないため、
            # 惰性で見続けている作者に気付く手がかりが他に無い。連続見送り回数が一定を超えたら
            # 「おすすめから外しますか？」と一度だけ提案する（下げるのではなく聞く）。
            # 第1段では回数の記録のみで、提案UIは後段。
            try:
                self.conn.execute(
                    "ALTER TABLE discovery_author_preference ADD COLUMN consecutive_skip_count INTEGER DEFAULT 0"
                )
            except sqlite3.OperationalError:
                pass

            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS discovery_swipe_history (
                    work_id TEXT PRIMARY KEY,
                    verdict TEXT,
                    judged_at TEXT,
                    source TEXT
                )
            """)
            # 判定1件が「何を加算したか」を復元できるようにするスナップショット。
            # これが無いとUndoが原理的に作れない（候補がメモリのキューから落ちた後、
            # どのタグを増やしたのか分からなくなる）。加えて、後からモデル由来のタグを
            # 除いた状態を履歴から再計算する経路にもなる（準備メモ§2-6補）。
            for _col, _decl in (
                ("reason", "TEXT"),        # "genre" | "style" | NULL(理由なし)
                ("tags", "TEXT"),          # 判定時点の正規化済みタグのJSON配列
                ("author_id", "TEXT"),
                ("opened", "INTEGER DEFAULT 0"),
                ("bookmarked", "INTEGER DEFAULT 0"),
                # #56: おすすめタブから明示的に取り下げたか（verdict列には触れない・
                # 「取り下げる」はこの列だけを立てる。学習そのものは残す）
                ("recommend_dismissed", "INTEGER DEFAULT 0"),
                # T-0099: 判定した瞬間の候補のサムネイルURL。discovery_view_history と
                # 同じ理由（振り返り時にAPIを叩かず済ませる）で、判定時点の値をそのまま持つ。
                ("thumbnail_url", "TEXT"),
            ):
                try:
                    self.conn.execute(f"ALTER TABLE discovery_swipe_history ADD COLUMN {_col} {_decl}")
                except sqlite3.OperationalError:
                    pass
            # 連続見送り回数を履歴から数え直すための索引（_recompute_consecutive_skips）
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_swipe_author_judged "
                "ON discovery_swipe_history (author_id, judged_at DESC)"
            )

            # --- 閲覧履歴（11-A・第一弾。docs/design/DISCOVERY_VIEW_HISTORY_SPEC_2026-08-15.md） ---
            #
            # 学習タブで「開いた」操作をした回数の生カウント（合意書§7）。
            # ⚠️ discovery_swipe_history.opened（判定時の+0.5加点フラグ・1回性）とは別物。
            # こちらは判定の有無・セッションをまたいで積み上がる回数そのもの。
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS discovery_view_history (
                    work_id TEXT PRIMARY KEY,
                    open_count INTEGER NOT NULL DEFAULT 0,
                    first_opened_at TEXT,
                    last_opened_at TEXT
                )
            """)
            # PO指示（2026-08-15・スコープ追加）: 履歴ダイアログにサムネイル表示を持たせるため、
            # 記録時点のcandidateが既に持つthumbnail_urlをそのまま保存する（追加API呼び出し不要）。
            try:
                self.conn.execute("ALTER TABLE discovery_view_history ADD COLUMN thumbnail_url TEXT")
            except sqlite3.OperationalError:
                pass

            # --- 一般母集団（世間との比較）: おすすめタブ合意書 §9-2-1・§9-2-2 ---
            #
            # 「このタグは自分の中で多いか」ではなく「**世間と比べて**多いか」を出すための分母。
            # 自分のライブラリを分母にすると自分の偏りがそのまま分母に入り、
            # ウマ娘を1,210件保存していれば『ウマ娘』が汎用タグに見えてしまう。
            #
            # ⚠️ **入れてよいのはランキング由来だけ。**
            # 検索結果・自分の閲覧・ブックマークは**入れない**——分母が自分と相関すると
            # リフト（自分の割合 ÷ 一般の割合）が1に近づき、何も見えなくなる。
            #
            # ⚠️ **R-18とそれ以外を別々に貯める**（audience 列）。混ぜたまま貯めると、
            # R-18を見る人と見ない人で同じ数字が違う意味になる。混ぜ方は読むときに
            # 純度フィルターへ従わせる（合意書§5-2）。分母の混合比につまみは置かない。
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS discovery_population (
                    tag TEXT NOT NULL,
                    audience TEXT NOT NULL,
                    doc_count INTEGER DEFAULT 0,
                    display_tag TEXT,
                    updated_at TEXT,
                    PRIMARY KEY (tag, audience)
                )
            """)
            # 分母そのもの（何作品を数えたか）と、状態表示用の最終更新日。
            # 「世間との比較: 2026-08-07 時点 ・ 作品 1,240 件から集計」を出すのに使う。
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS discovery_population_meta (
                    audience TEXT PRIMARY KEY,
                    work_count INTEGER DEFAULT 0,
                    updated_at TEXT
                )
            """)
            # ⚠️ **1作品は1回だけ数える。** 日次と週次には同じ作品が出るため、
            # これが無いと人気作だけが何度も加算されて分母が歪む。
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS discovery_population_seen (
                    work_id TEXT NOT NULL,
                    audience TEXT NOT NULL,
                    seen_at TEXT,
                    PRIMARY KEY (work_id, audience)
                )
            """)
            # 忘却（減衰）で古い順に間引くための索引
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_population_seen_at "
                "ON discovery_population_seen (audience, seen_at)"
            )

            # 【#60-A】おすすめタブ右クリックメニュー「出さない」の永続フラグ＋理由。
            # docs/design/CONTEXT_MENU_SPEC_2026-08-19.md §3-1。
            # discovery_swipe_history（学習タブの1枚ずつ判定）へは相乗りしない——
            # 判定履歴・Undo・傾向集計と区別できなくなるため（同spec §1-4）。
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS discovery_hidden_works (
                    work_id   TEXT PRIMARY KEY,
                    reason    TEXT,
                    author_id TEXT,
                    hidden_at TEXT
                )
            """)
            try:
                # 「出さない一覧」表示用（2026-08-20・docs/HIDDEN_WORKS_LIST_SPEC_2026-08-20.md）。
                # NULL許容——既存行（この列の追加より前に「出さない」した作品）は
                # タイトル無しのまま一覧に出る（get_hidden_works()側で表示用の
                # フォールバック文言を出す）。
                self.conn.execute("ALTER TABLE discovery_hidden_works ADD COLUMN title TEXT")
            except sqlite3.OperationalError:
                pass
            try:
                # 「出さない一覧」サムネイル表示用（2026-08-21・#60-A追加修正）。
                # title列と同じNULL許容パターン——既存行（この列の追加より前に
                # 「出さない」した作品）はサムネイル無しのまま一覧に出る
                # （_build_hidden_work_chip側でプレースホルダを出す）。
                self.conn.execute("ALTER TABLE discovery_hidden_works ADD COLUMN thumbnail_url TEXT")
            except sqlite3.OperationalError:
                pass

            # 「この作者を出さない」（2026-08-22・PO決定。discovery_hidden_worksの作者版）。
            # docs/design/RECOMMEND_HIDE_AUTHOR_TECH_SPEC_2026-08-22.md。
            # 作品単位のdiscovery_hidden_worksとは別テーブル——「作者を出さない」は
            # 「もっと見る登録済み作者」（discovery_boosted_authors）とは独立の概念で、
            # 表示除外のみ（登録テーブルは触らない・§2-3案A）。
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS discovery_hidden_authors (
                    author_id    TEXT PRIMARY KEY,
                    display_name TEXT,
                    reason       TEXT,
                    hidden_at    TEXT
                )
            """)

            # 【#60-A】「この作者をもっと見る」。discovery_author_preference（生カウント）へは
            # 書かない——観測ではなく明示的な指示であり、スワイプ由来のカウントと混ぜると
            # 後から分離できなくなる（CLAUDE.md §8 罠4）。docs/design/CONTEXT_MENU_SPEC_2026-08-19.md §3-2。
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS discovery_boosted_authors (
                    author_id    TEXT PRIMARY KEY,
                    display_name TEXT,
                    boosted_at   TEXT
                )
            """)

            # マイブックマークの永続化キャッシュ（#54⑥・2026-08-17・PO決定「PCの容量を活かした
            # 設計にしなければならない」）。worksは汚さず、pixiv側の生データをそのまま貯める
            # （解釈・除外フィルタは読み出し側で行う。discovery_populationと同じ思想）。
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS bookmark_remote_cache (
                    work_id TEXT PRIMARY KEY,
                    is_novel INTEGER NOT NULL DEFAULT 0,
                    title TEXT,
                    user_id TEXT,
                    user_name TEXT,
                    tags TEXT,
                    create_date TEXT,
                    thumb_url TEXT,
                    rest_type TEXT,
                    x_restrict INTEGER
                )
            """)
            try:
                self.conn.execute("ALTER TABLE bookmark_remote_cache ADD COLUMN x_restrict INTEGER")
            except sqlite3.OperationalError:
                pass

            # SuperLike欄のget_work_info結果キャッシュ（E案・2026-08-21・PO承認）。
            # 出どころ: docs/DISCOVERY_QUEUE_2026-08-19.md「速度調査」節。
            # recommend_tab.py:_load_superlikesがon_tab_openのたびに未ダウンロードの
            # SuperLike作品全件でget_work_info（1件ずつAPIコール・レート制限ゲートを
            # 通る）を叩き直していた（実測: タブ表示1回あたり12回）。works（本体テーブル）
            # は「未ダウンロードだけを対象にする」契約（_load_superlikes内の
            # local["is_deleted"]チェック）のため使えず、専用の軽量キャッシュを新設する。
            # ⚠️ 削除・改題の反映が遅れる問題があるため、fetched_atを持たせ、
            # 呼び出し側（recommend_tab.py）でTTL判定する（設計はコーダ裁量・契約どおり）。
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS superlike_info_cache (
                    work_id TEXT PRIMARY KEY,
                    title TEXT,
                    user_id TEXT,
                    user_name TEXT,
                    thumbnail_url TEXT,
                    fetched_at TEXT
                )
            """)

            # 2026-08-31・CONTRACT_2026-08-31_おすすめタブの初回表示を速くする.md
            # （裁定#91）: 節3(確実枠)・節4(boosted)の取得結果を起動後の初回へ持ち越す。
            # superlike_info_cacheと違いwork_id単位ではなく、節全体（タグ/作者ごとの
            # グルーピングを含む）を1個のJSONとして持ち越す
            # （docs/RECOMMEND_TAB_CARRYOVER_SPEC_2026-08-31.md「方針」節）。
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS recommend_section_carryover (
                    section TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    fetched_at TEXT NOT NULL
                )
            """)

            # --- 画像の埋め込み（フェーズM-1） ---
            #
            # ⚠️ **ファイルではなくDBへ入れる**（2026-08-07・アドバイザー決定）。
            # `.npz` を外に置くと、DBとファイルの2つを整合させることになり、
            # **「片方だけ消える」事故を作れる形**になる。12,327枚 × 1024次元 ×
            # 4byte ＝ 約50MB は SQLite にとって小さく、バックアップも1ファイルで済む。
            #
            # ⚠️⚠️ **`model` を主キーに含めること。** 埋め込みは**モデルごとに別の空間**で、
            # 混ぜると距離の意味が消える（`MODEL_SELECTION_2026-08-06.md` §6 が
            # 「1ページ目をEgaraNet内部から、2ページ目以降を単体DINOv3から取ると
            # 同じ空間の座標にならない恐れがある」と警告している論点そのもの）。
            # **キーに入れておけば、混ざったときに上書きではなく別行として現れる**ので、
            # 静かに壊れずに済む。
            #
            # ⚠️ **`kind` と `page` も分ける。** 絵柄は1ページ目だけでよいが、構図は
            # 全ページ要る（ページごとに構図は別物）。あとで構図を足すときに、
            # テーブルを作り直さずに入る形にしておく。
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS work_embeddings (
                    work_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    model TEXT NOT NULL,
                    page INTEGER NOT NULL DEFAULT 1,
                    dim INTEGER NOT NULL,
                    vec BLOB NOT NULL,
                    created_at TEXT,
                    PRIMARY KEY (work_id, kind, model, page)
                )
            """)
            # 「このモデルで、どの作品まで済んだか」を引くための索引。
            # ⚠️ 一括走査の**再開**がこれを使う（済んだ作品を飛ばす）。
            # 無いと再開のたびに全行を舐めることになる。
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_work_embeddings_model "
                "ON work_embeddings (kind, model, page)"
            )

            # --- ダウンロード時の推論（spec §5-3・§6-5）---------------------
            # ⚠️ 既存の表には1列も足さない。埋め込みの単位は
            # (work_id, kind, model, page) で、works に列を足すとモデルを
            # 差し替えたときに混ざる（docs/MODEL_SELECTION_2026-08-06.md §規定3）。
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS work_embed_failures (
                    work_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    model TEXT NOT NULL,
                    page INTEGER NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_reason TEXT,
                    updated_at TEXT,
                    PRIMARY KEY (work_id, kind, model, page)
                )
            """)
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_work_embed_failures_model "
                "ON work_embed_failures (kind, model, attempts)"
            )
            # 実ファイルの枚数。⚠️ kind / model を鍵に入れないのは、これが
            # 「ファイルの事実」でありモデルの事実ではないため（モデルを
            # 差し替えても枚数は変わらない）。
            # ⚠️ これが無いと、page_count が実ファイルより少ない作品で
            # 「取りこぼしたまま卒業」する（spec §6-5 の穴A）。
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS work_page_scan (
                    work_id TEXT PRIMARY KEY,
                    pages_found INTEGER NOT NULL,
                    updated_at TEXT
                )
            """)

            # --- 台帳（#12・10-10）------------------------------------------
            # docs/design/DUPLICATE_CLEANUP_SPEC_2026-08-15.md §4。
            # ⚠️ ダウンロード時には書かない（すぐ嘘になる・循環依存という旧設計の否決理由を避ける）。
            #    書くのは事後スキャン（tools/dedup.py）だけ。いつでも消して作り直せるキャッシュ。
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS file_ledger (
                    work_id TEXT NOT NULL,
                    page INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL,          -- 'present' | 'unsure' | 'missing'（core.py の LOCAL_COPY_* と同じ語彙）
                    store_path TEXT,
                    entry_name TEXT,
                    reason TEXT,
                    ambiguous_group TEXT,          -- まんげ型のとき (user_id, sanitize_filename(title)) の複合キー。
                                                    -- 同じ値を持つ複数work_idの行が同じ物理ファイルを指す
                    resolved_by TEXT,               -- NULL（自動判定） | 'manual'（10-11画面で人が確定）
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (work_id, page)
                )
            """)

            # --- ライブラリタブ（#51・作者フォルダ/ZIPの容量キャッシュ） --------
            # docs/design/LIBRARY_TAB_REDESIGN_SPEC_2026-08-16.md §2-5。
            # file_ledger と同じ「いつでも消して作り直せるキャッシュ」。毎回のタブ表示で
            # ファイルシステムを全スキャンしないための材料（作者数ぶんの os.walk は重い）。
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS library_capacity_cache (
                    user_id TEXT PRIMARY KEY,
                    total_bytes INTEGER NOT NULL,
                    store_path TEXT,
                    computed_at TEXT NOT NULL
                )
            """)

            # --- 重複整理の除外マーク（#47・quarantine） ---------------------
            # docs/design/DUPLICATE_DELETE_SPEC_2026-08-15.md §4。
            # ⚠️ file_ledger（上）とは性質が違うので混ぜない: file_ledger はスキャンのたびに
            #    rebuild_file_ledger が書き直す「いつでも消して作り直せるキャッシュ」だが、
            #    こちらは「ユーザーがquarantineへ意図的に移した」永久の記録——ゴミ箱を空にして
            #    物理ファイルが消えた後も残り続ける必要がある（消すと次回バックアップで復活する）。
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS quarantine_exclusions (
                    work_id TEXT NOT NULL,
                    page INTEGER NOT NULL DEFAULT 1,
                    quarantine_path TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    quarantined_at TEXT NOT NULL,
                    PRIMARY KEY (work_id, page)
                )
            """)

    # --- 画像の埋め込み（フェーズM-1）------------------------------------

    def save_work_embeddings(self, rows):
        """埋め込みをまとめて書く。`rows` は
        (work_id, kind, model, page, dim, vec_bytes) のリスト。

        ⚠️ **1件ずつではなくまとめて書くこと。** 一括走査は1万件以上を書くので、
        1件ごとにトランザクションを切ると、推論より書き込みのほうが重くなる。

        ⚠️ `vec_bytes` は **float32・L2正規化済み**のバイト列にすること。
        正規化を読むときに行うと、**読む側が複数になった時点で片方が忘れる**。
        """
        if not rows:
            return 0
        with self.lock, self.conn:
            self.conn.executemany(
                "INSERT OR REPLACE INTO work_embeddings "
                "(work_id, kind, model, page, dim, vec, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
                [(str(w), k, m, int(p), int(d), v) for w, k, m, p, d, v in rows],
            )
        return len(rows)

    def get_embedded_work_ids(self, kind, model, page=1):
        """そのモデルで既に埋め込みがある作品IDの集合。**再開のために使う。**"""
        with self.lock:
            cursor = self.conn.execute(
                "SELECT work_id FROM work_embeddings "
                "WHERE kind = ? AND model = ? AND page = ?",
                (kind, model, int(page)),
            )
            return {row[0] for row in cursor.fetchall()}

    def load_work_embeddings(self, kind, model, page=1):
        """(work_id, dim, vec_bytes) を順に返す。

        ⚠️ **numpy へは変換しない。** `database.py` は numpy に依存していないので、
        ここで持ち込むと本体アプリの起動に numpy が要ることになる。
        変換は使う側（走査ツール・学習軸）で行う。
        """
        with self.lock:
            cursor = self.conn.execute(
                "SELECT work_id, dim, vec FROM work_embeddings "
                "WHERE kind = ? AND model = ? AND page = ?",
                (kind, model, int(page)),
            )
            return [(row[0], row[1], row[2]) for row in cursor.fetchall()]

    def load_work_embeddings_all_pages(self, kind, model):
        """`load_work_embeddings` の全ページ版。`(work_id, page, dim, vec_bytes)` を返す。

        1ページ目だけを見る `load_work_embeddings` と違い、同じ `work_id` の
        別ページが**別の行**として並ぶ。似た絵の検索をページ単位へ広げるための口
        （`docs/design/LIBRARY_AUTHOR_DRILLDOWN_SPEC_2026-09-05.md` §4-0 の2）。

        並び順は `work_id`・`page` の昇順で固定する。呼ぶ側は行の位置で
        `(work_id, page)` を引くので、順序が実行ごとに変わると結果が揺れる。

        ⚠️ `load_work_embeddings` の既定（`page=1`）の挙動は変えない。
        ⚠️ numpy へは変換しない（理由は `load_work_embeddings` と同じ）。
        """
        with self.lock:
            cursor = self.conn.execute(
                "SELECT work_id, page, dim, vec FROM work_embeddings "
                "WHERE kind = ? AND model = ? ORDER BY work_id, page",
                (kind, model),
            )
            return [(row[0], row[1], row[2], row[3]) for row in cursor.fetchall()]

    def delete_work_embeddings(self, kind, model, page=None):
        """指定したモデルの埋め込みを消す。**走り直すときに使う。**

        ⚠️⚠️ **`kind` と `model` は必須にしてある。** 省けるようにすると、
        この口が「全部消す口」になる。走り直しで消したいのは**常に1モデルぶん**で、
        他のモデルの埋め込みまで巻き込む理由が無い。

        ⚠️ 手でSQLを書くのをやめるための口。7時間かけた結果を消す操作なので、
        **どこまで消えるかが引数だけで分かる形**にしてある。

        戻り値: 消した件数。
        """
        if not kind or not model:
            raise ValueError("kind と model は必須です（全件削除を防ぐため）")
        sql = "DELETE FROM work_embeddings WHERE kind = ? AND model = ?"
        params = [kind, model]
        if page is not None:
            sql += " AND page = ?"
            params.append(int(page))
        with self.lock, self.conn:
            cursor = self.conn.execute(sql, params)
            return cursor.rowcount

    def count_work_embeddings(self, kind=None, model=None):
        """埋め込みの件数と総容量（バイト）。サマリと状態表示に使う。"""
        sql = "SELECT COUNT(*), COALESCE(SUM(LENGTH(vec)), 0) FROM work_embeddings"
        params = []
        conds = []
        if kind:
            conds.append("kind = ?")
            params.append(kind)
        if model:
            conds.append("model = ?")
            params.append(model)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        with self.lock:
            row = self.conn.execute(sql, params).fetchone()
            return int(row[0]), int(row[1])

    # --- ライブラリタブ（#20・#51・docs/design/LIBRARY_TAB_REDESIGN_SPEC_2026-08-16.md） --

    def get_library_works(self):
        """ライブラリタブの一覧に使う全作品を返します（削除済みは除く）。

        work_meta（詳細APIの生JSON）を含める。サムネURL（`urls.small`等）が
        ここにしか無く、追加のAPI呼び出しをせずに済むため（ライブラリタブ仕様書§2-1）。
        last_backup は #51 の代表サムネイル選定・「保存日」ソートに使う。
        content_type は小説（'novel'）判定に使う（T-0188: 小説はwork_metaに
        サムネURLを持たず、代表作品のサムネイルが空になるため、表紙画像へ
        フォールバックする際の分岐に要る）。
        """
        with self.lock:
            cursor = self.conn.execute(
                "SELECT work_id, title, tags, user_id, create_date, x_restrict, "
                "bookmark_count, work_meta, last_backup, content_type, page_count "
                "FROM works WHERE is_deleted = 0"
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_bookmarked_library_works(self):
        """『マイブックマーク』サブビュー用（#52）: DL済み・ブックマーク済みの作品を返します。

        get_library_works と同じ列構成に is_bookmarked を加えたもの。
        """
        with self.lock:
            cursor = self.conn.execute(
                "SELECT work_id, title, tags, user_id, create_date, x_restrict, "
                "bookmark_count, work_meta, last_backup, content_type, is_bookmarked "
                "FROM works WHERE is_deleted = 0 AND is_bookmarked = 1"
            )
            return [dict(row) for row in cursor.fetchall()]

    def set_work_bookmarked(self, work_id, value=True):
        """work_id のブックマーク済みフラグを立てる/下ろす（#52）。

        ⚠️ 呼び出し元をマイブックマークからのDL確定時1経路に決め打ちしない
        （将来の#46踏み台APIバックフィル等、複数の入力経路から呼べる普通の関数にする）。
        work_id が works に存在しない（未DL）場合は何もしない（UPDATEが0行に当たるだけ）。
        """
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE works SET is_bookmarked = ? WHERE work_id = ?",
                (1 if value else 0, str(work_id)),
            )

    def get_bookmark_remote_cache(self):
        """マイブックマークの永続化キャッシュ全件を、_merge_bookmarksがそのまま食える形で返す
        （#54⑥・docs/design/LIBRARY_BOOKMARK_CACHE_SPEC_2026-08-17.md §3）。

        列名work_idを"id"キーへ、tags(JSON文字列)をリストへ変換する——
        _fetch_bookmarks_remote()が返す生データと同じ形にすることで、_merge_bookmarks
        （#52・変更してはいけない範囲）を一切変えずに両方の入力経路で使い回す。
        """
        with self.lock:
            cursor = self.conn.execute(
                "SELECT work_id, is_novel, title, user_id, user_name, tags, "
                "create_date, thumb_url, rest_type, x_restrict FROM bookmark_remote_cache"
            )
            rows = cursor.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["id"] = d.pop("work_id")
            d["is_novel"] = bool(d["is_novel"])
            try:
                d["tags"] = json.loads(d["tags"] or "[]")
            except Exception:
                d["tags"] = []
            result.append(d)
        return result

    def save_bookmark_remote_cache(self, items):
        """bookmark_remote_cacheを全洗い替えする（DELETE→INSERT・1トランザクション）。

        itemsは_fetch_bookmarks_remote()の戻り値（rest_type付与後）そのもの。
        ⚠️⚠️ 当初 `with self.lock, self.conn:` で書いていたが、この接続は
        `autocommit=True`（`:98`）で開いているため **`with self.conn:` はトランザクションを
        開始せず、文ごとに即コミットされる**（`_transaction()`のdocstring・`:1536`に明記済みの
        既知の罠）。DELETE直後に例外が起きてもDELETEはすでに確定してしまい、キャッシュが
        空になる——契約の「クラッシュ時に空にならない」を満たせていなかった（回帰テストで
        実際に再現・発見）。`self._transaction()`（明示的BEGIN/COMMIT・失敗時ROLLBACK）を使う。
        """
        with self._transaction() as conn:
            conn.execute("DELETE FROM bookmark_remote_cache")
            conn.executemany(
                "INSERT INTO bookmark_remote_cache "
                "(work_id, is_novel, title, user_id, user_name, tags, create_date, "
                "thumb_url, rest_type, x_restrict) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(str(it.get("id")), 1 if it.get("is_novel") else 0, it.get("title"),
                  str(it.get("user_id") or ""), it.get("user_name"),
                  json.dumps(it.get("tags") or []), it.get("create_date"),
                  it.get("thumb_url"), it.get("rest_type"), it.get("x_restrict"))
                 for it in items],
            )

    def get_superlike_info_cache_bulk(self, work_ids):
        """superlike_info_cacheから、渡されたwork_idぶんだけをまとめて引く（E案）。

        TTL判定はしない（fetched_atをそのまま返すだけ）——「新鮮かどうか」は
        呼び出し側（recommend_tab.py）が判断する。存在しないwork_idはキーごと
        結果に含まれない（呼び出し側でdict.get(...)がNoneになる形で扱う）。
        """
        ids = [str(w) for w in (work_ids or [])]
        if not ids:
            return {}
        with self.lock:
            placeholders = ",".join("?" for _ in ids)
            cursor = self.conn.execute(
                f"SELECT work_id, title, user_id, user_name, thumbnail_url, fetched_at "
                f"FROM superlike_info_cache WHERE work_id IN ({placeholders})",
                ids,
            )
            rows = cursor.fetchall()
        return {row["work_id"]: dict(row) for row in rows}

    def save_superlike_info_cache(self, work_id, title, user_id, user_name, thumbnail_url):
        """get_work_info成功時の結果を1件upsertする（E案）。fetched_atは呼び出し時刻。"""
        now = datetime.now().isoformat()
        with self.lock, self.conn:
            self.conn.execute(
                """
                INSERT INTO superlike_info_cache
                    (work_id, title, user_id, user_name, thumbnail_url, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(work_id) DO UPDATE SET
                    title = excluded.title,
                    user_id = excluded.user_id,
                    user_name = excluded.user_name,
                    thumbnail_url = excluded.thumbnail_url,
                    fetched_at = excluded.fetched_at
                """,
                (str(work_id), title, user_id, user_name, thumbnail_url, now),
            )

    def get_recommend_section_carryover(self, section):
        """おすすめタブの節全体（節3=確実枠／節4=boosted）の持ち越しを読む
        （CONTRACT_2026-08-31_おすすめタブの初回表示を速くする.md）。

        戻り値: (payload_list, fetched_at) のタプル。
        - 行が無い場合: (None, None) ——「まだ一度も取得していない」
        - 行があり中身が正しくJSONとして読める場合: (リスト, fetched_at文字列)
          ——空リスト([])でも「取得したが0件だった」として区別してそのまま返す
        - 行はあるがpayload_jsonが壊れていてjson.loadsに失敗した場合: (None, None)
          を返し、logger.exceptionへ記録する。⚠️ 例外を外へ出さない——「行が無い」と
          同じ扱いに倒すことで、呼び出し側は従来どおりAPIへ取りに行く（回復不能な
          状態を作らないほうを、DBの整合性より優先する）。
        """
        with self.lock:
            cursor = self.conn.execute(
                "SELECT payload_json, fetched_at FROM recommend_section_carryover WHERE section = ?",
                (section,),
            )
            row = cursor.fetchone()
        if row is None:
            return None, None
        try:
            payload_list = json.loads(row["payload_json"])
        except Exception:
            logger.exception(
                f"おすすめタブ持ち越し: section={section} のpayload_jsonが壊れているため、"
                "未取得扱いにします。"
            )
            return None, None
        return payload_list, row["fetched_at"]

    def save_recommend_section_carryover(self, section, payload_list):
        """おすすめタブの節全体の持ち越しをUPSERTする。fetched_atは呼び出し時刻。
        既存行は丸ごと置き換える（差分マージはしない・superlike_info_cacheと同じ
        「新しい結果で上書き」の考え方）。空リストでもそのまま保存する
        （＝「取得したが0件だった」という事実を持ち越すため）。"""
        now = datetime.now().isoformat()
        payload_json = json.dumps(payload_list)
        with self.lock, self.conn:
            self.conn.execute(
                """
                INSERT INTO recommend_section_carryover (section, payload_json, fetched_at)
                VALUES (?, ?, ?)
                ON CONFLICT(section) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    fetched_at = excluded.fetched_at
                """,
                (section, payload_json, now),
            )

    def get_view_counts_by_author(self):
        """discovery_view_history.open_count を works.user_id で合算して返します（#51②軸）。

        works に存在しない/削除済みの work_id は works との内部結合で自然に除外されます。
        """
        with self.lock:
            cursor = self.conn.execute("""
                SELECT w.user_id AS user_id, SUM(h.open_count) AS total
                FROM discovery_view_history h
                JOIN works w ON w.work_id = h.work_id
                WHERE w.is_deleted = 0
                GROUP BY w.user_id
            """)
            return {row["user_id"]: int(row["total"] or 0) for row in cursor.fetchall()}

    def get_capacity_cache(self):
        """作者フォルダ/ZIPの容量キャッシュを {user_id: {"total_bytes","store_path","computed_at"}} で返します。"""
        with self.lock:
            cursor = self.conn.execute(
                "SELECT user_id, total_bytes, store_path, computed_at FROM library_capacity_cache"
            )
            return {row["user_id"]: dict(row) for row in cursor.fetchall()}

    def save_capacity_cache(self, user_id, total_bytes, store_path):
        """1作者ぶんの容量計測結果を書き込みます（UPSERT）。"""
        now = datetime.now().isoformat()
        with self.lock, self.conn:
            self.conn.execute("""
                INSERT INTO library_capacity_cache (user_id, total_bytes, store_path, computed_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    total_bytes = excluded.total_bytes,
                    store_path = excluded.store_path,
                    computed_at = excluded.computed_at
            """, (str(user_id), int(total_bytes), store_path, now))

    def clear_capacity_cache(self, user_id):
        """1作者ぶんの容量キャッシュを消します（再計測ボタン用）。"""
        with self.lock, self.conn:
            self.conn.execute("DELETE FROM library_capacity_cache WHERE user_id = ?", (str(user_id),))

    # --- 台帳（#12・10-10）------------------------------------------------
    # docs/design/DUPLICATE_CLEANUP_SPEC_2026-08-15.md §4-3・§4-4。

    def rebuild_file_ledger(self, rows):
        """10-9/10-8のスキャン結果（`tools/dedup.py`）を `file_ledger` へ書く。

        `rows` は `{"work_id","page","status","store_path","entry_name","reason",
        "ambiguous_group"}` の辞書のリスト。この関数自身はファイルを読まない
        （判断は呼び出し側・書き込みだけをここに閉じる）。

        ⚠️ `resolved_by='manual'` の既存行は上書きしない（人が確定させた行を
        再スキャンで消さない）。
        ⚠️ `works` に存在しない work_id の行は書かない（呼び出し側の入力ミスとして無視）。
        掃除: `rows` に含まれない・かつ `works` にも無い work_id の孤立行を削除する
        （`works` の行が消えた work_id の台帳行は、再構築のたびに自然に消える）。

        戻り値: `{"written","protected_manual","skipped_not_in_works","deleted_orphan"}`。
        `written` は**実際に INSERT OR REPLACE した行数**（`len(rows)` そのものではない）。
        検算: `len(rows) == written + protected_manual + skipped_not_in_works` が常に成り立つ。

        冪等性: `INSERT OR REPLACE` は同じ主キー（work_id, page）に対して常に同じ最終状態に
        なる（manual保護を除けば、同じ `rows` を2回渡せば台帳の中身は1回目と完全一致する）。
        """
        with self.lock, self.conn:
            manual_keys = {
                (r["work_id"], r["page"]) for r in self.conn.execute(
                    "SELECT work_id, page FROM file_ledger WHERE resolved_by = 'manual'"
                ).fetchall()
            }
            work_ids = {
                r["work_id"] for r in self.conn.execute("SELECT work_id FROM works").fetchall()
            }

            to_write = []
            protected_manual = 0
            skipped_not_in_works = 0
            for r in rows:
                key = (str(r["work_id"]), int(r.get("page", 1)))
                if key in manual_keys:
                    protected_manual += 1
                    continue
                if key[0] not in work_ids:
                    skipped_not_in_works += 1
                    continue
                to_write.append((
                    key[0], key[1], r["status"], r.get("store_path"), r.get("entry_name"),
                    r.get("reason"), r.get("ambiguous_group"),
                ))

            if to_write:
                self.conn.executemany(
                    "INSERT OR REPLACE INTO file_ledger "
                    "(work_id, page, status, store_path, entry_name, reason, ambiguous_group, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
                    to_write,
                )

            cursor = self.conn.execute(
                "DELETE FROM file_ledger WHERE work_id NOT IN (SELECT work_id FROM works)"
            )
            deleted_orphan = cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0

        return {
            "written": len(to_write),
            "protected_manual": protected_manual,
            "skipped_not_in_works": skipped_not_in_works,
            "deleted_orphan": deleted_orphan,
        }

    def resolve_ledger_entry(self, work_id, page, store_path, entry_name):
        """10-11画面で人が「これはこの作品のファイルです」と選んだときだけ呼ぶ。

        `status='present', resolved_by='manual', updated_at=now` を1行だけ書く。
        ⚠️ **同じ `ambiguous_group` の他の work_id 行には触れない**（他の作品が実際に
        ファイルを持っているかは、この操作からは分からないため）。他の行を自動的に
        `missing` にしない——次回の `rebuild_file_ledger` が実ファイルを見て正しい状態を出す。

        ⚠️ `reason`/`ambiguous_group` は、既存行（直近の `rebuild_file_ledger` が書いたもの）
        から引き継ぐ。10-11画面のグループ除外クエリ（§5-2）が `ambiguous_group` を頼りに
        するため、ここで失うと除外が効かなくなる。
        """
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO file_ledger "
                "(work_id, page, status, store_path, entry_name, reason, ambiguous_group, "
                " resolved_by, updated_at) "
                "VALUES (?, ?, 'present', ?, ?, "
                "  (SELECT reason FROM file_ledger WHERE work_id = ? AND page = ?), "
                "  (SELECT ambiguous_group FROM file_ledger WHERE work_id = ? AND page = ?), "
                "  'manual', datetime('now'))",
                (str(work_id), int(page), store_path, entry_name,
                 str(work_id), int(page), str(work_id), int(page)),
            )

    def get_undecidable_groups(self):
        """10-11画面セクションAの一覧（まんげ型・仕様§5-2）。

        `resolved_by='manual'` が1件でも入った `ambiguous_group` はグループごと除外する
        （読み出し時にだけ畳む。`rebuild_file_ledger`・`resolve_ledger_entry` 自体は変更しない）。

        戻り値: [{"ambiguous_group": str, "reason": str,
                  "candidates": [{"work_id": str, "title": str, "author_name": str,
                                  "user_id": str,
                                  "pages": [{"page": int, "store_path": str, "entry_name": str}, ...]},
                                 ...]}, ...]
        """
        with self.lock:
            rows = self.conn.execute(
                "SELECT fl.work_id AS work_id, fl.page AS page, fl.store_path AS store_path, "
                "       fl.entry_name AS entry_name, fl.reason AS reason, "
                "       fl.ambiguous_group AS ambiguous_group, "
                "       w.title AS title, w.user_id AS user_id, "
                "       COALESCE(f.name, '') AS author_name "
                "FROM file_ledger fl "
                "JOIN works w ON w.work_id = fl.work_id "
                "LEFT JOIN following_users f ON f.user_id = w.user_id "
                "WHERE fl.status = 'unsure' AND fl.ambiguous_group IS NOT NULL "
                "  AND fl.ambiguous_group NOT IN ( "
                "      SELECT DISTINCT ambiguous_group FROM file_ledger "
                "      WHERE resolved_by = 'manual' AND ambiguous_group IS NOT NULL "
                "  ) "
                "ORDER BY fl.ambiguous_group, fl.work_id, fl.page"
            ).fetchall()

        groups = {}
        for r in rows:
            g = groups.setdefault(r["ambiguous_group"], {
                "ambiguous_group": r["ambiguous_group"], "reason": r["reason"], "candidates": {},
            })
            c = g["candidates"].setdefault(r["work_id"], {
                "work_id": r["work_id"], "title": r["title"], "author_name": r["author_name"],
                "user_id": r["user_id"], "pages": [],
            })
            c["pages"].append({"page": r["page"], "store_path": r["store_path"],
                                "entry_name": r["entry_name"]})

        out = []
        for g in groups.values():
            g["candidates"] = list(g["candidates"].values())
            out.append(g)
        return out

    # --- 重複整理の除外マーク（#47・quarantine） -------------------------
    # docs/design/DUPLICATE_DELETE_SPEC_2026-08-15.md §4。

    def mark_quarantine_excluded(self, work_id, page, quarantine_path,
                                  reason="near_duplicate_manual"):
        """ユーザーが重複整理でこの(work_id, page)をquarantineへ移したことを記録する。

        ⚠️ 物理ファイルの有無とは独立に永久に残る記録。`file_ledger`のような
        スキャンでの作り直し対象ではない（§4-1）。
        """
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO quarantine_exclusions "
                "(work_id, page, quarantine_path, reason, quarantined_at) "
                "VALUES (?, ?, ?, ?, datetime('now'))",
                (str(work_id), int(page), quarantine_path, reason),
            )

    def is_quarantine_excluded(self, work_id, page=None):
        """`page`指定ありならその1ページだけ、`None`ならその work_id のどれか1ページでも
        除外済みなら True。"""
        with self.lock:
            if page is None:
                row = self.conn.execute(
                    "SELECT 1 FROM quarantine_exclusions WHERE work_id = ? LIMIT 1",
                    (str(work_id),),
                ).fetchone()
            else:
                row = self.conn.execute(
                    "SELECT 1 FROM quarantine_exclusions WHERE work_id = ? AND page = ?",
                    (str(work_id), int(page)),
                ).fetchone()
            return row is not None

    def is_fully_quarantine_excluded(self, work_id, page_count):
        """その作品が期待する全ページ（1〜page_count）が、すべて除外済みかどうか。

        `core.py`の再取得抑止（`judge_local_copy`の呼び出し側）はこちらを使う。
        `is_quarantine_excluded`自体は「どれか1ページでも」の意味のまま変えない
        （§4-3・アドバイザーレビュー指摘: 1ページだけの除外で他の本当に欠けているページまで
        再取得を止めてしまうのを防ぐ）。

        ⚠️⚠️ `page_count < 1` をガードする（実測役指摘）: そうしないと `range(1, page_count+1)`
        が空になり、`all([]) == True` で「除外レコードが1件も無いのに全ページ除外済み」という
        誤判定になる。
        """
        if page_count < 1:
            return False
        return all(self.is_quarantine_excluded(work_id, p) for p in range(1, page_count + 1))

    def list_quarantine_excluded(self):
        """「ごみ箱を空にする」画面（§6）が使う。quarantine_path・quarantined_atを含む。"""
        with self.lock:
            rows = self.conn.execute(
                "SELECT work_id, page, quarantine_path, reason, quarantined_at "
                "FROM quarantine_exclusions ORDER BY quarantined_at"
            ).fetchall()
            return [dict(r) for r in rows]

    # --- ダウンロード時の推論: 失敗の記録とページ数（spec §5-3・§6・§6-5）------
    #
    # ⚠️ この一群は「まだ誰も呼ばない」段（第1段）。呼び出しは第2段以降で足す。

    # 「済んだページ」と「諦めたページ」の**和集合**を数える部分。
    # ⚠️⚠️ UNION であって UNION ALL ではない。あるページが3回失敗して gave_up に
    # なったあと、バッチ道具（tools/scan_style_embeddings.py）がそれを成功させると、
    # 道具は failures 表を知らないので行が残り、同じページが両方に在る状態になる。
    # 足し算で数えると、その1ページを2つと数えて page_count に届き、
    # **まだ手付かずのページが残っているのに作品が対象から外れる**。
    _EMBED_DONE_OR_GAVEUP = """
        SELECT work_id, COUNT(*) AS n FROM (
            SELECT work_id, page FROM work_embeddings
             WHERE kind = ? AND model = ?
            UNION
            SELECT work_id, page FROM work_embed_failures
             WHERE kind = ? AND model = ? AND attempts >= ?
        ) GROUP BY work_id
    """

    # 1作品に必要なページ数。
    # ⚠️ NULLIF(page_count, 0) … page_count が 0 の作品は COALESCE では拾えず、
    #    「0 < 0 は偽」で**一度も試されないまま永久に対象外**になる（痕跡も残らない）。
    # ⚠️ work_page_scan との MAX … page_count が実ファイルより少ない作品で、
    #    途中まで済んだところで分母に到達し、**取りこぼしたまま卒業**するのを防ぐ。
    _EMBED_EXPECTED_PAGES = ("MAX(COALESCE(NULLIF(w.page_count, 0), 1), "
                             "COALESCE(s.pages_found, 0))")

    def next_work_needing_embedding(self, kind, model,
                                    max_attempts=EMBED_MAX_ATTEMPTS,
                                    content_type='illust'):
        """まだ埋め込みが足りない作品を1件返す（無ければ None）。

        戻り値: (work_id, 必要なページ数)

        ⚠️ **引く単位はページではなく作品。** ページを持つ表は無く（works.page_count
        という数だけ）、実際のページ番号はファイルから決まるため、
        「まだ推論していないページ」はSQLだけでは列挙できない。
        ページの列挙は、呼ぶ側がファイルを解決してから行うこと（spec §6-5）。

        ⚠️ 小説を外すのは必須。画像が無いので必ず失敗し、3回で「諦めた」に入って
        画面の件数が小説で埋まる（メタデータ側も同じ絞り方をしている）。
        """
        sql = f"""
            SELECT w.work_id AS work_id, {self._EMBED_EXPECTED_PAGES} AS expected
              FROM works w
              LEFT JOIN ({self._EMBED_DONE_OR_GAVEUP}) d ON d.work_id = w.work_id
              LEFT JOIN work_page_scan s ON s.work_id = w.work_id
             WHERE w.content_type = ? AND w.is_deleted = 0
               AND COALESCE(d.n, 0) < {self._EMBED_EXPECTED_PAGES}
             ORDER BY w.work_id
             LIMIT 1
        """
        with self.lock:
            row = self.conn.execute(
                sql, (kind, model, kind, model, int(max_attempts), content_type)).fetchone()
        return (row["work_id"], int(row["expected"])) if row else None

    def count_embed_status(self, kind, model,
                           max_attempts=EMBED_MAX_ATTEMPTS, content_type='illust'):
        """{'total':M, 'done':N, 'gave_up':Q, 'remaining':P} を返す（画面用・**ページ単位**）。

        ⚠️ 4つとも「ページ」で数える。作品数と混ぜない（混ぜると足し引きが合わない）。
        ⚠️ gave_up は失敗行をそのまま数えない。バッチ道具が後から成功させても
          failures の行は消えないので、**いま埋め込みが在るページを除く**。
          除かないと、実際には完了しているのに「諦めた」だけが減らない。
        """
        with self.lock:
            row = self.conn.execute(f"""
                SELECT
                  (SELECT COALESCE(SUM({self._EMBED_EXPECTED_PAGES}), 0)
                     FROM works w LEFT JOIN work_page_scan s ON s.work_id = w.work_id
                    WHERE w.content_type = ? AND w.is_deleted = 0) AS total,
                  (SELECT COUNT(*) FROM work_embeddings e
                     JOIN works w ON w.work_id = e.work_id
                    WHERE e.kind = ? AND e.model = ?
                      AND w.content_type = ? AND w.is_deleted = 0) AS done,
                  (SELECT COUNT(*) FROM work_embed_failures f
                     JOIN works w ON w.work_id = f.work_id
                    WHERE f.kind = ? AND f.model = ? AND f.attempts >= ?
                      AND w.content_type = ? AND w.is_deleted = 0
                      AND NOT EXISTS (SELECT 1 FROM work_embeddings e
                                       WHERE e.work_id = f.work_id AND e.kind = f.kind
                                         AND e.model = f.model AND e.page = f.page)) AS gave_up
            """, (content_type, kind, model, content_type,
                  kind, model, int(max_attempts), content_type)).fetchone()
        total, done, gave_up = int(row["total"]), int(row["done"]), int(row["gave_up"])
        return {"total": total, "done": done, "gave_up": gave_up,
                "remaining": max(0, total - done - gave_up)}

    def increment_embed_attempts(self, work_id, kind, model, page, reason=None):
        """「そのページの問題」で推論できなかったときだけ +1 する。

        ⚠️ 一時的な障害では呼ばないこと（モデルが読めない・DBの書き込み失敗・
          ZIPの鍵が取れない・未知の例外）。巻き添えで増やすと、作品自体は
          何ともないのに永久に除外される（database.py:increment_meta_attempts と同じ判断）。
        """
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO work_embed_failures"
                " (work_id, kind, model, page, attempts, last_reason, updated_at)"
                " VALUES (?, ?, ?, ?, 1, ?, datetime('now'))"
                " ON CONFLICT(work_id, kind, model, page) DO UPDATE SET"
                "   attempts = attempts + 1,"
                "   last_reason = excluded.last_reason,"
                "   updated_at = excluded.updated_at",
                (str(work_id), kind, model, int(page), reason))

    def clear_embed_failure(self, work_id, kind, model, page):
        """成功したら失敗の記録を消す（core.py の remove_failed_job と同じ形）。

        ⚠️ 消さないと、3回失敗 → 後から成功 → モデルの走り直しで埋め込みだけ消える、
          という順で**その作品だけ静かに永久欠落**する（spec §5-3）。
        """
        with self.lock, self.conn:
            cur = self.conn.execute(
                "DELETE FROM work_embed_failures"
                " WHERE work_id = ? AND kind = ? AND model = ? AND page = ?",
                (str(work_id), kind, model, int(page)))
            return cur.rowcount

    def clear_embed_failures_for_work(self, work_id):
        """その作品の失敗記録を全部消す（**落とし直したときに呼ぶ**）。

        ⚠️ ファイルが入れ替わったので、前の「3回失敗」はもう根拠が無い。
          消さないと、品質チェックに落ちた作品が落とし直しの前に試行枠を使い切り、
          直ったのに対象へ戻らない経路が残る（2026-08-12・実測役の指摘4）。
        """
        with self.lock, self.conn:
            cur = self.conn.execute(
                "DELETE FROM work_embed_failures WHERE work_id = ?", (str(work_id),))
            return cur.rowcount

    def reset_embed_attempts(self, kind, model, max_attempts=EMBED_MAX_ATTEMPTS):
        """諦めたぶんを対象へ戻す（画面のボタン）。自動では呼ばないこと。"""
        with self.lock, self.conn:
            cur = self.conn.execute(
                "DELETE FROM work_embed_failures"
                " WHERE kind = ? AND model = ? AND attempts >= ?",
                (kind, model, int(max_attempts)))
            return cur.rowcount

    def upsert_page_scan(self, work_id, pages_found):
        """その作品で実際に見つかったページ枚数を記録する。"""
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO work_page_scan (work_id, pages_found, updated_at)"
                " VALUES (?, ?, datetime('now'))"
                " ON CONFLICT(work_id) DO UPDATE SET"
                "   pages_found = excluded.pages_found,"
                "   updated_at = excluded.updated_at",
                (str(work_id), int(pages_found)))

    def clear_page_scan(self, work_id=None):
        """数え直しの対象へ戻す（行を消すだけ。**埋め込みは消さない**）。

        work_id を省くと全件。⚠️ 落とし直した作品はファイルが増えている可能性が
        あるので、その作品ぶんを消す（消さないと枚数が古いまま凍る・spec §6-5）。
        """
        with self.lock, self.conn:
            if work_id is None:
                cur = self.conn.execute("DELETE FROM work_page_scan")
            else:
                cur = self.conn.execute(
                    "DELETE FROM work_page_scan WHERE work_id = ?", (str(work_id),))
            return cur.rowcount

    def get_user_work_ids(self, user_id, content_types=None):
        """指定したユーザーの作品IDを取得します。

        content_types にリストを渡すと、その content_type（'illust'/'novel'）に
        限定して取得します。省略時は従来通り全タイプを対象にします。
        """
        with self.lock:
            if content_types:
                placeholders = ",".join("?" for _ in content_types)
                cursor = self.conn.execute(
                    f"SELECT work_id FROM works WHERE CAST(user_id AS TEXT) = ? AND content_type IN ({placeholders})",
                    (str(user_id), *content_types)
                )
            else:
                cursor = self.conn.execute("SELECT work_id FROM works WHERE CAST(user_id AS TEXT) = ?", (str(user_id),))
            return {row['work_id'] for row in cursor.fetchall()}

    def get_work(self, work_id):
        """指定した作品IDのレコードを取得します"""
        with self.lock:
            cursor = self.conn.execute("SELECT * FROM works WHERE work_id = ?", (work_id,))
            return cursor.fetchone()

    def mark_as_deleted(self, work_id):
        """作品がPixivから削除されたことを記録します"""
        with self.lock, self.conn:
            self.conn.execute("UPDATE works SET is_deleted = 1 WHERE work_id = ?", (work_id,))

    def increment_missing_streak(self, work_id):
        """作品が今回の一覧に見つからなかった回数を1増やし、増やした後の値を返します。"""
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE works SET missing_streak = COALESCE(missing_streak, 0) + 1 WHERE work_id = ?",
                (work_id,)
            )
            row = self.conn.execute(
                "SELECT missing_streak FROM works WHERE work_id = ?", (work_id,)
            ).fetchone()
            return row['missing_streak'] if row else 0

    def reset_missing_streak_for_found(self, user_id, found_work_ids):
        """今回の一覧に見つかった作品のうち、連続欠落カウントが残っているものを0へ戻します。"""
        if not found_work_ids:
            return
        with self.lock:
            stale_ids = {
                row['work_id'] for row in self.conn.execute(
                    "SELECT work_id FROM works WHERE CAST(user_id AS TEXT) = ? AND missing_streak > 0",
                    (str(user_id),)
                )
            }
        to_reset = stale_ids & set(found_work_ids)
        if not to_reset:
            return
        placeholders = ",".join("?" for _ in to_reset)
        with self.lock, self.conn:
            self.conn.execute(
                f"UPDATE works SET missing_streak = 0 WHERE work_id IN ({placeholders})",
                tuple(to_reset)
            )

    def delete_work(self, work_id):
        """作品IDのレコードをDBから完全に削除します（物理削除時の再DLを促すため）"""
        with self.lock, self.conn:
            self.conn.execute("DELETE FROM works WHERE work_id = ?", (work_id,))

    def upsert_work(self, work_id, user_id, title, page_count, create_date, update_date, content_type='illust', tags=None, meta=None):
        """作品情報を新規登録、または更新します

        tags は文字列のリストを渡すとJSON文字列として保存します。省略時（None）は
        既存のtags列の値を変更しません（呼び出し元がまだタグを持っていない経路のため）。

        meta は core.extract_work_meta() の戻り値。省略時（None）はメタ列を1つも
        触らない。これが「経路によってメタが消える」を防ぐ根拠で、A経路が meta なしで
        upsert しても B経路が入れた bookmark_count は残る
        （docs/WORK_METADATA_SPEC_2026-08-10.md §3-2）。
        """
        now = datetime.now().isoformat()
        w_id = str(work_id) if work_id is not None else None
        u_id = str(user_id) if user_id is not None else None
        tags_json = json.dumps(tags, ensure_ascii=False) if tags is not None else None

        cols = ["work_id", "user_id", "title", "page_count", "create_date",
                "update_date", "last_backup", "content_type", "tags"]
        vals = [w_id, u_id, title, page_count, create_date, update_date, now, content_type, tags_json]
        sets = ["user_id = excluded.user_id",
                "title = excluded.title",
                "page_count = excluded.page_count",
                "create_date = excluded.create_date",
                "update_date = excluded.update_date",
                "last_backup = excluded.last_backup",
                "is_deleted = 0",
                "content_type = excluded.content_type",
                "tags = COALESCE(excluded.tags, works.tags)"]

        if meta is not None:
            merged = self._meta_values_for_write(w_id, meta, now)
            for col in META_COLUMNS:
                cols.append(col)
                vals.append(merged.get(col))
                # ⚠️ excluded.列名 をそのまま書かないこと。渡されなかった列（そのAPIが
                #    持っていない項目）が NULL で潰れ、後から取ったメタが消える。
                sets.append(f"{col} = COALESCE(excluded.{col}, works.{col})")

        placeholders = ", ".join("?" for _ in cols)
        sql = (f"INSERT INTO works ({', '.join(cols)}, is_deleted) "
               f"VALUES ({placeholders}, 0) "
               f"ON CONFLICT(work_id) DO UPDATE SET {', '.join(sets)}")
        with self.lock, self.conn:
            self.conn.execute(sql, vals)

    def _meta_values_for_write(self, work_id, meta, now):
        """メタ列へ書く値を組み立てる。work_meta は既存と部分マージする（§4-1）。

        ⚠️ A経路の description は空文字で返る（詳細APIでは本文が入っている）。
          単純に入れ替えると、後から走ったA経路が本文を消す。
        """
        out = {k: meta.get(k) for k in META_COLUMNS if k != "work_meta"}
        out["meta_fetched_at"] = now
        old_json = None
        with self.lock:
            row = self.conn.execute(
                "SELECT work_meta FROM works WHERE work_id = ?", (work_id,)).fetchone()
            if row is not None:
                old_json = row["work_meta"]
        merged = merge_work_meta(old_json, meta.get("work_meta"))
        out["work_meta"] = json.dumps(merged, ensure_ascii=False) if merged else None
        return out

    def update_work_meta(self, work_id, meta):
        """既存の作品の**メタ列だけ**を更新します（後埋め用）。

        ⚠️⚠️ works の本体（title / create_date / update_date / page_count / tags /
          is_deleted）には一切触らない。後埋めが使う作品詳細APIには updateDate が無く、
          createDate も一覧APIと表記が違う（UTC / JST）ため、本体を書くと壊れる
          （docs/WORK_METADATA_SPEC_2026-08-10.md §10）。

        存在しない work_id には何もしない（新規行を作らない）。
        """
        w_id = str(work_id)
        now = datetime.now().isoformat()
        values = self._meta_values_for_write(w_id, meta or {}, now)
        # NULL で既存を潰さないよう COALESCE で書く（そのAPIが持っていない項目のため）。
        sets = ", ".join(f"{c} = COALESCE(?, {c})" for c in META_COLUMNS)
        params = [values.get(c) for c in META_COLUMNS] + [w_id]
        with self.lock, self.conn:
            cur = self.conn.execute(f"UPDATE works SET {sets} WHERE work_id = ?", params)
            return cur.rowcount

    def get_works_needing_meta(self, max_attempts=3, content_type='illust'):
        """メタの後埋めが要る作品を [(work_id, user_id), ...] で返します。

        meta_fetched_at ではなく bookmark_count で見る。A経路で埋めた作品は
        meta_fetched_at が入るのに bookmark_count は NULL のままになるため
        （＝ 埋まっているが「世間の人気順」に使えない）。

        呼び出し側はこの一覧を1回だけ引いて固定し、1件ごとに引き直さないこと。
        引き直すと、取れなかった作品が毎回また先頭に戻り、その後ろが永久に
        処理されない（spec §6-1-2）。
        """
        with self.lock:
            rows = self.conn.execute(
                "SELECT work_id, user_id FROM works"
                " WHERE content_type = ? AND is_deleted = 0"
                "   AND bookmark_count IS NULL AND meta_attempts < ?"
                " ORDER BY meta_attempts, user_id, work_id",
                (content_type, int(max_attempts))).fetchall()
        return [(r["work_id"], r["user_id"]) for r in rows]

    def increment_meta_attempts(self, work_id):
        """「その作品の問題」で取れなかったときだけ +1 する（spec §6-9）。

        ⚠️ 一時的な障害（429・5xx・通信断・未知の例外）では呼ばないこと。
          巻き添えで増やすと、作品自体は何ともないのに永久に除外される。
        """
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE works SET meta_attempts = COALESCE(meta_attempts, 0) + 1"
                " WHERE work_id = ?", (str(work_id),))

    def count_meta_status(self, max_attempts=3, content_type='illust'):
        """{'filled':N, 'pending':N, 'gave_up':N} を返します（画面の進捗表示用）。"""
        with self.lock:
            row = self.conn.execute(
                "SELECT"
                "  SUM(CASE WHEN bookmark_count IS NOT NULL THEN 1 ELSE 0 END) AS filled,"
                "  SUM(CASE WHEN bookmark_count IS NULL AND meta_attempts <  ? THEN 1 ELSE 0 END) AS pending,"
                "  SUM(CASE WHEN bookmark_count IS NULL AND meta_attempts >= ? THEN 1 ELSE 0 END) AS gave_up"
                " FROM works WHERE content_type = ? AND is_deleted = 0",
                (int(max_attempts), int(max_attempts), content_type)).fetchone()
        return {k: (row[k] or 0) for k in ("filled", "pending", "gave_up")}

    def reset_meta_attempts(self, max_attempts=3):
        """打ち切った作品をもう一度対象に戻します（画面の「もう一度試す」）。

        自動では呼ばないこと。自動で戻すと、取れない作品を永久に叩き続ける形へ逆戻りする。
        """
        with self.lock, self.conn:
            cur = self.conn.execute(
                "UPDATE works SET meta_attempts = 0 WHERE meta_attempts >= ?",
                (int(max_attempts),))
            return cur.rowcount

    def set_work_tags(self, work_id, tags):
        """既存の作品レコードの tags 列だけを更新します。

        upsert_work() はタイトル・ページ数等をまとめて書き換えるため、
        「既にある作品にタグだけ後から補完する」用途（tools/backfill_work_tags.py）には
        使えない。存在しない work_id に対しては何もしない（新規行を作らない）。
        """
        tags_json = json.dumps(tags or [], ensure_ascii=False)
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE works SET tags = ? WHERE work_id = ?", (tags_json, str(work_id))
            )

    def touch_last_backup(self, work_id, user_id=None, title=None, content_type='illust'):
        """`works.last_backup` **だけ**を今の時刻にする（行が無ければ最小の行を作る）。

        画集ビューアの「個別DL」（1ページだけ保存する・`artbook_viewer.py`）専用。
        「直近のバックアップ」タブは `works.last_backup IS NOT NULL` を作者ごとに
        集計しているだけ（`gui.py:3964-3972`）なので、一覧へ出すのに必要なのは
        `last_backup` と `user_id` の2つだけ。

        ⚠️⚠️ ここで `upsert_work()` を使ってはいけない（2026-08-22・調査で確認）。
        あちらは ON CONFLICT で `page_count` / `create_date` / `update_date` を
        **無条件に上書き**する（:1321-1329）が、個別DLの時点では次のとおり値が無い:
          - `create_date` … `/ajax/illust/{id}/pages` にも `ugoira_meta` にも無い
          - `update_date` … 作品詳細の応答にそもそも入っていない（`pixiv_client.py:860-861`）
          - `page_count`  … うごイラでは分からない
        空で潰すと、その空が今度は「一致」として効いてしまう——
        `core.py:1822-1823`（単一作品DLの省略判定）と `core.py:1127-1130`
        （差分取得の早期打ち切り）はどちらも `update_date` の一致だけを見るため、
        あとから「作品まるごとDL」しても**無音でスキップ**され、1ページしか
        持っていないのに持っているように見える状態が残る。
        だから触るのは `last_backup` 1列だけにしてある（`set_work_tags()` と同じ形）。

        行が無いときだけ `user_id` / `title` / `content_type` も入れる（新規行では
        潰す既存値が無いため）。⚠️ 既にある行の `user_id` / `title` は上書きしない
        ——本流が入れた正しい値のほうが確かなので。
        `create_date` / `update_date` / `page_count` は NULL のまま残す（NULL は
        上記2つの一致判定のどちらにも一致しないので、後から本流が正しい値で
        `upsert_work()` すれば素直に埋まる）。
        `is_deleted` は列の既定（0）に任せ、既存行では**触らない**
        （`upsert_work()` が無条件に 0 へ戻すのとは違う。ここは復活の判断をしない）。
        """
        now = datetime.now().isoformat()
        w_id = str(work_id) if work_id is not None else None
        u_id = str(user_id) if user_id is not None else None
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO works (work_id, user_id, title, content_type, last_backup) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(work_id) DO UPDATE SET last_backup = excluded.last_backup",
                (w_id, u_id, title, content_type, now)
            )
        return now

    def get_works_missing_tags(self, content_types=('illust',), include_deleted=False):
        """tags がまだ埋まっていない作品を {user_id: [work_id, ...]} で返します。

        バックフィルの対象抽出用。作者ごとにまとめるのは、pixivの詳細取得APIが
        「作者ID＋作品IDの配列」を単位にしているため（PixivClient.get_works_by_ids）。
        """
        sql = "SELECT user_id, work_id FROM works WHERE (tags IS NULL OR tags = '')"
        params = []
        if content_types:
            sql += " AND content_type IN (%s)" % ",".join("?" for _ in content_types)
            params.extend(content_types)
        if not include_deleted:
            sql += " AND is_deleted = 0"
        sql += " AND user_id IS NOT NULL AND user_id != ''"
        grouped = {}
        with self.lock:
            for row in self.conn.execute(sql, params).fetchall():
                grouped.setdefault(str(row['user_id']), []).append(str(row['work_id']))
        return grouped

    def save_following_users(self, users_list):
        """フォローしているユーザー一覧をデータベースに保存/更新します

        users_list の並び順は Pixiv 側のフォロー中一覧の並び（フォロー登録順、新しい順）
        をそのまま反映しているため、そのインデックスを follow_order として保存し、
        「フォロー登録順」ソートに利用します。
        """
        # 2026-08-27: `with self.lock, self.conn:` は接続がautocommitのため実際には
        # トランザクションを開始しない（_transaction()のdocstring参照）。ループ途中で
        # 失敗すると一部のユーザーだけ更新された状態が残るため、_transaction()経由にする。
        with self._transaction() as conn:
            for order, user in enumerate(users_list):
                conn.execute("""
                    INSERT INTO following_users (user_id, name, account, profile_img, last_downloaded, follow_order)
                    VALUES (?, ?, ?, ?, (SELECT last_downloaded FROM following_users WHERE user_id = ?), ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        name = excluded.name,
                        account = excluded.account,
                        profile_img = excluded.profile_img,
                        follow_order = excluded.follow_order
                """, (user['user_id'], user['name'], user['account'], user['profile_img'], user['user_id'], order))

    def count_works_by_author(self):
        """フォロー中作者ごとの保存済み作品数 {user_id: 件数}（is_deleted=0のみ）。

        #41 (6) 統合ダウンロード画面・作者一覧の「作品数」列で使う（読み取り専用の集計）。
        """
        with self.lock:
            cursor = self.conn.execute(
                "SELECT user_id, COUNT(*) AS n FROM works "
                "WHERE is_deleted = 0 AND user_id IS NOT NULL GROUP BY user_id")
            return {row["user_id"]: row["n"] for row in cursor.fetchall()}

    def get_following_users(self, sort_by="follow_order", sort_order="asc"):
        """保存されているフォローユーザー一覧を取得します

        sort_by: "name"（名前） または "follow_order"（フォロー登録順、新しい順が0）
        sort_order: "asc" または "desc"
        """
        with self.lock:
            column = "follow_order" if sort_by != "name" else "name COLLATE NOCASE"
            direction = "DESC" if str(sort_order).lower() == "desc" else "ASC"
            cursor = self.conn.execute(f"SELECT * FROM following_users ORDER BY {column} {direction}")
            return [dict(row) for row in cursor.fetchall()]

    def clear_following_users(self):
        """保存されているフォローユーザー一覧をすべてクリア（削除）します"""
        with self.lock, self.conn:
            self.conn.execute("DELETE FROM following_users")

    def update_following_last_downloaded(self, user_id):
        """特定のフォローユーザーの最終ダウンロード日時を更新します"""
        now = datetime.now().isoformat()
        with self.lock, self.conn:
            self.conn.execute("""
                UPDATE following_users 
                SET last_downloaded = ? 
                WHERE user_id = ?
            """, (now, user_id))

    def get_setting(self, key, default=None):
        """設定値を取得します"""
        with self.lock:
            cursor = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row['value'] if row else default

    def set_setting(self, key, value):
        """設定値を保存/更新します"""
        with self.lock, self.conn:
            self.conn.execute("""
                INSERT INTO settings (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """, (key, str(value)))

    # --- Nana7mi踏み台の鍵保護（#46・docs/design/NANA7MI_VAULT_FOUNDATION_SPEC_2026-08-16.md） ---
    # settingsテーブルへの薄いラッパー（契約通り新規テーブルは作らない）。
    # パスワードそのものは一切保存しない——保存するのはPBKDF2+HKDF由来の検証用ハッシュとsaltのみ。

    def get_nana7mi_credentials(self):
        """(pw_hash_b64, salt_b64) を返す。未設定なら (None, None)。"""
        return self.get_setting("nana7mi_pw_hash"), self.get_setting("nana7mi_pw_salt")

    def set_nana7mi_credentials(self, pw_hash_b64, salt_b64):
        self.set_setting("nana7mi_pw_hash", pw_hash_b64)
        self.set_setting("nana7mi_pw_salt", salt_b64)

    def set_zipped(self, user_id, is_zipped):
        """特定のユーザーのZip圧縮対象状態を更新します"""
        with self.lock, self.conn:
            self.conn.execute("UPDATE following_users SET is_zipped = ? WHERE user_id = ?", (int(is_zipped), user_id))

    def set_favorite(self, user_id, is_favorite):
        """特定のユーザーをお気に入り状態を更新します"""
        with self.lock, self.conn:
            self.conn.execute("UPDATE following_users SET is_favorite = ? WHERE user_id = ?", (int(is_favorite), user_id))

    def get_favorite_users(self):
        """お気に入りに設定されているユーザー一覧を取得します"""
        with self.lock:
            cursor = self.conn.execute("SELECT * FROM following_users WHERE is_favorite = 1")
            return [dict(row) for row in cursor.fetchall()]

    def get_or_create_following_user(self, user_id, name, auto_archive=False):
        """following_users にレコードがなければ作成し、常に最新の行を返します。

        SELECT してから INSERT する方式は複数スレッドから同時に呼ばれると
        (拡張機能経由とGUI経由の並行実行など) IntegrityError で衝突するため、
        ON CONFLICT DO NOTHING による単一のアトミックな INSERT に統一しています。
        戻り値は (row, created) のタプルで、created は今回新規作成した場合 True。
        """
        with self.lock, self.conn:
            cursor = self.conn.execute("""
                INSERT INTO following_users (user_id, name, is_zipped)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO NOTHING
            """, (str(user_id), name, 1 if auto_archive else 0))
            created = cursor.rowcount > 0
            row = self.conn.execute(
                "SELECT name, is_zipped FROM following_users WHERE user_id = ?", (str(user_id),)
            ).fetchone()
            return row, created

    def record_followed_user(self, user_id, name=None):
        """フォローAPI成功時に1件だけ following_users へ反映します（T-0283）。

        一覧の全件更新（save_following_users）はpixiv APIを叩き直すため、フォロー
        1件ごとには走らせない（このタスクのstop_if）。既に行があれば触らない——
        is_favorite/is_zipped/last_downloaded（pixiv側に無く消えたら戻らない列）を
        保持するため、ON CONFLICT DO NOTHINGで新規行のときだけ挿入する。
        follow_orderは既存の最小値-1にし、「新しい順」の並びで先頭に来るようにする。
        """
        with self.lock, self.conn:
            min_order = self.conn.execute(
                "SELECT MIN(follow_order) FROM following_users"
            ).fetchone()[0]
            new_order = (min_order - 1) if min_order is not None else 0
            self.conn.execute("""
                INSERT INTO following_users (user_id, name, follow_order, is_following)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(user_id) DO UPDATE SET is_following = 1
            """, (str(user_id), name or "", new_order))

    def record_unfollowed_user(self, user_id):
        """フォロー解除API成功時に1件だけ following_users へ反映します（T-0291）。

        ⚠️ 行は消さない。is_favorite / is_zipped / last_downloaded は pixiv 側に無く、
        消すと二度と戻らない（CLAUDE.md §5）。フォロー中かどうかは is_following で表す。
        行が無ければ何もしない（知らない相手を「解除済み」として増やさない）。
        """
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE following_users SET is_following = 0 WHERE user_id = ?",
                (str(user_id),))

    def is_following_user(self, user_id) -> bool:
        """いまフォロー中か（T-0291）。行が無ければ False。

        ⚠️ 列が無い古いDBでも動くよう、COALESCE で既定1として読む。
        """
        with self.lock:
            row = self.conn.execute(
                "SELECT COALESCE(is_following, 1) AS f FROM following_users WHERE user_id = ?",
                (str(user_id),)).fetchone()
        return bool(row["f"]) if row is not None else False

    def add_failed_job(self, work_id, user_id, title, work_type, error_reason):
        """失敗したダウンロードまたは品質チェック異常を失敗キューに追加/更新します"""
        now = datetime.now().isoformat()
        w_id = str(work_id) if work_id is not None else ""
        u_id = str(user_id) if user_id is not None else ""
        with self.lock, self.conn:
            self.conn.execute("""
                INSERT INTO failed_queue (work_id, user_id, title, work_type, error_reason, failed_at, retry_count)
                VALUES (?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(work_id) DO UPDATE SET
                    user_id = excluded.user_id,
                    title = excluded.title,
                    work_type = excluded.work_type,
                    error_reason = excluded.error_reason,
                    failed_at = excluded.failed_at,
                    retry_count = excluded.retry_count + 1
            """, (w_id, u_id, title or "", work_type or "illust", error_reason or "不明なエラー", now))

    def remove_failed_job(self, work_id):
        """指定した作品IDを失敗キューから削除します（正常保存完了時などに使用）"""
        with self.lock, self.conn:
            self.conn.execute("DELETE FROM failed_queue WHERE work_id = ?", (str(work_id),))

    def get_failed_jobs(self):
        """失敗キューの全レコードを新しい順に取得します"""
        with self.lock:
            cursor = self.conn.execute("SELECT * FROM failed_queue ORDER BY failed_at DESC")
            return [dict(row) for row in cursor.fetchall()]

    def clear_failed_jobs(self):
        """失敗キューをすべてクリアします"""
        with self.lock, self.conn:
            self.conn.execute("DELETE FROM failed_queue")

    # --- 作者個別設定オーバーライド ---
    def get_author_setting(self, user_id, key, default=None):
        with self.lock:
            cursor = self.conn.execute("SELECT value FROM author_settings WHERE user_id = ? AND key = ?", (str(user_id), str(key)))
            row = cursor.fetchone()
            return row['value'] if row else default

    def set_author_setting(self, user_id, key, value):
        with self.lock, self.conn:
            self.conn.execute("""
                INSERT INTO author_settings (user_id, key, value)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value
            """, (str(user_id), str(key), str(value)))

    def delete_author_setting(self, user_id, key):
        with self.lock, self.conn:
            self.conn.execute("DELETE FROM author_settings WHERE user_id = ? AND key = ?", (str(user_id), str(key)))

    def get_all_author_settings(self, user_id):
        with self.lock:
            cursor = self.conn.execute("SELECT key, value FROM author_settings WHERE user_id = ?", (str(user_id),))
            return {row['key']: row['value'] for row in cursor.fetchall()}

    @contextmanager
    def _transaction(self):
        """複数の書き込みを1つの原子的な単位にまとめます。

        ⚠️ この接続は autocommit（`autocommit=True` / `isolation_level=None`）で開いている
        ため、**`with self.conn:` はトランザクションを開始しない**——BEGINが発行されないので
        commit()が何も巻き取らず、文ごとに即コミットされる。単文の書き込みしかしない既存の
        メソッドでは問題にならないが、「タグ・作者・履歴をまとめて書く」ような複数文の
        書き込みでは、途中で失敗すると片方だけ残る（実装仕様①の⚠️「1トランザクションで
        書くこと。タグだけ書けて履歴が書けないと、同じ作品が再び候補に出てきて二重に
        学習する」）。そこで明示的に BEGIN / COMMIT を発行する。

        副次的な効果として、1文ごとのfsyncが1回にまとまるため大量書き込みが桁違いに速くなる
        （seed_from_library は数万文を書くので、これが無いと実用にならない）。
        """
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
                self.conn.execute("COMMIT")
            except Exception:
                try:
                    self.conn.execute("ROLLBACK")
                except Exception:
                    pass
                raise

    # --- 発掘: 判定の生カウント（方式B） ---
    _PREF_COUNT_COLUMNS = (
        "superlike_count", "like_count", "skip_count", "hate_count",
        "shown_count", "open_count", "bookmark_count",
        "seed_saved", "seed_bookmarked",
    )
    _AUTHOR_COUNT_COLUMNS = _PREF_COUNT_COLUMNS + ("seed_following", "consecutive_skip_count")

    def _recompute_consecutive_skips(self, author_id):
        """その作者を「連続して見送った回数」を判定履歴から数え直します。

        加算方式にしないのは、Undoと再判定があるため。「見送る→見送る→いいね（0に戻る）」の
        いいねをUndoしたとき、加算方式では2へ戻せない（戻す先の値を別に覚える必要が出る）。
        履歴が正なので、そこから数え直せばどの経路を通っても必ず正しい値になる。
        呼び出し側でトランザクションを張り、**履歴を書き終えた後に**呼ぶこと。
        """
        if not author_id:
            return
        n = 0
        for row in self.conn.execute(
            "SELECT verdict FROM discovery_swipe_history WHERE author_id = ? "
            "ORDER BY judged_at DESC, work_id DESC", (str(author_id),)
        ):
            if row["verdict"] != "skip":
                break
            n += 1
        self.conn.execute("""
            INSERT INTO discovery_author_preference (author_id, consecutive_skip_count, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(author_id) DO UPDATE SET
                consecutive_skip_count = excluded.consecutive_skip_count,
                updated_at = excluded.updated_at
        """, (str(author_id), n, datetime.now().isoformat()))

    def _bump_tag(self, tag, column, amount, display_tag=None, now=None):
        """タグ1件の指定カラムを amount だけ増減させます（行が無ければ作る）。

        amount が負でも列が負値にならないよう MAX(0, ...) で床を張る。Undoの逆再生では
        理屈上ちょうど0に戻るが、履歴とカウントが何らかの理由でずれた場合に
        「負の回数」という意味を成さない状態を残さないため。
        """
        if not column or not amount:
            return
        now = now or datetime.now().isoformat()
        self.conn.execute(f"""
            INSERT INTO discovery_preference (tag, display_tag, {column}, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(tag) DO UPDATE SET
                {column} = MAX(0, COALESCE(discovery_preference.{column}, 0) + ?),
                display_tag = COALESCE(excluded.display_tag, discovery_preference.display_tag),
                updated_at = excluded.updated_at
        """, (tag, display_tag, max(0, amount), now, amount))

    def _bump_author(self, author_id, column, amount, display_name=None, now=None):
        """作者1件の指定カラムを amount だけ増減させます（行が無ければ作る）。"""
        if not column or not amount or not author_id:
            return
        now = now or datetime.now().isoformat()
        self.conn.execute(f"""
            INSERT INTO discovery_author_preference (author_id, display_name, {column}, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(author_id) DO UPDATE SET
                {column} = MAX(0, COALESCE(discovery_author_preference.{column}, 0) + ?),
                display_name = COALESCE(excluded.display_name, discovery_author_preference.display_name),
                updated_at = excluded.updated_at
        """, (str(author_id), display_name, max(0, amount), now, amount))

    def _apply_judgment(self, verdict, reason, tags, author_id, source,
                        opened, bookmarked, sign, display_tags=None, display_name=None):
        """判定1件ぶんのカウントを sign（+1で加算 / -1で減算）の向きに適用します。

        Undoと再判定の巻き戻しは「同じ関数を sign=-1 で呼ぶ」ことで実現する。
        書き込みと巻き戻しが同一のルーティング表を通るため、両者がずれようがない。
        呼び出し側でトランザクションを張ること。
        """
        now = datetime.now().isoformat()
        tag_col, author_col = _JUDGMENT_ROUTING.get(
            (verdict, reason if verdict == "hate" else None), (None, None)
        )

        # フォロー新着の見送りを作者軸へ記録しない仕様は裁定#174で否定された。
        # 無選別な新着で見送りが多くなる点は、観測を捨てず、読む側の
        # AUTHOR_JUDGMENT_WEIGHTS["skip_count"]=(0.25, 0.0) で吸収する。

        display_tags = display_tags or {}
        for tag in tags or []:
            self._bump_tag(tag, tag_col, sign, display_tag=display_tags.get(tag), now=now)
            self._bump_tag(tag, "shown_count", sign, display_tag=display_tags.get(tag), now=now)
            # おまけの加点は判定がプラス側のときだけ乗る（合意書§3。マイナス側なら消える＝
            # 「見送ったら開いたことも残さない」と宣言することで、開くコストをゼロにする）
            if verdict in ("like", "superLike"):
                if opened:
                    self._bump_tag(tag, "open_count", sign, now=now)
                if bookmarked:
                    self._bump_tag(tag, "bookmark_count", sign, now=now)

        if author_id:
            self._bump_author(author_id, "shown_count", sign, display_name=display_name, now=now)
            self._bump_author(author_id, author_col, sign, display_name=display_name, now=now)
            if verdict in ("like", "superLike"):
                if opened:
                    self._bump_author(author_id, "open_count", sign, now=now)
                if bookmarked:
                    self._bump_author(author_id, "bookmark_count", sign, now=now)

    def record_discovery_judgment(self, work_id, verdict, tags=None, author_id=None,
                                  source=None, reason=None, opened=False, bookmarked=False,
                                  display_name=None, thumbnail_url=None):
        """判定1件を **1トランザクションで** 記録します（タグ・作者・履歴をまとめて）。

        タグだけ書けて履歴が書けない状態になると、同じ作品が再び候補に出てきて
        二重に学習してしまうため、途中の失敗で片方だけ残ることを許さない。

        同じ作品を再判定した場合は、**先に旧判定を逆再生してから**新判定を適用する。
        履歴テーブルは work_id が主キーで1行に上書きされる一方、カウントは加算しか
        しないため、これを行わないと Undo→再判定の経路でカウントだけが二重に入る。

        tags は生のタグ文字列のリストを渡してよい（内部で normalize_tag する）。
        履歴には正規化後のタグを保存する——実際に加算した対象そのものを残しておけば、
        後から正規化規則を変えても過去の判定を正確に巻き戻せる。

        戻り値は保存した正規化済みタグのリスト。
        """
        if verdict not in VALID_VERDICTS:
            raise ValueError(f"未知の判定です: {verdict!r}（有効値: {VALID_VERDICTS}）")
        if reason is not None and reason not in VALID_HATE_REASONS:
            raise ValueError(f"未知の理由です: {reason!r}（有効値: {VALID_HATE_REASONS}）")
        if verdict != "hate":
            reason = None  # 「合わない」以外に理由は付かない

        display_tags = {}
        normalized = []
        for raw in tags or []:
            n = normalize_tag(raw)
            if not n or n in display_tags:
                continue
            display_tags[n] = str(raw)
            normalized.append(n)

        work_id = str(work_id)
        author_id = str(author_id) if author_id else None
        now = datetime.now().isoformat()

        with self._transaction():
            prev = self.conn.execute(
                "SELECT verdict, reason, tags, author_id, source, opened, bookmarked "
                "FROM discovery_swipe_history WHERE work_id = ?", (work_id,)
            ).fetchone()
            if prev:
                self._apply_judgment(
                    prev["verdict"], prev["reason"], json.loads(prev["tags"] or "[]"),
                    prev["author_id"], prev["source"],
                    bool(prev["opened"]), bool(prev["bookmarked"]), sign=-1,
                )

            self._apply_judgment(
                verdict, reason, normalized, author_id, source,
                bool(opened), bool(bookmarked), sign=+1,
                display_tags=display_tags, display_name=display_name,
            )

            self.conn.execute("""
                INSERT INTO discovery_swipe_history
                    (work_id, verdict, judged_at, source, reason, tags, author_id, opened, bookmarked, thumbnail_url)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(work_id) DO UPDATE SET
                    verdict = excluded.verdict,
                    judged_at = excluded.judged_at,
                    source = excluded.source,
                    reason = excluded.reason,
                    tags = excluded.tags,
                    author_id = excluded.author_id,
                    opened = excluded.opened,
                    bookmarked = excluded.bookmarked,
                    thumbnail_url = COALESCE(excluded.thumbnail_url, discovery_swipe_history.thumbnail_url)
            """, (work_id, verdict, now, source, reason,
                  json.dumps(normalized, ensure_ascii=False), author_id,
                  int(bool(opened)), int(bool(bookmarked)), thumbnail_url))

            # 履歴を書き終えてから数え直す。再判定で作者が変わることもあるため、
            # 旧作者と新作者の両方を対象にする。
            targets = {author_id}
            if prev and prev["author_id"]:
                targets.add(prev["author_id"])
            for aid in targets:
                self._recompute_consecutive_skips(aid)

        return normalized

    def undo_discovery_judgment(self, work_id):
        """直前の判定1件を取り消します（カウントを逆再生し、履歴行を削除）。

        メモリ上のスタックではなく履歴テーブルのスナップショットから巻き戻すため、
        **アプリを再起動しても1手戻せる**。取り消す判定が無ければ False を返す。
        """
        work_id = str(work_id)
        with self._transaction():
            row = self.conn.execute(
                "SELECT verdict, reason, tags, author_id, source, opened, bookmarked "
                "FROM discovery_swipe_history WHERE work_id = ?", (work_id,)
            ).fetchone()
            if not row:
                return False
            self._apply_judgment(
                row["verdict"], row["reason"], json.loads(row["tags"] or "[]"),
                row["author_id"], row["source"],
                bool(row["opened"]), bool(row["bookmarked"]), sign=-1,
            )
            self.conn.execute("DELETE FROM discovery_swipe_history WHERE work_id = ?", (work_id,))
            self._recompute_consecutive_skips(row["author_id"])
            self._prune_empty_preference_rows()
        return True

    def _prune_empty_preference_rows(self):
        """全カウントが0の行を消します（`undo_discovery_judgment` の内側から呼ぶ）。

        ⚠️ **学習は狂っていません。** 全列0の行は `preference_bounds` が n=0 として
        「わからない」を返すので、順位にも幅にも影響しません。消すのは**行だけ**が
        増え続けるのを防ぐため——初めて見たタグを判定して取り消すと、そのタグの行が
        値0のまま残ります（`_bump_tag` が「行が無ければ作る」ため）。

        ⚠️ **既存資産(seed)が入っている行は消さない。** `seed_saved` などは判定と別の
        カウントで、判定を取り消しても失われてはいけない事実です（合意書§7-3）。
        全列0という条件がそのまま「seedも無い」を含んでいます。
        """
        cols = self._PREF_COUNT_COLUMNS
        cond = " AND ".join(f"COALESCE({c}, 0) = 0" for c in cols)
        self.conn.execute(f"DELETE FROM discovery_preference WHERE {cond}")
        acols = self._AUTHOR_COUNT_COLUMNS
        acond = " AND ".join(f"COALESCE({c}, 0) = 0" for c in acols)
        self.conn.execute(f"DELETE FROM discovery_author_preference WHERE {acond}")

    def get_discovery_preferences(self):
        """全タグの生カウントを {正規化タグ: {列名: 値}} で取得します（並べ替え用）。"""
        cols = ", ".join(self._PREF_COUNT_COLUMNS)
        with self.lock:
            rows = self.conn.execute(
                f"SELECT tag, display_tag, {cols} FROM discovery_preference"
            ).fetchall()
        out = {}
        for row in rows:
            d = {c: (row[c] or 0) for c in self._PREF_COUNT_COLUMNS}
            d["display_tag"] = row["display_tag"] or row["tag"]
            out[row["tag"]] = d
        return out

    def get_discovery_author_preferences(self):
        """全作者の生カウントを {作者ID: {列名: 値}} で取得します（並べ替え用）。"""
        cols = ", ".join(self._AUTHOR_COUNT_COLUMNS)
        with self.lock:
            rows = self.conn.execute(
                f"SELECT author_id, display_name, {cols} FROM discovery_author_preference"
            ).fetchall()
        out = {}
        for row in rows:
            d = {c: (row[c] or 0) for c in self._AUTHOR_COUNT_COLUMNS}
            d["display_name"] = row["display_name"]
            out[row["author_id"]] = d
        return out

    def seed_from_library(self):
        """既存資産（保存済み作品・フォロー中の作者）から `seed_*` 列を埋めます。

        PC版は「ゼロから学習する」のではなく「既にある大量の証拠を読み込んでから、判定で
        微調整する」（合意書§8）。保存済み作品はクリック1回ではなくディスクを消費してまで
        残すと決めたものなので、いいねより強い証拠として扱う。

        ⚠️ `seed_*` は判定カウントとは**別の器**に入れる（§7-3）。混ぜてしまうと後から
        「保存履歴の効きが強すぎた／弱すぎた」と気づいても調整できない。

        **何度実行してもよい**（毎回ゼロから数え直す）。作品が増えた後の再実行で最新化できる。
        ブックマーク由来の `seed_bookmarked` はネットワーク取得が要るため
        `seed_from_bookmarks()` に分けてある。

        戻り値は {"tags": 触れたタグ数, "authors": 触れた作者数, "works": 数えた作品数}。
        """
        now = datetime.now().isoformat()
        tag_counts, author_counts = {}, {}
        works_counted = 0

        with self._transaction():
            rows = self.conn.execute(
                "SELECT user_id, tags FROM works WHERE is_deleted = 0"
            ).fetchall()
            for row in rows:
                try:
                    raw_tags = json.loads(row["tags"] or "[]")
                except (ValueError, TypeError):
                    raw_tags = []
                if row["user_id"]:
                    author_counts[str(row["user_id"])] = author_counts.get(str(row["user_id"]), 0) + 1
                if not raw_tags:
                    continue
                works_counted += 1
                seen = set()
                for raw in raw_tags:
                    n = normalize_tag(raw)
                    if not n or n in seen:
                        continue
                    seen.add(n)
                    tag_counts[n] = tag_counts.get(n, 0) + 1

            following = {
                str(r["user_id"]) for r in
                self.conn.execute("SELECT user_id FROM following_users").fetchall()
                if r["user_id"]
            }

            # 数え直しなので、まず既存の seed 値を落としてから入れ直す（二重加算の防止）
            self.conn.execute("UPDATE discovery_preference SET seed_saved = 0")
            self.conn.execute(
                "UPDATE discovery_author_preference SET seed_saved = 0, seed_following = 0"
            )
            for tag, n in tag_counts.items():
                self._bump_tag(tag, "seed_saved", n, now=now)
            for author_id, n in author_counts.items():
                self._bump_author(author_id, "seed_saved", n, now=now)
            for author_id in following:
                self._bump_author(author_id, "seed_following", 1, now=now)

        return {"tags": len(tag_counts), "authors": len(author_counts), "works": works_counted}

    def seed_from_bookmarks(self, bookmarked_works):
        """ブックマーク済み作品から `seed_bookmarked` を埋めます。

        `bookmarked_works` は PixivClient.get_bookmarked_works() が返す形の辞書のリスト
        （`tags` と `user_id` を使う）。ネットワーク取得は呼び出し側の責務にして、
        このクラスがHTTPを知らずに済むようにしている。

        `seed_from_library()` と同じく**何度実行してもよい**（毎回ゼロから数え直す）。
        """
        now = datetime.now().isoformat()
        tag_counts, author_counts = {}, {}

        for work in bookmarked_works or []:
            user_id = work.get("user_id")
            if user_id:
                author_counts[str(user_id)] = author_counts.get(str(user_id), 0) + 1
            seen = set()
            for raw in work.get("tags") or []:
                n = normalize_tag(raw)
                if not n or n in seen:
                    continue
                seen.add(n)
                tag_counts[n] = tag_counts.get(n, 0) + 1

        with self._transaction():
            self.conn.execute("UPDATE discovery_preference SET seed_bookmarked = 0")
            self.conn.execute("UPDATE discovery_author_preference SET seed_bookmarked = 0")
            for tag, n in tag_counts.items():
                self._bump_tag(tag, "seed_bookmarked", n, now=now)
            for author_id, n in author_counts.items():
                self._bump_author(author_id, "seed_bookmarked", n, now=now)

        return {"tags": len(tag_counts), "authors": len(author_counts)}

    def clear_discovery_preference(self):
        """学習した嗜好カウントをすべてクリアします（判定履歴は消しません）。"""
        # 2026-08-27: `with self.lock, self.conn:` は実際にはトランザクションを開始しない
        # （_transaction()のdocstring参照）。2つ目のDELETEが失敗すると片方のテーブルだけ
        # 消えた状態が残るため、_transaction()経由にする。
        with self._transaction() as conn:
            conn.execute("DELETE FROM discovery_preference")
            conn.execute("DELETE FROM discovery_author_preference")

    def reset_discovery_learning(self, keep_seed=True):
        """学習状況をリセットします（判定履歴の全削除＋判定由来カウントの初期化）。

        範囲は docs/design/DISCOVERY_RESET_SPEC_2026-09-03.md §2 の案B——
        discovery_swipe_history を全削除し、discovery_preference /
        discovery_author_preference の判定由来カウントを0に戻す。閲覧履歴・
        「出さない」一覧・世間の分母には触れない。

        keep_seed=True（既定）では seed_* 列は残し、判定由来のカウント列だけを
        0にする（0になった行は _prune_empty_preference_rows() で削除——seedの
        入っている行だけが残る）。keep_seed=False では clear_discovery_preference()
        と同じく行ごと削除する。

        ⚠️ 件数は cursor.rowcount で取る。トランザクションの中から
        get_judged_work_ids() 等の公開メソッドを呼ぶと、再入不可の self.lock を
        二重に取ろうとしてデッドロックする（例外も出ず「重い」としか見えない）。
        """
        judgment_cols = [c for c in self._PREF_COUNT_COLUMNS if not c.startswith("seed_")]
        with self._transaction() as conn:
            cursor = conn.execute("DELETE FROM discovery_swipe_history")
            history_count = cursor.rowcount

            if keep_seed:
                set_clause = ", ".join(f"{c} = 0" for c in judgment_cols)
                cursor = conn.execute(f"UPDATE discovery_preference SET {set_clause}")
                tags_count = cursor.rowcount
                author_set_clause = ", ".join(
                    f"{c} = 0" for c in judgment_cols + ["consecutive_skip_count"])
                cursor = conn.execute(f"UPDATE discovery_author_preference SET {author_set_clause}")
                authors_count = cursor.rowcount
                self._prune_empty_preference_rows()
            else:
                cursor = conn.execute("DELETE FROM discovery_preference")
                tags_count = cursor.rowcount
                cursor = conn.execute("DELETE FROM discovery_author_preference")
                authors_count = cursor.rowcount

            return {"history": history_count, "tags": tags_count, "authors": authors_count}

    def get_judged_work_ids(self):
        """既に判定済みの作品IDの集合を取得します（候補プールの重複除去に使用）"""
        with self.lock:
            cursor = self.conn.execute("SELECT work_id FROM discovery_swipe_history")
            return {row['work_id'] for row in cursor.fetchall()}

    def get_hated_work_ids(self):
        """学習タブで「合わない」（verdict='hate'）と判定した作品IDの集合を取得します。

        出どころ: docs/design/RANKING_TAB_REDESIGN_SPEC_2026-08-23.md §4-4-2（PO決定・
        2026-08-23）。「自分のランキング」の絞り込み「除外」節で使う読み取り専用メソッド。

        ⚠️ get_judged_work_ids() との違い: あちらは verdict を問わず**判定済み全部**を返す
        （候補プールの重複除去用）。こちらは 'hate' だけ——'skip'（見送り）は「NG」ではない
        ため含めない（PO決定。VALID_VERDICTS は database.py:25 の
        ("skip", "hate", "like", "superLike")）。
        """
        with self.lock:
            cursor = self.conn.execute(
                "SELECT work_id FROM discovery_swipe_history WHERE verdict = 'hate'")
            return {row['work_id'] for row in cursor.fetchall()}

    def get_downloaded_work_ids(self):
        """ローカルへダウンロード済みの作品IDの集合を取得します。

        出どころ: docs/design/RANKING_TAB_REDESIGN_SPEC_2026-08-23.md §4-4-2（PO決定・
        2026-08-23）。判定条件は library_tab.py:55-69 の work_actually_saved() と同じ
        （last_backup が入っていることをもって「保存済み」とみなす）。

        ⚠️ is_deleted は**除外しない**（PO決定・2026-08-23）——pixiv側で消えていても
        ローカルにファイルが残っているものは「ダウンロード済み」として扱う。
        ⚠️ works に is_zipped・last_downloaded という列は無い（あれは following_users＝
        作者テーブルの列）。作品単位の「DL済み」は last_backup で判定すること。
        """
        with self.lock:
            cursor = self.conn.execute(
                "SELECT work_id FROM works WHERE last_backup IS NOT NULL AND last_backup != ''")
            return {row['work_id'] for row in cursor.fetchall()}

    # --- 【#60-A】「出さない」・「この作者をもっと見る」 -----------------------------
    # docs/design/CONTEXT_MENU_SPEC_2026-08-19.md §3-3。

    def add_hidden_work(self, work_id, reason=None, author_id=None, title=None, thumbnail_url=None):
        """「出さない」を記録します（冪等: 同じwork_idは上書き）。

        title・thumbnail_url は「出さない一覧」の表示専用（2026-08-20・thumbnail_urlは
        2026-08-21追加）。呼び出し側が既に持っている値をそのまま渡すだけで、DB側で
        別途取得はしない——既定Noneのままでも既存の呼び出し元（渡さない）は無変更で動く。
        """
        now = datetime.now().isoformat()
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO discovery_hidden_works "
                "(work_id, reason, author_id, hidden_at, title, thumbnail_url) VALUES (?, ?, ?, ?, ?, ?)",
                (str(work_id), reason, str(author_id) if author_id else None, now, title, thumbnail_url),
            )

    def get_hidden_work_ids(self):
        """「出さない」済みの作品IDの集合を取得します（候補プールの除外に使用）。"""
        with self.lock:
            cursor = self.conn.execute("SELECT work_id FROM discovery_hidden_works")
            return {row['work_id'] for row in cursor.fetchall()}

    def get_hidden_works(self):
        """「出さない」済みの一覧を、新しい順で取得します（表示用・2026-08-20）。

        work_id集合だけが要るときは get_hidden_work_ids() を使うこと——
        こちらはタイトル等の表示用メタデータ付きの別関数（シグネチャ変更ではない）。
        """
        with self.lock:
            cursor = self.conn.execute(
                "SELECT work_id, title, reason, author_id, hidden_at, thumbnail_url "
                "FROM discovery_hidden_works ORDER BY hidden_at DESC"
            )
            return [dict(row) for row in cursor.fetchall()]

    def remove_hidden_work(self, work_id):
        """「出さない」を取り消します。"""
        with self.lock, self.conn:
            self.conn.execute(
                "DELETE FROM discovery_hidden_works WHERE work_id = ?", (str(work_id),)
            )

    # --- 「この作者を出さない」（discovery_hidden_worksの作者版。2026-08-22） -------

    def add_hidden_author(self, author_id, reason=None, display_name=None):
        """「この作者を出さない」を記録します（冪等: 同じauthor_idは上書き）。"""
        now = datetime.now().isoformat()
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO discovery_hidden_authors "
                "(author_id, display_name, reason, hidden_at) VALUES (?, ?, ?, ?)",
                (str(author_id), display_name, reason, now),
            )

    def get_hidden_author_ids(self):
        """「出さない」済みの作者IDの集合を取得します（候補プールの除外に使用）。"""
        with self.lock:
            cursor = self.conn.execute("SELECT author_id FROM discovery_hidden_authors")
            return {row['author_id'] for row in cursor.fetchall()}

    def get_hidden_authors(self):
        """「出さない」済みの作者一覧を、新しい順で取得します（表示用）。"""
        with self.lock:
            cursor = self.conn.execute(
                "SELECT author_id, display_name, reason, hidden_at "
                "FROM discovery_hidden_authors ORDER BY hidden_at DESC"
            )
            return [dict(row) for row in cursor.fetchall()]

    def remove_hidden_author(self, author_id):
        """「この作者を出さない」を取り消します。"""
        with self.lock, self.conn:
            self.conn.execute(
                "DELETE FROM discovery_hidden_authors WHERE author_id = ?", (str(author_id),)
            )

    def add_boosted_author(self, author_id, display_name=None):
        """「この作者をもっと見る」を記録します（冪等: 同じauthor_idは上書き）。"""
        now = datetime.now().isoformat()
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO discovery_boosted_authors "
                "(author_id, display_name, boosted_at) VALUES (?, ?, ?)",
                (str(author_id), display_name, now),
            )

    def get_boosted_authors(self):
        """「もっと見る」に登録済みの作者一覧を、新しい順で取得します。"""
        with self.lock:
            cursor = self.conn.execute(
                "SELECT author_id, display_name, boosted_at FROM discovery_boosted_authors "
                "ORDER BY boosted_at DESC"
            )
            return [dict(row) for row in cursor.fetchall()]

    def remove_boosted_author(self, author_id):
        """「もっと見る」を取り消します。"""
        with self.lock, self.conn:
            self.conn.execute(
                "DELETE FROM discovery_boosted_authors WHERE author_id = ?", (str(author_id),)
            )

    def get_discovery_swipe_history(self, verdict=None):
        """スワイプ判定履歴を新しい順に取得します。verdict指定時はその判定のみに絞ります"""
        with self.lock:
            if verdict:
                cursor = self.conn.execute(
                    "SELECT * FROM discovery_swipe_history WHERE verdict = ? ORDER BY judged_at DESC", (verdict,)
                )
            else:
                cursor = self.conn.execute("SELECT * FROM discovery_swipe_history ORDER BY judged_at DESC")
            return [dict(row) for row in cursor.fetchall()]

    def get_undismissed_superlikes(self):
        """おすすめタブ『あなたがSuperLikeした作品』節用（#56）:
        verdict='superLike'かつrecommend_dismissed=0の行を新しい順に返す。
        """
        with self.lock:
            cursor = self.conn.execute(
                "SELECT work_id, judged_at FROM discovery_swipe_history "
                "WHERE verdict = 'superLike' "
                "AND (recommend_dismissed IS NULL OR recommend_dismissed = 0) "
                "ORDER BY judged_at DESC"
            )
            return [dict(row) for row in cursor.fetchall()]

    def dismiss_recommend(self, work_id):
        """おすすめタブから明示的に取り下げる（#56）。verdict列には触れない——
        学習（判定そのもの）は残したまま、一覧から外すためだけの操作。
        """
        with self.lock, self.conn:
            self.conn.execute(
                "UPDATE discovery_swipe_history SET recommend_dismissed = 1 WHERE work_id = ?",
                (str(work_id),),
            )

    # --- 閲覧履歴（11-A・第一弾。docs/design/DISCOVERY_VIEW_HISTORY_SPEC_2026-08-15.md） ---

    def record_discovery_open(self, work_id, thumbnail_url=None):
        """学習タブで作品を「開いた」事実を生カウントとして記録します（合意書§7）。

        同じ work_id は行を UPSERT で1行に保ち、open_count を+1するだけです。
        加工（頭打ち・時間の重み付け等）はしません——それは読むときの仕事です。

        thumbnail_url は候補が持つサムネイルURLをそのまま保存するだけで、ここでは
        取得も加工もしません（履歴ダイアログを開いたときに初めて画像バイトを取りに行く）。
        再訪時に新しい値が来なければ（None）、既存の値を保持します（COALESCE）。
        """
        now = datetime.now().isoformat()
        with self.lock, self.conn:
            self.conn.execute("""
                INSERT INTO discovery_view_history
                    (work_id, open_count, first_opened_at, last_opened_at, thumbnail_url)
                VALUES (?, 1, ?, ?, ?)
                ON CONFLICT(work_id) DO UPDATE SET
                    open_count = open_count + 1,
                    last_opened_at = excluded.last_opened_at,
                    thumbnail_url = COALESCE(excluded.thumbnail_url, discovery_view_history.thumbnail_url)
            """, (work_id, now, now, thumbnail_url))

    def get_discovery_view_history(self):
        """閲覧履歴を最終閲覧が新しい順に取得します。works.title・学習の判定verdictを
        左結合して返します（#57・判定バッジ用。discovery_swipe_historyを複製せず
        work_idでその場参照する——書き換え型の同期は行わない）。

        ⚠️ 列名は discovery_verdict にしてある（verdictにすると両テーブルの列名衝突を
        避けるための明示。dict(row)化時の取り違え事故を防ぐ）。
        """
        with self.lock:
            cursor = self.conn.execute("""
                SELECT h.work_id, h.open_count, h.first_opened_at, h.last_opened_at,
                       h.thumbnail_url, w.title, s.verdict AS discovery_verdict,
                       COALESCE(s.author_id, w.user_id) AS author_id
                FROM discovery_view_history h
                LEFT JOIN works w ON w.work_id = h.work_id
                LEFT JOIN discovery_swipe_history s ON s.work_id = h.work_id
                ORDER BY h.last_opened_at DESC
            """)
            return [dict(row) for row in cursor.fetchall()]

    def get_discovery_swipe_history_for_review(self):
        """振り返りビュー用（#57）: discovery_swipe_history全件をworksと突き合わせて返す。

        ⚠️ ここでは画像取得（thumbnail_url取得のためのAPI呼び出し）は一切行わない——
        呼び出し側（discovery_tab.py）が表示するページぶん（9〜11件）だけ、必要なら
        get_work_infoで補う設計（#56のSuperLike節と同じ「表示ぶんだけ」の考え方）。
        全件をここで返すが、SQLite側は画像を含まないため数千件でも軽い。
        """
        with self.lock:
            cursor = self.conn.execute("""
                SELECT h.work_id, h.verdict, h.reason, h.tags, h.author_id, h.source,
                       h.judged_at, h.opened, h.bookmarked, h.thumbnail_url,
                       w.title, w.work_meta, w.is_deleted, w.last_backup
                FROM discovery_swipe_history h
                LEFT JOIN works w ON w.work_id = h.work_id
                ORDER BY h.judged_at DESC
            """)
            return [dict(row) for row in cursor.fetchall()]

    def clear_discovery_view_history(self):
        """閲覧履歴を全件削除します（DangerZone確定後にのみ呼ばれる想定）。"""
        with self.lock, self.conn:
            self.conn.execute("DELETE FROM discovery_view_history")

    # --- 一般母集団（世間との比較）: おすすめタブ合意書 §9-2-1・§9-2-2 ---

    def record_population_works(self, works, audience, decay_at=None, decay_factor=0.5):
        """ランキングで観測した作品を一般母集団へ数え入れます。

        `works` は `[{"work_id": str, "tags": [表記のままのタグ, ...]}, ...]`。
        `audience` は "sfw" | "r18"。**混ぜずに別々に貯める**（合意書§9-2-1 ②）。

        ⚠️ **1作品は1回だけ数えます**（`discovery_population_seen`）。日次と週次には同じ
        作品が出るため、これが無いと人気作だけ何度も加算されて分母が歪みます。
        既に数えた作品しか無かった場合は、何も書かずに 0 を返します。

        ⚠️ **タグは正規化して数え、表示用の表記は別に持ちます**（`display_tag`）。
        `discovery_preference` と同じ流儀です。

        `decay_at` を渡すと、数え終えた分母がそれを超えたときに過去の集計を
        `decay_factor` 倍へ減衰させます（合意書§9-2-1 ④「古い観測は忘れる」）。
        **呼び出し側が名前付き定数で渡すこと。ユーザー設定にはしない。**

        戻り値は新しく数えた作品数。
        """
        audience = "r18" if str(audience) == "r18" else "sfw"
        now = datetime.now().isoformat()

        fresh = []
        for w in works or []:
            wid = str((w or {}).get("work_id") or "")
            if not wid:
                continue
            fresh.append((wid, (w or {}).get("tags") or []))
        if not fresh:
            return 0

        with self._transaction() as conn:
            # 既に数えた作品を除く（作品IDで照合。位置や順序では見ない）
            known = set()
            for chunk_start in range(0, len(fresh), 400):
                chunk = fresh[chunk_start:chunk_start + 400]
                placeholders = ",".join("?" for _ in chunk)
                cur = conn.execute(
                    f"SELECT work_id FROM discovery_population_seen "
                    f"WHERE audience = ? AND work_id IN ({placeholders})",
                    (audience, *[wid for wid, _ in chunk]),
                )
                known.update(row["work_id"] for row in cur.fetchall())

            counted = 0
            for wid, tags in fresh:
                if wid in known:
                    continue
                known.add(wid)   # 同じ応答の中に同じ作品が2回出ても1回だけ数える
                counted += 1
                conn.execute(
                    "INSERT OR REPLACE INTO discovery_population_seen (work_id, audience, seen_at) "
                    "VALUES (?, ?, ?)",
                    (wid, audience, now),
                )
                # 1作品の中に同じタグが2回出ても「その作品に付いていた」の1回だけ数える
                seen_tags = set()
                for raw in tags:
                    norm = normalize_tag(raw)
                    if not norm or norm in seen_tags:
                        continue
                    seen_tags.add(norm)
                    conn.execute("""
                        INSERT INTO discovery_population (tag, audience, doc_count, display_tag, updated_at)
                        VALUES (?, ?, 1, ?, ?)
                        ON CONFLICT(tag, audience) DO UPDATE SET
                            doc_count = doc_count + 1,
                            display_tag = excluded.display_tag,
                            updated_at = excluded.updated_at
                    """, (norm, audience, str(raw), now))

            if not counted:
                return 0

            conn.execute("""
                INSERT INTO discovery_population_meta (audience, work_count, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(audience) DO UPDATE SET
                    work_count = work_count + excluded.work_count,
                    updated_at = excluded.updated_at
            """, (audience, counted, now))

            if decay_at:
                cur = conn.execute(
                    "SELECT work_count FROM discovery_population_meta WHERE audience = ?",
                    (audience,),
                )
                row = cur.fetchone()
                total = int(row["work_count"] or 0) if row else 0
                if total > int(decay_at):
                    self._decay_population_locked(conn, audience, float(decay_factor), now)

        return counted

    def _decay_population_locked(self, conn, audience, factor, now):
        """過去の集計を減衰させます（`record_population_works` の内側から呼ぶ）。

        ⚠️ **数えた記録（`discovery_population_seen`）も同じ割合だけ古い順に捨てます。**
        カウントだけ減らして記録を残すと、分母は減るのに「もう数えた」だけが積み上がり、
        母集団が二度と増えなくなります（＝忘れたのに覚えている状態）。
        """
        factor = min(max(float(factor), 0.0), 1.0)
        conn.execute(
            "UPDATE discovery_population SET doc_count = CAST(doc_count * ? AS INTEGER), updated_at = ? "
            "WHERE audience = ?",
            (factor, now, audience),
        )
        conn.execute("DELETE FROM discovery_population WHERE audience = ? AND doc_count <= 0", (audience,))
        conn.execute("""
            UPDATE discovery_population_meta
            SET work_count = CAST(work_count * ? AS INTEGER), updated_at = ?
            WHERE audience = ?
        """, (factor, now, audience))
        cur = conn.execute(
            "SELECT COUNT(*) AS n FROM discovery_population_seen WHERE audience = ?", (audience,)
        )
        seen_total = int((cur.fetchone() or {"n": 0})["n"] or 0)
        drop = seen_total - int(seen_total * factor)
        if drop > 0:
            conn.execute("""
                DELETE FROM discovery_population_seen
                WHERE rowid IN (
                    SELECT rowid FROM discovery_population_seen
                    WHERE audience = ? ORDER BY seen_at ASC LIMIT ?
                )
            """, (audience, drop))

    def get_population_tag_counts(self, audience="sfw"):
        """一般母集団のタグ件数を {正規化タグ: 件数} で返します。"""
        audience = "r18" if str(audience) == "r18" else "sfw"
        with self.lock:
            cursor = self.conn.execute(
                "SELECT tag, doc_count FROM discovery_population WHERE audience = ?", (audience,)
            )
            return {row["tag"]: int(row["doc_count"] or 0) for row in cursor.fetchall()}

    def get_population_meta(self):
        """{audience: {"work_count": int, "updated_at": str}} を返します（状態表示用）。"""
        with self.lock:
            cursor = self.conn.execute(
                "SELECT audience, work_count, updated_at FROM discovery_population_meta"
            )
            return {
                row["audience"]: {
                    "work_count": int(row["work_count"] or 0),
                    "updated_at": row["updated_at"],
                }
                for row in cursor.fetchall()
            }

    def close(self):
        """データベース接続を安全に閉じます。アプリ終了時に呼び出してください。"""
        try:
            self.conn.close()
        except Exception:
            pass
