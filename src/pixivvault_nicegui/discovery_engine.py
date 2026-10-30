"""発掘機能の候補データ供給レイヤー。

候補元は CANDIDATE_SOURCES に「同じ形の辞書のリストを返す関数」として登録する。
フェーズ1（docs/archive/DISCOVERY_PC_IMPLEMENTATION_BRAINSTORM_2026-08-05.md §1）として
"following"（フォロー新着）・"ranking"（pixiv公式ランキング）を配線済み。"mock" は
テスト・オフライン確認用にそのまま残っている。公式関連サジェスト・フォロー2ホップ等の
残りのフェーズは今後追加していく。呼び出し側（gui.py の発掘タブ）は get_next_batch()
が返す下記スキーマにのみ依存する。

⚠️⚠️ **get_next_batch はリストではなく `BatchResult` を返す**（フェーズ6-2-5）。
理由はそのクラスの説明を読むこと——**「候補0件」には2つの意味がある。**

候補1件のスキーマ:
    work_id: str
    title: str
    author_name: str
    author_id: str
    thumbnail_url: str | None
    tags: list[str]
    source: str             # "following" | "ranking" | "similar_author" | "mock"
    bookmark_count: int | None
"""

import logging
import random

from . import discovery_population

logger = logging.getLogger(__name__)

_MOCK_POOL = [
    {
        "work_id": "mock-1", "title": "無題の連作 第3話", "author_name": "花見坂 いろは",
        "author_id": "mock-author-1", "thumbnail_url": None,
        "tags": ["オリジナル", "和風", "連作"], "bookmark_count": 812,
    },
    {
        "work_id": "mock-2", "title": "夜行バスと君の話", "author_name": "青野 すばる",
        "author_id": "mock-author-2", "thumbnail_url": None,
        "tags": ["オリジナル", "エッセイ漫画", "旅"], "bookmark_count": 231,
    },
    {
        "work_id": "mock-3", "title": "百鬼夜行スケッチ", "author_name": "宵宮 かなで",
        "author_id": "mock-author-3", "thumbnail_url": None,
        "tags": ["妖怪", "線画", "モノクロ"], "bookmark_count": 1502,
    },
    {
        "work_id": "mock-4", "title": "喫茶ノスタルジア", "author_name": "花見坂 いろは",
        "author_id": "mock-author-1", "thumbnail_url": None,
        "tags": ["オリジナル", "喫茶店", "背景"], "bookmark_count": 640,
    },
    {
        "work_id": "mock-5", "title": "たそがれ通信 #12", "author_name": "白樺 ミナト",
        "author_id": "mock-author-4", "thumbnail_url": None,
        "tags": ["連作", "水彩", "空"], "bookmark_count": 98,
    },
]


def _source_mock(n=20, exclude_ids=None, **_kwargs):
    """常にダミー候補を返す（実データ未配線時の既定ソース）"""
    exclude_ids = exclude_ids or set()
    pool = [w for w in _MOCK_POOL if w["work_id"] not in exclude_ids]
    random.shuffle(pool)
    out = []
    for w in pool[:n]:
        candidate = dict(w)
        candidate["source"] = "mock"
        out.append(candidate)
    return out


# 各候補元が1回のバッチ取得で発行するリクエスト数の上限。
# 756eefc（DISCOVERY_API_RATE_TEST_PLAN_2026-08-05.md）の実測（補充レートR≈1.15req/s、
# バースト容量C≈71リクエスト分）を踏まえ、**1回の呼び出しの合計を一桁台に抑える**。
#
#   following      最大 2人 × 最大2リクエスト = 4
#   ranking        1
#   related_author 2人 × 1 = 2
#   tag_search     2タグ × 1 = 2
#   ────────────────────────────  合計 最大9
#
# ⚠️ 候補元を足すときは、この合計を必ず数え直すこと。2026-08-06に related_author と
# tag_search を追加した際、following を3人から2人へ下げて9本に収めた（そのままだと11本）。
# 1件あたりの収穫は related_author と tag_search のほうが桁違いに大きい
# （1リクエストで30〜60件。following は新着が無ければ0件のこともある）ため、
# 削る先として following を選んでいる。
_FOLLOWING_MAX_USERS_PER_BATCH = 2
_FOLLOWING_FIRST_PAGE_CAP = 48  # 新着確認は最新1ページ分のみ（全履歴走査はしない）

