"""一般母集団（世間との比較）を、ランキングから細く長く貯める。

設計は `docs/RECOMMEND_TAB_AGREEMENT_2026-08-07.md` §9-2-1・§9-2-2 が正。
本モジュールは**貯めるところだけ**を担当する（使うのはフェーズ7-3、見せるのは7-2）。

---

## なぜ要るのか

`DISCOVERY_LEARNING_LOGIC_AGREEMENT_2026-08-06.md` §5-1 は「汎用タグ」を
**自分のライブラリ**を分母にして自動算出している。⚠️ **これだと自分の偏りが
そのまま分母に入る**——ウマ娘を1,210件保存していれば『ウマ娘』は自分の中で
汎用に見えるが、**世間では汎用ではない**。

外部の一般母集団と比べれば（自分の割合 ÷ 一般の割合）、
『オリジナル』『R-18』のような**本当の汎用タグだけ**が落ちる。

---

## ⚠️ 踏んではいけない3つ

### 1. 取る場所をローテーションする（このフェーズの生命線）

**日次ランキングの1ページ目は、その日ずっと同じ50件。**
何度取り直しても**母集団は1件も増えない**（1日50作品が上限）。

→ 日次／週次／月次 × ページ を輪番する。**1回のリクエスト数は変えない**
（＝細く長くのまま）が、**毎回違う50件が入る**。
既存の流儀と同じ——フォロー新着も1回の補充で2人ずつ輪番している。

### 2. 追加リクエストはゼロ

ランキングは**既に候補元として毎回叩いている**（`discovery_engine._source_ranking`）。
返ってくる作品にはタグが付いており、**候補として採らなかったものも捨てている**。
それを数えるだけで、通信は1本も増えない
（合意書§11-4「捨てていたものを拾う」パターンそのもの）。

⚠️ **だからこのモジュールは自分でネットワークを叩かない。**
`_source_ranking` が取ってきた応答を渡してもらう側に徹する。

### 3. 検索結果・自分の閲覧・ブックマークを入れない

**分母が自分と相関すると、リフトが1に近づいて何も見えなくなる。**
検索は「自分が探したもの」、閲覧・ブクマは「自分が選んだもの」なので、
**どちらも分母にできない**。ランキング由来だけを入れる。
"""

import logging

logger = logging.getLogger(__name__)


# --- 取る場所（スライス）の定義 ---------------------------------------------
#
# ⚠️ **ここに書いてよいのは、`pixiv_client.get_ranking` の docstring に実在が
# 明記されているモードだけ。** 記憶で増やさないこと（CLAUDE.md §7:
# 未知のAPIを使う前に実物で確認する）。
_SFW_MODES = ("daily", "weekly", "monthly")
_R18_MODES = ("daily_r18",)

# 1モードあたり何ページまで輪番するか。ランキングは1ページ最大50件なので、
# 5ページ＝上位250作品ぶんを順に舐める。
#
# ⚠️ **モードによっては5ページも無い。** 実際 `daily_r18` の5ページ目は404を返し、
# リトライ3回ぶん無駄に叩いたうえ、**その回の母集団の仕入れが空振り**していた
# （2026-08-07・フェーズ7-1-2）。空振りのほうが重い——7-1の蓄積を待っている先が
# 3つあり（仕入れ側の探索／おすすめの探索枠の根拠行／リフトの開始）、全部が遅れる。
#
# ⚠️⚠️ **ここに「daily_r18 は2ページ」のような数字を書かない。**
# pixivが返すページ数はモードと日によって変わるうえ、**実物で確認せずに書いた数字は
# 記憶で書いたのと同じ**（CLAUDE.md §7）。少なく書けば取れるものを取り逃がし、
# 多く書けばまた404を叩く。**尽きたと分かった時点で覚える**のが本筋。
_PAGES_PER_MODE = 5

# 学習したページ上限の置き場（設定キーの接頭辞）。モードごとに1つ。
# 値は `"<上限>|<覚えた日>"` の形で持つ（日付が要る理由は下記）。
_PAGE_LIMIT_PREFIX = "discovery_population_max_page_"