# 関連作者（公式レコメンド）は1作者=1リクエストで関連作者10人・作品30件前後が返るため、
# following より少ない回数で多くの候補が取れる。1回のバッチでは2人ぶんだけを見る。
_RELATED_MAX_SEEDS_PER_BATCH = 2

# タグ検索は1タグ=1リクエストで60件返る。1バッチ2タグに抑える（合計リクエスト数を
# 一桁台に保つ: following最大6＋ranking1＋related2＋search2 ＝ 最大11）。
_SEARCH_TAGS_PER_BATCH = 2
_SEARCH_TAG_POOL = 20      # 上位何件のタグをローテーションの母集団にするか
_UNKNOWN_TAG_MIN = 2       # 未知タグをいくつ持てば「題材が未知」寄りとみなすか


def _get_db(db):
    if db is None: raise ValueError("explicit managed database required")
    return db


def _get_client(client, db):
    if client is None: raise ValueError("explicit reader required")
    return client


def _source_following(n=20, exclude_ids=None, db=None, client=None, **_kwargs):
    """フォロー中作者の新着作品を候補として返す（フェーズ1、following_users テーブル起点）。

    フォロー中一覧の取得自体はAPIを叩かず、既に database.py に保存済みの一覧
    （get_following_users）をそのまま使う。対象作者は `discovery_following_cursor`
    設定でローテーションし、1回の呼び出しでは `_FOLLOWING_MAX_USERS_PER_BATCH` 人だけを
    見る。各作者につき「最新48件」のみを確認する（早期打ち切り：既知の作品IDに到達した
    時点、または48件に達した時点でそれ以上のページ取得を行わない）ことで、フォロー一括DL
    のような全履歴走査にはしない。
    """
    exclude_ids = exclude_ids or set()
    try:
        db = _get_db(db)
        client = _get_client(client, db)
    except Exception:
        # ⚠️ 取りにいけなかった（DB/クライアント初期化に失敗＝実行環境の問題）。
        # 実装契約2026-09-01「取りにいけなかったを伝える」: return []で握り潰すと
        # get_next_batchが「成功・0件」と誤って数える。呼び出し元(get_next_batch)の
        # 既存の握り潰し(1ソースの失敗は他を巻き込まない)へ委ね、failedへ正しく積ませる。
        logger.warning("discovery source status")
        raise

    try:
        following = db.get_following_users(sort_by="follow_order", sort_order="asc")
    except Exception:
        # ⚠️ 取りにいけなかった（DB読み取りの失敗）。上と同じ理由でraiseへ変更。
        logger.warning("discovery source status")
        raise
    if not following:
        # ⭐ 本当に在庫が無い（フォロー中の作者が0人）。DB自体は正常に読めており、
        # 「取りにいけなかった」ではない——素直な0件なのでreturn []のままでよい。
        return []

    try:
        cursor = int(db.get_setting("discovery_following_cursor", "0") or 0) % len(following)
    except Exception:
        cursor = 0
    ordered = following[cursor:] + following[:cursor]
    targets = ordered[:_FOLLOWING_MAX_USERS_PER_BATCH]
    try:
        db.set_setting("discovery_following_cursor", (cursor + len(targets)) % len(following))
    except Exception:
        pass

    try:
        judged_ids = db.get_judged_work_ids()
    except Exception:
        # ⚠️ 取りにいけなかった（DB読み取りの失敗）。判定済みの除外が効かなくなると
        # 「判定済みの作品が再び出る」という、学習していないように見える形で現れる
        # （CLAUDE.md §3の「黙る嘘」）。DB読み取り失敗＝取りにいけなかった、という
        # 規則を他のDB読み取り箇所と揃える。
        logger.warning("discovery source status")
        raise

    out = []
    attempted = 0
    succeeded = 0
    for user in targets:
        user_id = user.get("user_id")
        if not user_id:
            continue
        attempted += 1
        try:
            known_ids = db.get_user_work_ids(user_id)
        except Exception:
            # ⚠️ ここだけループの内側（1ユーザーごと）にあるため、他のDB読み取り箇所
            # と同じ単純raiseにはしない——1人のDB読み取りが詰まっただけでバッチ全体
            # （他のユーザーぶんの候補も）を失敗にすると、下の「全滅のみ失敗」という
            # 設計と矛盾する。1人ぶんの失敗として数え、次のユーザーへ続行する。
            logger.warning("discovery source status")
            continue

        seen = [0]

        def _early_exit(parsed_work, _known_ids=known_ids, _seen=seen):
            _seen[0] += 1
            if str(parsed_work.get("id")) in _known_ids:
                return True
            return _seen[0] >= _FOLLOWING_FIRST_PAGE_CAP

        try:
            works = client.get_user_works(user_id, early_exit_checker=_early_exit, lane="interactive")
        except Exception:
            logger.warning("discovery source status")
            continue
        succeeded += 1

        for w in works:
            wid = str(w.get("id"))
            if not wid or wid in exclude_ids or wid in judged_ids or wid in known_ids:
                continue
            candidate = {
                "work_id": wid,
                "title": w.get("title") or "",
                "author_name": w.get("user_name") or user.get("name") or "",
                "author_id": str(user_id),
                "thumbnail_url": w.get("thumbnail_url") or None,
                "tags": w.get("tags") or [],
                "source": "following",
                "bookmark_count": None,  # profile/illusts はブックマーク数を返さない
            }
            out.append(candidate)
            if len(out) >= n:
                return out
    if attempted > 0 and succeeded == 0:
        # ⚠️ 取りにいけなかった（全滅）。対象者全員の取得(get_user_work_ids/
        # get_user_works)が失敗した。1件も成功していないので在庫の有無が
        # 一切分からない——「候補が無い」と偽らずget_next_batchのfailedへ積ませる。
        raise RuntimeError(f"発掘（フォロー新着）: 対象{attempted}人すべての作品取得に失敗しました。")
    return out


def _is_not_found(exc):
    return (getattr(exc, 'status_code', None) == 404 or getattr(getattr(exc, 'response', None), 'status_code', None) == 404)


def _source_ranking(n=20, exclude_ids=None, db=None, client=None, **_kwargs):
    """pixiv公式ランキングを候補として返す（フェーズ1）＋ 一般母集団を貯める（フェーズ7-1）。

    コールドスタート対策・新規開拓用のソース。1呼び出し=1リクエスト（ranking.php 1ページ、
    最大50件）で完結する軽量ラッパー。

    ⚠️ **取る場所は輪番する**（日次／週次／月次 × ページ）。
    **日次ランキングの1ページ目は、その日ずっと同じ50件**なので、固定していると
    何度取り直しても新しい作品が1件も入らない。リクエスト数は変えずに場所だけ回す。
    候補としても毎回違う50件になるので、こちらにも効く。

    ⚠️ **返ってきた全件を一般母集団へ数える**（`discovery_population`）。
    候補として採らなかったものも数える——**捨てていたものを拾う**のが主旨で、
    通信は1本も増えない（おすすめタブ合意書§9-2-2）。
    """
    exclude_ids = exclude_ids or set()
    try:
        db = _get_db(db)
        client = _get_client(client, db)
    except Exception:
        # ⚠️ 取りにいけなかった（DB/クライアント初期化の失敗）。理由は_source_followingと同じ。
        logger.warning("discovery source status")
        raise

    # 純度フィルター（合意書§5のリスト2）。母集団もこれに従う（§9-2-1 ②）
    purity = db.get_setting("discovery_search_mode", "all") or "all"
    if purity not in ("all", "safe", "r18"):
        purity = "all"
    mode, page, audience = discovery_population.next_slice(db, purity)

    try:
        items = client.get_ranking(mode=mode, content="illust", page=page, lane="interactive")
    except Exception as exc:
        # ⚠️ **404を「そういうもの」として握りつぶさない**（フェーズ7-1-2）。
        # 無いページは何度叩いても無い。覚えないと毎回同じ穴を叩き、そのたびに
        # 母集団の仕入れが1回空振りする——蓄積を待っている先が3つあるので、
        # 空振りはリトライ3回の通信より重い。
        #
        # ⭐ 404＝本当に在庫が無い（このページは存在しないと確定した。通信の可否とは
        # 無関係の事実）。下の「0件も同じ扱いにする」コメントとも整合する——両方とも
        # 「取りにいけなかった」ではなく「このスライスには何も無いと判明した」側。
        # ⚠️ 404以外＝取りにいけなかった（通信断・タイムアウト・認証切れ等）。raiseする。
        if _is_not_found(exc):
            discovery_population.remember_page_exhausted(db, mode, page)
            logger.warning("discovery source status")
            return []
        logger.warning("discovery source status")
        raise

    # 応答は返ったが0件＝そのページは無い（ランキングの実ページは必ず埋まっている）。
    # 404と同じ扱いにする。片方だけ覚えると、404を返さないモードで回り続ける。
    if not items:
        discovery_population.remember_page_exhausted(db, mode, page)

    # ⚠️ 候補の絞り込みより**先に**数える。絞り込みで落ちたものこそ母集団の本体。
    added = discovery_population.record_ranking_items(db, items, audience)
    if added:
        logger.warning("discovery source status")

    try:
        judged_ids = db.get_judged_work_ids()
    except Exception:
        # ⚠️ 取りにいけなかった（DB読み取りの失敗）。_source_followingと同じ理由。
        logger.warning("discovery source status")
        raise

    out = []
    for item in items:
        wid = str(item.get("illust_id") or "")
        if not wid or wid in exclude_ids or wid in judged_ids:
            continue
        try:
            if db.get_work(wid):
                continue  # 既にダウンロード済み
        except Exception:
            logger.warning("discovery source status")
            continue
        out.append({
            "work_id": wid,
            "title": item.get("title") or "",
            "author_name": item.get("user_name") or "",
            "author_id": str(item.get("user_id") or ""),
            "thumbnail_url": item.get("url") or None,
            "tags": item.get("tags") or [],
            "source": "ranking",
            "bookmark_count": None,  # ranking.php はブックマーク数を返さない（閲覧数/評価数のみ）
        })
        if len(out) >= n:
            break
    return out