def _today():
    """上限の有効期限を測る単位。**日付だけ**（時刻は見ない）。"""
    from datetime import date
    return date.today().isoformat()


def get_page_limits(db, today=None):
    """**今日ぶんの**ページ上限 {mode: 最大ページ} を返す。無ければ空の辞書。

    ⚠️ **上限は1日で失効する**（フェーズ7-1-2-b）。日付の違うものは無かった扱いにする。
    値が日付を持たない古い形（フェーズ7-1-2 の最初の実装）も、**失効扱い**にして
    今日また学び直させる——古い恒久値がそのまま残るほうが害が大きい。
    """
    today = today or _today()
    limits = {}
    for mode in set(_SFW_MODES) | set(_R18_MODES):
        try:
            raw = str(db.get_setting(_PAGE_LIMIT_PREFIX + mode, "") or "")
        except Exception:
            raw = ""
        if "|" not in raw:
            continue  # 日付を持たない＝古い形。今日また学び直す
        value_s, _, stamp = raw.partition("|")
        if stamp != today:
            continue  # 日が変わった。忘れる
        try:
            value = int(value_s)
        except (TypeError, ValueError):
            continue
        if value >= 1:
            limits[mode] = value
    return limits


def remember_page_exhausted(db, mode, page, today=None):
    """「このモードは page ページ目が無かった」を**今日ぶん**覚える。次から叩かない。

    ⚠️ **404を「そういうもの」として握りつぶさないこと**（フェーズ7-1-2）。
    覚えないと毎回同じ穴を叩き、そのたびに母集団の仕入れが1回空振りする。

    ⚠️ **1ページ目では覚えない。** 上限を0にすると**そのモードが丸ごと消える**。
    1ページ目が取れないのはページ数の問題ではなく、通信か認証の問題なので、
    ここで永続化してはいけない（一時的な失敗を恒久的な設定にしてしまう）。

    ⚠️⚠️ **覚えるのは今日ぶんだけ**（フェーズ7-1-2-b）。ランキングは日が変われば
    中身もページ数も変わる。**0件は404より弱い証拠**で、「そのページは無い」ではなく
    「今日は無い」かもしれない。恒久的に縮めると**そのページを二度と見に行かなくなり、
    いま直している「母集団が伸びない」を別の形で作り直す**ことになる。

    ⚠️ **時計で細かく制御しない**（7-2「歯止めに時計を使わない」と同じ規律）。
    **日付が変わったら忘れる**だけにしてある——日次ランキングの中身が変わる単位と一致する。
    404と0件で扱いを分けてもいない。分ければ規則が2つになり、
    得られるのは「1日あたり最大1回の404を減らせる」だけで、割に合わない。

    戻り値: 覚えたら新しい上限、覚えなかったら None。
    """
    today = today or _today()
    try:
        page = int(page)
    except (TypeError, ValueError):
        return None
    if page <= 1 or mode not in (set(_SFW_MODES) | set(_R18_MODES)):
        return None
    limit = page - 1
    current = get_page_limits(db, today).get(mode)
    if current is not None and current <= limit:
        return None  # 今日、既にもっと狭い上限を知っている
    try:
        db.set_setting(_PAGE_LIMIT_PREFIX + mode, f"{limit}|{today}")
    except Exception:
        logger.warning("一般母集団: ページ上限を保存できませんでした。", exc_info=True)
        return None
    logger.info(
        f"一般母集団: {mode} は今日 {limit} ページまでと分かりました（日が変わるまで叩きません）。")
    return limit