def _source_related_authors(n=20, exclude_ids=None, db=None, client=None, **_kwargs):
    """フォロー中作者の「隣にいる作者」の作品を候補として返す（フェーズ1、公式レコメンド）。

    **題材探索枠の主力**（合意書§11-3・§11-4）。フォロー新着は既知の作者・ほぼ既知の題材
    なので確実枠に、ランキングは未知の作者なので絵柄探索枠に入ってしまい、
    「作者は好みに合致・題材は未知」という組み合わせをどちらも作れなかった。
    作者＝絵柄の代理なので、**好きな作者の隣の作者が描いた知らない題材**がここで手に入る。

    1作者につき1リクエストで、関連作者10人ぶんの作品30件前後がタグ込みで返る
    （`get_recommended_users`）。`_source_following` と同じくカーソルでローテーションし、
    1回の呼び出しでは数人ぶんだけを見る。

    ⚠️ **既にフォローしている作者は除く。** その人の作品はフォロー新着から来るので、
    ここで拾うと同じものを二重に仕入れるだけで「隣を広げる」目的に反する。

    ⚠️ **公式レコメンドは「素材」としてだけ使う**（実装ブレスト§1）。そのまま並べて出すと
    公式の劣化コピーになる。この関数は候補を返すだけで、順序には一切関与しない
    （並べるのは discovery_ranking＝こちらの学習結果）。
    """
    exclude_ids = exclude_ids or set()
    try:
        db = _get_db(db)
        client = _get_client(client, db)
    except Exception:
        # ⚠️ 取りにいけなかった（DB/クライアント初期化の失敗）。理由は_source_followingと同じ。
        logger.warning("discovery source status")
        raise

    try:
        following = db.get_following_users(sort_by="follow_order", sort_order="asc")
    except Exception:
        # ⚠️ 取りにいけなかった（DB読み取りの失敗）。
        logger.warning("discovery source status")
        raise
    if not following:
        # ⭐ 本当に在庫が無い（フォロー中の作者が0人）。_source_followingと同じ理由。
        return []

    try:
        cursor = int(db.get_setting("discovery_related_cursor", "0") or 0) % len(following)
    except Exception:
        cursor = 0
    ordered = following[cursor:] + following[:cursor]
    targets = ordered[:_RELATED_MAX_SEEDS_PER_BATCH]
    try:
        db.set_setting("discovery_related_cursor", (cursor + len(targets)) % len(following))
    except Exception:
        pass

    try:
        judged_ids = db.get_judged_work_ids()
    except Exception:
        # ⚠️ 取りにいけなかった（DB読み取りの失敗）。_source_followingと同じ理由。
        logger.warning("discovery source status")
        raise
    followed_ids = {str(u.get("user_id")) for u in following if u.get("user_id")}

    out = []
    attempted = 0
    succeeded = 0
    for seed in targets:
        seed_id = seed.get("user_id")
        if not seed_id:
            continue
        attempted += 1
        try:
            result = client.get_recommended_users(str(seed_id), lane="background")
        except Exception:
            logger.warning("discovery source status")
            continue
        succeeded += 1

        # 既フォローの作者は落とす（フォロー新着と二重になるため）
        skip_authors = {u["user_id"] for u in result.get("users") or []
                        if u.get("is_followed")} | followed_ids

        for w in result.get("works") or []:
            wid = str(w.get("id") or "")
            author_id = str(w.get("user_id") or "")
            if not wid or wid in exclude_ids or wid in judged_ids:
                continue
            if w.get("is_masked"):
                continue  # 表示できない作品（非公開化等）。画像を見ないままの判定はさせない
            if not author_id or author_id in skip_authors:
                continue
            try:
                if db.get_work(wid):
                    continue  # 既にダウンロード済み
            except Exception:
                logger.warning("discovery source status")
                continue
            out.append({
                "work_id": wid,
                "title": w.get("title") or "",
                "author_name": w.get("user_name") or "",
                "author_id": author_id,
                "thumbnail_url": w.get("thumbnail_url") or None,
                "tags": w.get("tags") or [],
                "source": "related_author",
                # どのフォロー中作者の隣から来たか。並べ替え側が「絵柄は担保されている」と
                # 判断する根拠になる（作者そのものは未評価でも、好きな作者の隣なので）。
                "related_to": str(seed_id),
                "bookmark_count": None,  # recommends はブックマーク数を返さない
            })
            if len(out) >= n:
                return out
    if attempted > 0 and succeeded == 0:
        # ⚠️ 取りにいけなかった（全滅）。_source_followingと同じ理由。
        raise RuntimeError(f"発掘（関連作者）: 対象{attempted}人すべての関連作者取得に失敗しました。")
    return out


def _source_tag_search(n=20, exclude_ids=None, db=None, client=None, **_kwargs):
    """学習した「好きなタグ」で検索して候補を返す（フェーズ1.5、合意書§11-4の本命）。

    関連度が最も高く、**ランキングでは構造的に届かないニッチを拾える**
    （§12でランキングを品質の下限から降ろした、その受け皿）。

    ⚠️ **素直に作ると全部が確実枠に入る。** 好きなタグで引くのだから返ってくるのは
    既知の題材ばかりになる。題材探索枠へ回すには「**引いたタグは既知だが、他に未知の
    タグも併せ持つもの**」を優先して拾う必要がある——関連作者で「未知の作者だが絵柄は
    既知」を見たのとまったく同じ構図。ここでは `_UNKNOWN_TAG_MIN` 個以上の未知タグを
    持つものを先に並べて返す（枠の判定そのものは discovery_ranking 側が行う）。

    ⚠️ **汎用タグでは検索しない**（§5-1）。『オリジナル』『女の子』で引いても候補が
    絞れず、リクエストを捨てることになる。

    ⚠️ **上位1タグに固定しない**（プールが痩せるため）。`discovery_search_cursor` で
    上位N件をローテーションする（`_source_following` と同じ考え方）。
    """
    exclude_ids = exclude_ids or set()
    try:
        db = _get_db(db)
        client = _get_client(client, db)
    except Exception:
        # ⚠️ 取りにいけなかった（DB/クライアント初期化の失敗）。理由は_source_followingと同じ。
        logger.warning("discovery source status")
        raise

    from . import discovery_ranking
    from .legacy.database import normalize_tag

    try:
        preferences = db.get_discovery_preferences()
        total_works = db.conn.execute(
            "SELECT COUNT(*) FROM works WHERE is_deleted = 0").fetchone()[0]
    except Exception:
        # ⚠️ 取りにいけなかった（DB読み取りの失敗）。
        logger.warning("discovery source status")
        raise

    rows = discovery_ranking.search_tag_candidates(
        preferences, total_works, get_setting=db.get_setting, limit=_SEARCH_TAG_POOL)
    if not rows:
        # ⭐ 本当に在庫が無い（学習した好きなタグがまだ無い＝典型的な初回ユーザー状態）。
        # DB自体は正常に読めており「取りにいけなかった」ではない。
        logger.warning("discovery source status")
        return []

    try:
        cursor = int(db.get_setting("discovery_search_cursor", "0") or 0) % len(rows)
    except Exception:
        cursor = 0
    ordered_rows = rows[cursor:] + rows[:cursor]
    targets = ordered_rows[:_SEARCH_TAGS_PER_BATCH]
    try:
        db.set_setting("discovery_search_cursor", (cursor + len(targets)) % len(rows))
    except Exception:
        pass

    # 純度フィルター（合意書§5のリスト2）。区分として常時オン/オフするもので、
    # 判定のついでに決める性質ではないため設定から読む。UIは第2段。
    mode = db.get_setting("discovery_search_mode", "all") or "all"
    if mode not in ("all", "safe", "r18"):
        mode = "all"

    try:
        judged_ids = db.get_judged_work_ids()
    except Exception:
        # ⚠️ 取りにいけなかった（DB読み取りの失敗）。_source_followingと同じ理由。
        logger.warning("discovery source status")
        raise
    known_tags = set(preferences.keys())

    fresh, familiar = [], []
    attempted = 0
    succeeded = 0
    for row in targets:
        word = row["display"]
        attempted += 1
        try:
            result = client.search_illusts(word, mode=mode, lane="background")
        except Exception:
            logger.warning("discovery source status")
            continue
        succeeded += 1

        for w in result.get("works") or []:
            wid = str(w.get("id") or "")
            if not wid or wid in exclude_ids or wid in judged_ids:
                continue
            if w.get("is_masked"):
                continue
            try:
                if db.get_work(wid):
                    continue  # 既にダウンロード済み
            except Exception:
                logger.warning("discovery source status")
                continue

            tags = w.get("tags") or []
            unknown = sum(1 for t in tags if (normalize_tag(t) or "") not in known_tags)
            candidate = {
                "work_id": wid,
                "title": w.get("title") or "",
                "author_name": w.get("user_name") or "",
                "author_id": str(w.get("user_id") or ""),
                "thumbnail_url": w.get("thumbnail_url") or None,
                "tags": tags,
                "source": "tag_search",
                "searched_tag": word,
                # ⚠️ ブックマーク数は検索レスポンスに含まれない（実レスポンスで確認済み）。
                # 仮に取れても軸には使わない——ニッチな良作は伸びず、AI作品は質と数が
                # 噛み合わないため（合意書§12）。表示に使う程度の参考に留める。
                "bookmark_count": None,
            }
            (fresh if unknown >= _UNKNOWN_TAG_MIN else familiar).append(candidate)

    if attempted > 0 and succeeded == 0:
        # ⚠️ 取りにいけなかった（全滅）。_source_followingと同じ理由。
        raise RuntimeError(f"発掘（タグ検索）: 対象{attempted}件すべてのタグ検索に失敗しました。")

    # 未知タグを併せ持つものを先に返す。確実枠に偏らせないための並べ方であって、
    # 最終的な順序は discovery_ranking が決める（候補元は順序に関与しない）。
    out = (fresh + familiar)[:n]
    logger.warning("discovery source status")
    return out