def build_slices(purity_mode="all", page_limits=None):
    """輪番する「取る場所」の一覧を (mode, page, audience) で返す。

    `purity_mode` は純度フィルター（`discovery_search_mode`）の値: "all" | "safe" | "r18"。
    `page_limits` は学習済みのページ上限 {mode: 最大ページ}（`get_page_limits`）。

    ⚠️ **純度フィルターに従う**（合意書§5-2・§9-2-1 ②）。
    R-18を表示しない設定なら、R-18のスライスは**取りに行かない**——
    分母としても要らないし、候補としても出せないため、通信の無駄になる。
    """
    modes = []
    if purity_mode != "r18":
        modes += [(m, "sfw") for m in _SFW_MODES]
    if purity_mode != "safe":
        modes += [(m, "r18") for m in _R18_MODES]
    if not modes:  # 想定外の値。SFWへ寄せる（安全側）
        modes = [(m, "sfw") for m in _SFW_MODES]

    page_limits = page_limits or {}
    slices = []
    for page in range(1, _PAGES_PER_MODE + 1):
        for mode, audience in modes:
            # 学習済みの上限を超えるページは、そもそも輪番に入れない
            if page > page_limits.get(mode, _PAGES_PER_MODE):
                continue
            slices.append((mode, page, audience))
    return slices


def next_slice(db, purity_mode="all"):
    """次に取るスライスを1つ返し、カーソルを1つ進める。

    ⚠️ **カーソルはDBへ持たせる**（アプリを閉じても続きから回る）。
    `discovery_engine._source_tag_search` が `discovery_search_cursor` でやっているのと
    同じ流儀に揃えてある。

    ⚠️ 上限を学習すると輪番の並びが縮むので、カーソルの指す先が1回ずれる。
    **順序が変わるだけで、取り逃がしにはならない**（次の周で必ず回ってくる）。

    戻り値: (mode, page, audience)
    """
    slices = build_slices(purity_mode, get_page_limits(db))
    if not slices:  # 全モードの上限が消えるのは想定外。上限を無視して回す（止めない）
        slices = build_slices(purity_mode)
    cursor = 0
    try:
        cursor = int(db.get_setting("discovery_population_cursor", "0") or 0)
    except Exception:
        cursor = 0
    cursor %= len(slices)
    try:
        db.set_setting("discovery_population_cursor", (cursor + 1) % len(slices))
    except Exception:
        pass
    return slices[cursor]


# --- 忘却（減衰）--------------------------------------------------------------
#
# ⚠️ **名前付き定数にする。ユーザー設定にしない**（合意書§9-2-1 ④）。
# 「何日で忘れますか」と聞かれて答えられる材料がユーザーに無く、しかも間違えると
# **静かに壊れる**——短すぎれば分母が貯まる前に消えてリフトが永久に効かず、
# 長すぎれば何年も前の流行が分母に残り続ける。どちらも画面には何も出ない。
# 前例は `SEED_SCALE`（合意書§8-1）。あれもつまみだが、ユーザーには見せず実測で決めた。
#
# 代わりに**状態を見せる**: 「世間との比較: ◯◯ 時点 ・ 作品 N 件から集計」（フェーズ7-2）。
POPULATION_DECAY_AT = 20000      # 数えた作品がこれを超えたら
POPULATION_DECAY_FACTOR = 0.5    # 過去の集計をこの倍率へ落とす


def record_ranking_items(db, items, audience):
    """ランキングの応答をそのまま母集団へ数え入れる。

    `items` は `pixiv_client.get_ranking` の戻り（`illust_id` / `tags` を持つ辞書のリスト）。
    **候補として採ったかどうかに関わらず、返ってきた全件を数える**——
    捨てていたものを拾うのがこのフェーズの主旨。

    ⚠️ ここで例外を投げないこと。母集団の集計は**おまけ**であって、
    失敗しても候補取得（学習タブの本業）を巻き添えにしてはいけない。
    """
    works = []
    for item in items or []:
        wid = str((item or {}).get("illust_id") or "")
        if not wid:
            continue
        works.append({"work_id": wid, "tags": (item or {}).get("tags") or []})
    if not works:
        return 0

    try:
        return db.record_population_works(
            works, audience,
            decay_at=POPULATION_DECAY_AT,
            decay_factor=POPULATION_DECAY_FACTOR,
        )
    except Exception:
        logger.warning("一般母集団: 集計に失敗しました（候補取得は続行します）。", exc_info=True)
        return 0