CANDIDATE_SOURCES = {
    "mock": _source_mock,
    "following": _source_following,
    "ranking": _source_ranking,
    "related_author": _source_related_authors,
    "tag_search": _source_tag_search,
}


class BatchResult:
    """`get_next_batch` の結果。⚠️⚠️ **リストではない。**

    ---

    ## なぜ要るか（フェーズ6-2-5）

    ⭐️ **「候補0件」には、意味の違う2つがある。**

    | | 呼び出し側が出すべき画面 |
    |---|---|
    | **本当に在庫が無い** | 「表示できる候補がありません」 |
    | ⚠️⚠️ **取りにいけなかった**（通信断・認証切れ） | **「候補を取りにいけませんでした」＋もう一度探す** |

    ⚠️ **以前はどちらも `[]` で返していた。** ⭐️ だから呼び出し側は区別できず、
    **通信が落ちているだけの人に「初回・学習データ無し」の画面を出していた**
    ——⚠️ **何百枚も判定した人に対しても**（`CLAUDE.md` §0）。

    ---

    ## ⚠️ わざとリスト互換にしていない

    `__iter__` も `__len__` も持たせない。⭐️ **見落とした呼び出し元があれば、
    「空のリスト」として黙って通るのではなく、その場で落ちる。**
    ⚠️ **黙って空扱いになるのが、いちばん危ない失敗のしかた。**
    """

    __slots__ = ("items", "ok", "failed")

    def __init__(self, items, ok, failed):
        self.items = list(items or [])
        #: 例外を出さずに返したソース名（0件を返したものも**成功**に数える）
        self.ok = list(ok or [])
        #: 例外を出したソース名
        self.failed = list(failed or [])

    @property
    def all_failed(self):
        """⚠️ **1本も成功していない。** ＝ いま候補の在庫は一切分かっていない。"""
        return bool(self.failed) and not self.ok

    @property
    def nothing_and_failed(self):
        """⚠️⚠️ **0件で、しかも失敗したソースがある。**

        ⭐️ **この場合「在庫が無い」と言い切ってはいけない。**
        一部だけ失敗（3本失敗＋1本が0件）でも、**見ていない棚がある**ので同じこと。
        """
        return not self.items and bool(self.failed)

    def __repr__(self):
        return (f"<BatchResult {len(self.items)}件 "
                f"成功={self.ok} 失敗={self.failed}>")


def get_next_batch(n=20, sources=None, exclude_ids=None, db=None, client=None):
    """次に見せる候補をまとめて取得します。⚠️ **返すのは `BatchResult`（リストではない）。**

    sources を省略した場合は CANDIDATE_SOURCES に登録済みの全ソース（"mock"含む）を使う。
    実データ取得時（following/ranking）はUIから db（database.Database インスタンス）を
    渡すことを想定しているが、省略時は各ソースが自前で Database()/PixivClient() を生成する
    （PixivClient.__init__ と同じ「db未指定なら自分で作る」流儀）。
    ソース側の失敗（ネットワーク断など）が他ソースやバッチ全体を巻き込まないよう、
    1ソースごとに例外を握りつぶして続行する。

    ⚠️⚠️ **握り潰しはそのまま**（1ソースの失敗で他を巻き込まないのは正しい設計）。
    ⭐️ **変えたのは「握り潰したことを呼び出し側へ伝えるかどうか」だけ**
    ——失敗したソース名を `BatchResult.failed` に残す（フェーズ6-2-5）。
    """
    names = sources if sources is not None else list(CANDIDATE_SOURCES)
    batch = []
    ok, failed = [], []
    for name in names:
        fn = CANDIDATE_SOURCES.get(name)
        if not fn:
            # 登録が無い名前。**失敗ではない**（そもそも試していない）ので数えない。
            continue
        try:
            batch.extend(fn(n=n, exclude_ids=exclude_ids, db=db, client=client))
        except Exception:
            logger.warning("discovery source status")
            failed.append(name)
        else:
            ok.append(name)
    random.shuffle(batch)
    if failed:
        logger.warning("discovery source status")
    return BatchResult(batch[:n], ok, failed)
