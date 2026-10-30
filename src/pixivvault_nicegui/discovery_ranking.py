"""学習タブの並べ替え（実装仕様③／合意書§9・§11）。

**このモジュールは何も保存しない。** DBが持つのは生カウントだけで、幅・順位・探索枠の
割り当てはすべて「読むときに」ここで計算する（生カウントと履歴を正とし、派生値は
キャッシュしない）。係数を変えれば過去の判定も含めて全部が計算し直される。

用語:
    幅          各タグ・各作者の「好み度」の信頼区間。判定が少ないほど広い
    下限        「少なくともこれくらいは好き、と言い切れる線」。表示にも並べ替えにも使う
    上限        「化ける可能性」。探索枠の並べ替えに使う（§11-1）

⚠️ 表示と並べ替えの両方で下限を使うのが要点（§9-2）。実測値を表示して統計的な基準で
順位を止めると「バーが長いほうが下にいる」状態が起き、ユーザーには不具合に見える。
バーが指す数字のほうを下限に変えることで、**バーの長さの順に並べるだけで順位が決まる**。
"""

import math
from collections import Counter, deque

# --- 係数はここ1箇所だけに置く（実装仕様③-3-2） -----------------------------
# 値は (票の重さ, 好意度)。「票の重さ」は露出の分母へ、「票の重さ×好意度」は分子へ入る。
#
# 順序関係だけが合意事項（合意書§2-2）: 加点は like < superLike、減点は skip < hate、
# そして skip は hate よりずっと軽い（skip は拒否ではなく「今日の気分ではない」）。
JUDGMENT_WEIGHTS = {
    "superlike_count": (2.0, 1.0),   # 大きくプラス。iOS版が作者へ2票入れるのと揃える
    "like_count":      (1.0, 1.0),   # 基準
    "skip_count":      (1.0, 0.15),  # 票は1だが、好意度はわずかしか下がらない
    "hate_count":      (2.0, 0.0),   # 重い減点
    # 既存資産は判定と別カウントで持っている（合意書§7-3）。混ぜていないので、
    # 「保存履歴の効きが強すぎた／弱すぎた」と後から気づいてもここだけで調整できる。
    # ⚠️ 生の回数ではなく SEED_SCALE で飽和させた値を使う（_damped_seed 参照）
    "seed_saved":      (1.0, 1.0),   # ディスクを使ってまで残した＝いいね相当
    "seed_bookmarked": (0.8, 1.0),   # pixiv側に残した
}

# 作者だけが持つ列。フォロー中である事実は「この人は見続けたい」の表明（合意書§8で強さ「中」）
AUTHOR_ONLY_WEIGHTS = {
    "seed_following": (1.0, 1.0),
}

# 作者は観測回数と効きの強さを分け、中立の事前情報から平滑化する（裁定#174）。
AUTHOR_JUDGMENT_WEIGHTS = {
    "superlike_count": (2.0, 1.0),
    "like_count":      (1.0, 1.0),
    "skip_count":      (0.25, 0.0),
    "hate_count":      (1.0, 0.0),
    "seed_saved":      (1.0, 1.0),
    "seed_bookmarked": (0.8, 1.0),
    "seed_following":  (1.0, 1.0),
}
KAPPA_BASE = 4.0  # 必ず正。0では未知作者の分母が0になる。
AUTHOR_QUOTA_WINDOW = 30
AUTHOR_QUOTA_RATIO = 0.15
AUTHOR_UNKNOWN_THRESHOLD = 2.0

# 「合わない・絵柄」の扱いは2段階（第4版・裁定#174 の追補・2026-09-18 PO決定）。
#
# ⚠️ 1回の Hate で作者を確定させない（合意書§2-2「Hateは作者に1票」）。無条件で最後尾へ
# 固定した第3版は、いいね50回・SuperLike10回の作者（作者軸0.9600）でも1回の誤タップで
# 未知候補より後ろへ回り、**Undoが直前1手しか戻せない以上、回復手段が無かった**。
#   tier1（1回）  探索枠からは外すが、確実枠では減点付きのスコア順に残す＝また出てくる
#   tier2（2回〜） 探索枠から外し、最終列でも通常候補の後ろへ固定する＝完全排除
#
# ⚠️ 減点はスコアの値そのものではなく**並べ替えキーの中だけ**へ入れる。`info["score"]` は
# `discovery_tab.py` の先読みヒステリシスも読む値で、嘘を入れると別の機能の判断が狂う。
#
# 0.10 は2つの不等式から決めた（合成スコアで比較・`docs/design/
# DISCOVERY_AUTHOR_DIVERSITY_PLAN_A_2026-09-17.md` §11-3）:
#   好かれた作者(0.9562)が、いいね2回だけの作者(0.8095)に負けない  → P < 0.1467
#   拒否1回だけの作者(0.6762)が、見送り10回の作者(0.6300)より下へ  → P > 0.0462
AUTHOR_REJECT_CONFIRMED = 2
AUTHOR_REJECT_PENALTY = 0.10

SEED_COLUMNS = ("seed_saved", "seed_bookmarked", "seed_following")

# 既存資産の飽和の強さ。**これが無いと判定が永久に既存資産へ勝てない。**
#
# 2026-08-06、本番の実データ（保存済み15,797件）で確認した問題:
# 既存資産は全部が肯定（否定が1件も無い）ため好み度が1.0に張り付き、しかも回数がそのまま
# 効くと `r-18` が n=9803、`ウマ娘` が n=1210 になる。この状態では
#   ・下限がほぼ全タグ99.9〜100%になり、傾向パネルのバーが意味を成さない
#   ・ウマ娘を動かすのに1210回の見送りが要る＝判定が候補に効かない
# となり、合意書§8「既存データは"これまでの"好みであり"いまの"好みではない。判定が
# 上書きしていく」も、③の目的（出てくるものが自分に寄ってくる）も成立しない。
#
# そこで対数で飽和させ、**多いほど強いが、増えるほど伸びが鈍る**形にする:
#   保存1件 → 0.9票 / 8件 → 4.4票 / 100件 → 13票 / 1,210件 → 23票 / 9,803件 → 31票
# 9,803:1 の開きが 35:1 まで縮み、十数回の判定で上書きできる重さに収まる。
# 順位（どのタグが厚いか）は保たれるので、既存資産を「土台」として活かす狙いは変わらない。
# SEED_SCALE を小さくするほど早く飽和する（既存資産が軽くなる）。大きくすると
# 生の回数に近づく。曲線は自然対数（math.log1p）で固定で、**強さの調整はこの1つの
# 数字だけで行う**——底を変えるのと SEED_SCALE を変えるのは同じ操作になるため、
# つまみを2つ持たない。
#
# 2026-08-07: 4.0 → 2.0（フェーズ3-1・合意書§8-1は更新済み）。
# 4.0 のままだと**20枚SuperLikeしても「寄った」と気づける差にならず、約40枚必要**だった。
# 原因は既存資産(seed)の飽和——seedは全部が肯定なので下限が最初から天井に張り付き、
# 候補全体のタグ下限は平均0.829・上位10%の線が0.927＝**使える幅が0.10しかない**。
# 作品スコアはタグ8個前後の平均なので、20枚判定しても対象作品のスコアは +0.010 しか
# 動かず、上位10%へ届くのに必要な +0.044 の4分の1にしかならなかった。
#
# ⚠️ **1.0 ではなく 2.0 にした理由**: 1.0 だと5枚で頭打ちになり、既存資産の土台が
# ほぼ無意味になる。2.0 なら20枚で頭打ちに達し、合意書が掲げた「10〜20枚で寄る」を
# ちょうど満たす。
# ⚠️ **係数（JUDGMENT_WEIGHTS の superLike）はシロ。** SEED_SCALE=0 なら5枚で15件入る。
# ⚠️ **生カウント側は一切触らない**（合意書§8-1「適用場所が重要：生カウントは素の件数の
# まま保存し、読むときの解釈として対数を掛ける」）。数字1つで戻せる状態を保つこと。
SEED_SCALE = 2.0

# 「見送る」の票を薄める強さ（フェーズ3-3・合意書§8-1）。
#
# ⚠️ **判定するほど、そのタグの順位が下がる**という問題への対処。
# 1枚を見送る理由は、その作品が持つ8個前後のタグのうち**1個**なのに、
# **8個すべてに見送りが記録される**。結果、いいね164回の `r-18` が2,013位、
# 判定のあるタグ全体でも中央値2,912位まで沈んでいた。
#
# そこで「自分のライブラリでよく出てくるタグほど、見送り1回の重みを軽くする」:
#     薄め係数 = 1 / (1 + K × (そのタグの seed_saved ÷ works の総数))
# 既に大量に保存しているタグは「今日の気分ではない」で見送られる機会が多いだけなので、
# その1票を額面どおり引くと不当に沈む。逆に保存が少ないタグは薄まらない。
#
# ⚠️ **`hate_count` は薄めない。** 明示的な拒否を握りつぶさないため（合意書§8-1）。
# ⚠️ **生カウントは加工しない。** 薄めるのは**読むときの解釈**だけ
#    （§8-1「適用場所が重要：生カウントは素の件数のまま保存する」）。
# ⚠️ **分母は自分のライブラリでの出現率。** 一般母集団は使わない（測りたいものが違う）。
# ⚠️ **8 は実測で決めた値**（`tools/tune_negative_dilution.py` のスイープ）。
#    判定が増えたら再調整する前提の定数。
SKIP_DILUTION_K = 8.0

# 傾向パネルに出す最小の有効試行数。これ未満のタグは行にしない
# （合意書§9-4「空の輪郭パネルや0%のバーを出さない」の行単位版）。
TREND_MIN_N = 1.0

# 題材探索枠に入れる上限。**1タグあたりの平均**の有効試行数がこれ未満なら
# 「題材の判定材料が薄い」とみなす（合意書§11-2）。
# ⚠️ 実機の感触で調整する前提の暫定値。
SUBJECT_UNKNOWN_THRESHOLD = 8.0


def _damped_seed(count):
    """既存資産の回数を、飽和した「票数」へ変換します。"""
    if count <= 0:
        return 0.0
    return SEED_SCALE * math.log1p(count / SEED_SCALE)

# 幅の広さ。z=1.0 は合意書§9-3が挙げる実測値を再現する値:
#   判定10回ほどでいいね1回あたり約4.3ポイント、140回ほどで約0.30ポイント動く。
# z=1.96(95%)にすると序盤の下限が過度に沈み、ほぼ全タグが同じ位置に潰れて
# 「毎回、静かに伸び縮みする」が成立しなくなる。
Z = 1.0

# 判定材料がまったく無いときの扱い。0ではなく中立に置く——「低い」と「わからない」を
# 混ぜないため（合意書§11-1）。0にすると未知が「嫌い」と同じ場所へ沈む。
NEUTRAL = 0.5

# 候補1件のスコアの配分。画風タグ案の棄却（57b669c）により軸は2本だけになり、
# 画風軸が担っていた「絵柄が自分に合うか」は作者軸だけが背負う（合意書§12）。
SCORE_WEIGHTS = {"subject": 0.50, "author": 0.50}

# 探索枠の既定割合（合意書§11-2）。⚠️ ハードコードしない——設定値として上書きできる。
# 確実枠を厚くしてはいけない: 学習タブにとって確実枠は**情報量が最も低い枠**
# （すでに分かっていることの確認にしかならない）。ただし全部が未知だと見送りばかり
# 押す作業になって続かないため、気力の維持として3割は残す。
DEFAULT_RATIOS = {"sure": 0.30, "subject": 0.50, "style": 0.20}

# 同じ作者を続けて表示しないために、直近何人を避けるか。
AUTHOR_COOLDOWN = 3

# 呼び出し側が渡すべき「表示済み作者」の件数（第4版・2026-09-18）。
#
# ⚠️ **窓の定義を知っているのはこのモジュールだけにする。** 呼び出し側に `-1` や `max()` を
# 書かせると、片方の定数を変えたときにもう片方が置き去りになる——実際にそれが起きた:
# `discovery_tab.py` が `AUTHOR_COOLDOWN`（3件）ぶんしか履歴を渡しておらず、
# クォータの窓（29件）に26件足りないまま、**クォータが一度も発火していなかった**。
# 単体テストは「1回だけ並べ替えて先頭30件を数える」形だったので全部緑のまま、
# 実機では当初の症状（上位30枚中8枚＝27%が同一作者）がそのまま残っていた。
AUTHOR_HISTORY_NEEDED = max(AUTHOR_COOLDOWN, AUTHOR_QUOTA_WINDOW - 1)

# つまみは**2本だけ**（合意書§11-2「知らないジャンルを見る割合」「知らない絵柄を見る割合」）。
# 確実枠はその残りとして自動的に決まる。3本目を置いて合計を正規化する方式にすると、
# 片方を上げたときに確実枠が意図せず潰れる（実際、実装中に0.4%まで潰れた）。
RATIO_SETTING_KEYS = {
    "subject": "discovery_ratio_subject",
    "style": "discovery_ratio_style",
}

# 探索枠の合計の上限。確実枠を完全には潰させない——全部が未知だと見送りばかり押す作業に
# なって続かないため、気力の維持として一定は残す（合意書§11-2）。
MAX_EXPLORATION = 0.95

# 「作者そのものは未評価だが、絵柄は担保されている」と扱う候補元。
#
# 関連作者（フォロー中作者の公式レコメンド）はこちらから見れば未知の作者なので、
# 素直に数えると `author_n == 0` で**絵柄探索枠**へ落ちてしまう。しかし合意書§11-3が
# 題材探索枠の本命に挙げているとおり、**作者＝絵柄の代理**であり「好きな作者の隣」は
# 絵柄が近い。見られることは担保されていて、題材だけが未知——これが題材探索枠そのもの。
#
# 候補元は「どこから来たか」という事実だけを候補に付けて渡し、それをどう解釈して
# どの枠へ入れるかはこちら側で決める（候補元は順序に関与しない、という分担）。
AFFINITY_SOURCES = ("related_author",)

# --- 汎用タグ（合意書§5-1） -------------------------------------------------
# 『オリジナル』『女の子』のように多くの作品に付いていて、除外すると候補が壊滅し、
# 検索に使っても候補が絞れないタグ。**PC版は既に大量の作品がローカルにあるので、
# 「このタグが全作品の何％に付いているか」を数えれば計算で分かる。**
#
# ⚠️ 線引きは高くしすぎないこと。外すのは**ライブラリの相当な割合に付いている
# 一握りのタグだけ**で、『猫耳』クラスの属性タグは普通に出す。線が高すぎると
# 「嫌いなタグがピルに出てこなくて除外できない」という事故が起きる。
GENERIC_PREVALENCE = 0.15

# 計算で拾えないものだけ手で足す（§5-1）。実ライブラリでは『女の子』9.3%・
# 『漫画』7.8% のように、割合では『催眠』8.3%（＝普通に検索したい題材タグ）と
# 区別が付かないものがある。性質が「題材ではない」ものをここに置く。
# ⚠️ ユーザーが設定で追加・削除できるようにすること（§5-2）。
DEFAULT_GENERIC_EXTRA = (
    "オリジナル", "女の子", "男の子", "おんなのこ", "イラスト", "漫画", "まんが",
    "落書き", "らくがき", "オリキャラ", "創作", "自作", "art", "illustration",
    "original", "R-18", "r-18", "r18",
)
GENERIC_EXTRA_SETTING_KEY = "discovery_generic_tags_extra"


def generic_tags(preferences, total_works, prevalence=GENERIC_PREVALENCE, extra=None):
    """汎用タグの集合を算出します（正規化済みタグ）。

    `preferences` の `seed_saved`（保存済み作品に何回出てきたか）を `total_works` で
    割った割合が `prevalence` 以上のものを汎用とみなし、手動リスト `extra` を足す。

    ⚠️ **算出結果は必ずユーザーへ提示して確認を取ること**（§5-2、PO要請）。
    自動算出にする以上、中身が見えないと怖い。
    """
    from .legacy.database import normalize_tag as _norm

    out = set()
    for raw in (extra if extra is not None else DEFAULT_GENERIC_EXTRA):
        n = _norm(raw)
        if n:
            out.add(n)
    if total_works and total_works > 0:
        for tag, counts in (preferences or {}).items():
            seed = counts.get("seed_saved") or 0
            if seed / total_works >= prevalence:
                out.add(tag)
    return out


def resolve_generic_extra(get_setting=None):
    """手動の汎用タグ一覧を設定から読みます（1行1タグ）。未設定なら既定を返す。"""
    if not get_setting:
        return list(DEFAULT_GENERIC_EXTRA)
    raw = get_setting(GENERIC_EXTRA_SETTING_KEY, None)
    if raw is None or str(raw).strip() == "":
        return list(DEFAULT_GENERIC_EXTRA)
    return [line.strip() for line in str(raw).splitlines() if line.strip()]


def _seed_based_tag_candidates(preferences, total_works, generic, exclude, limit):
    """判定材料がまだ少ないタグ検索候補を、既存資産（seed）ベースの下限で補います。

    2026-08-23・不具合2修正（量反応スコアへの変更）の副作用対応（PO決定・選択肢2）。
    `trend_rows()`は判定（like/superlike/skip/hate）のみを見る設計へ変えたため、
    判定を一度もしていないユーザーは`trend_rows()`ベースの候補が0件になる。
    タグ検索機能はそれでは動かせないので、ここだけは従来どおり`preference_bounds()`
    （`seed_saved`・`seed_bookmarked`を含むWilson下限）でタグを拾い、判定材料が
    育つまでの「土台」を保つ（合意書§8「既存データは土台として活かす」）。
    """
    rows = []
    for tag, counts in (preferences or {}).items():
        if tag in generic or tag in exclude:
            continue
        lower, upper, n = preference_bounds(counts, total_works=total_works)
        if n <= 0:
            continue
        rows.append({
            "tag": tag,
            "display": counts.get("display_tag") or tag,
            # ⚠️ ここの`score`はWilson下限（0〜1）で、`trend_rows()`が返す量反応スコアとは
            # 尺度が違う。両者を混ぜてソートし直すことはしない（この関数の中でしか使わない）。
            "score": lower,
            # capの概念が無い(Wilson下限ベース)ので常にFalse。UI側は`row.get("at_cap")`で
            # 読む前提（trend_rows由来の行と混在してもキー欠落で落ちないようにする）。
            "at_cap": False,
            "like_count": (counts.get("like_count") or 0) + (counts.get("superlike_count") or 0),
            "shown_count": counts.get("shown_count") or 0,
            "skip_count": counts.get("skip_count") or 0,
            "hate_count": counts.get("hate_count") or 0,
        })
    rows.sort(key=lambda r: (-r["score"], -r["shown_count"], r["tag"]))
    return rows[:limit]


def search_tag_candidates(preferences, total_works, get_setting=None, limit=20):
    """タグ検索に使う「好きなタグ」を返します（汎用タグを除く）。

    ⚠️ 汎用タグで検索しても候補が絞れず、リクエストを捨てることになる。
    §5-1の判定を、除外フィルタだけでなく**検索タグの選定にも使う**。

    優先順位: (1) 判定材料がある分は`trend_rows()`の量反応スコア順（画面の傾向パネルと
    食い違わせない）。(2) それだけで`limit`に届かない場合は、既存資産（seed）ベースの
    下限で残りを補う（2026-08-23・PO決定・`_seed_based_tag_candidates`参照）。
    """
    generic = generic_tags(preferences, total_works,
                           extra=resolve_generic_extra(get_setting))
    out = []
    for row in trend_rows(preferences, total_works=total_works):
        if row["tag"] in generic:
            continue
        out.append(row)
        if len(out) >= limit:
            break
    if len(out) < limit:
        seen = {r["tag"] for r in out}
        out.extend(_seed_based_tag_candidates(
            preferences, total_works, generic, seen, limit - len(out)))
    return out


def wilson_bounds(successes, n, z=Z):
    """好意度の下限・上限を返します。

    判定が少ないほど幅が広く、重ねるほど狭くなる。n=0（判定材料ゼロ）は
    「わからない」を表す (NEUTRAL, 1.0, 0.0) を返す——下限を0にすると
    未知が「嫌い」と同じ場所へ沈むため（合意書§11-1）。
    """
    if n <= 0:
        return (NEUTRAL, 1.0, 0.0)
    p = successes / n
    p = min(1.0, max(0.0, p))
    denom = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = (z / denom) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, center - margin), min(1.0, center + margin), n)


def skip_dilution_factor(counts, total_works):
    """「見送る」の票を薄める係数（フェーズ3-3・`SKIP_DILUTION_K` の注記）。

    ⚠️ `total_works` を渡さない（None / 0）と **1.0＝薄めない**。
    既存の道具・テストが引数なしで呼んでも従来どおりの値になるようにしてある。
    """
    if not total_works or total_works <= 0:
        return 1.0
    df = (counts.get("seed_saved") or 0) / total_works
    return 1.0 / (1.0 + SKIP_DILUTION_K * df)


def _author_organic_count(counts):
    """seed・重みを含めず、作者の判定4列だけを数えます。"""
    counts = counts or {}
    return float(sum(max(0, counts.get(column) or 0) for column in
                     ("like_count", "superlike_count", "skip_count", "hate_count")))


def _author_has_prior(counts):
    """判定以外に、保存・ブックマーク・フォローの事前情報があるか。"""
    counts = counts or {}
    return any((counts.get(column) or 0) >= 1 for column in SEED_COLUMNS)


def author_bounds(counts):
    """作者の (事後平均, 事後平均＋Z×事後SD, 生の判定件数) を返します。"""
    counts = counts or {}
    n = s = 0.0
    for column, (vote, favor) in AUTHOR_JUDGMENT_WEIGHTS.items():
        c = max(0, counts.get(column) or 0)
        if column in SEED_COLUMNS:
            c = _damped_seed(c)
        n += vote * c
        s += vote * favor * c
    a = KAPPA_BASE / 2 + s
    b = KAPPA_BASE / 2 + (n - s)
    score = a / (a + b)
    upper = min(1.0, score + Z * math.sqrt(a * b / ((a + b) ** 2 * (a + b + 1))))
    return score, upper, _author_organic_count(counts)


def preference_bounds(counts, include_author_only=False, total_works=None):
    """1件ぶんの生カウント辞書から (下限, 上限, 有効試行数) を求めます。

    `total_works` を渡すと `skip_count` の票だけを薄めてから足す（フェーズ3-3）。
    ⚠️ 薄めるのは**読むときの解釈**だけで、生カウントには触らない。
    """
    if include_author_only:
        return author_bounds(counts)
    weights = dict(JUDGMENT_WEIGHTS)
    factor = skip_dilution_factor(counts, total_works)
    n = 0.0
    s = 0.0
    for column, (vote, favor) in weights.items():
        c = counts.get(column) or 0
        if not c:
            continue
        if column in SEED_COLUMNS:
            c = _damped_seed(c)
        # ⚠️ 薄めるのは skip だけ。hate は明示的な拒否なので額面どおり引く
        if column == "skip_count":
            c *= factor
        n += vote * c
        s += vote * favor * c
    return wilson_bounds(s, n)


def build_bounds(preferences, include_author_only=False, total_works=None):
    """{キー: 生カウント} を {キー: (下限, 上限, n)} へ変換します。"""
    return {
        key: preference_bounds(counts, include_author_only=include_author_only,
                               total_works=total_works)
        for key, counts in (preferences or {}).items()
    }


# 「好みの傾向」並び順の量反応スコア(2026-08-23・不具合2修正・PO決定)。
#
# Wilson下限は「判定が少なくても全部好意的なら統計的に強く言い切れる」という設計上
# 正しい挙動だったが、PO所感（いいねを重ねても順位が上がって見えない）とズレていたため、
# 「重ねるほど素直に上がる」量反応の式へ変更した(docs/DISCOVERY_TREND_SCORE_SPEC_2026-08-23.md)。
#
# ⚠️ cap（いいね相当の頭打ち）が無いと、露出の多いタグ（例: `R-18`）がいいね数の絶対量だけで
# 永続的に上位を独占する——`.claude/rules/app-code.md`「レーン/キューの飢餓」と同型のリスク。
# 40 は実データ（開発DB・2026-08-23）で決めた: 25では「上限がもっと上でも良い」とPO所感が
# あったため、`R-18`が1位を奪う境界（実測で約48）を守りつつ、より高いcapで量への反応幅を
# 広げる方向で再探索した。40では`R-18`は上位5件には現れる（実効いいね71+超えのため）ものの
# 1〜4位（学園アイドルマスター23点等）は奪わない——safety margin約8。判定が増えて分布が
# 変わったら再調整する前提の定数。
TREND_SCORE_CAP = 40


def _effective_likes(counts):
    """`superlike_count`の重み×2を掛けた「いいね相当」の生量です（capを掛ける前）。

    `JUDGMENT_WEIGHTS`の票の重さ（2.0）に合わせた（合意書§2-2「加点はlike<superLike」の
    順序関係を保つため）。`tag_trend_score()`とMAXバッジ判定（`trend_rows()`の`at_cap`）
    の両方がこの値を基準にする——2箇所で式がズレないよう1箇所にまとめてある。
    """
    return (counts.get("like_count") or 0) + (counts.get("superlike_count") or 0) * 2


def tag_trend_score(counts, cap=TREND_SCORE_CAP):
    """1タグぶんの生カウントから、量反応スコアを返します。

    `skip_count`・`hate_count`は生カウントのまま使う（希薄化しない。
    docs/DISCOVERY_TREND_SCORE_SPEC_2026-08-23.md §7-1でレビュー済み）。
    既存資産（`seed_saved`・`seed_bookmarked`）はこのスコアに含めない
    （§7-2・PO承認済み: 「純粋に学習のみ」）。
    """
    return (min(_effective_likes(counts), cap)
            - (counts.get("skip_count") or 0)
            - (counts.get("hate_count") or 0) * 2)


def trend_rows(preferences, cap=TREND_SCORE_CAP, total_works=None, get_setting=None):
    """傾向パネル用の行を、量反応スコアの高い順に返します（2026-08-23・不具合2修正）。

    返すのは表示に必要な事実だけ: 表記・スコア・実際の回数。
    **合成スコア（◯点・◯%）は文字としては出さない**——読める文字はすべて事実で、
    計算結果はバーの長さだけ（§9-5）。

    ⚠️ 同点の第2キーを必ず決めること。決めないと実行のたびに順序が変わる。
    ここでは (スコア 降順, 表示回数 降順, タグ 昇順) の3段で完全に決定的にしている。

    行に出す条件（合意書§9-4「空の輪郭パネルを出さない」の行単位版）: そのタグへの
    生の判定（like+superlike+skip+hate）が1回以上ある。判定が一切無い（表示されただけ・
    既存資産のみの）タグは出さない。

    2026-08-23・汎用タグ除外(PO決定): `search_tag_candidates()`と同じ`generic_tags()`
    （§5-1）で汎用タグ（「女の子」等）を除外する。`total_works`が無くても`extra`の
    手動リスト（`DEFAULT_GENERIC_EXTRA`）は効く——出現割合ベースの自動判定だけが
    `total_works`無しでは働かない（呼び出し元がまだ持っていない場合の劣化動作）。
    """
    generic = generic_tags(preferences, total_works, extra=resolve_generic_extra(get_setting))
    rows = []
    for tag, counts in (preferences or {}).items():
        if tag in generic:
            continue
        judged = ((counts.get("like_count") or 0) + (counts.get("superlike_count") or 0)
                  + (counts.get("skip_count") or 0) + (counts.get("hate_count") or 0))
        if judged <= 0:
            continue
        rows.append({
            "tag": tag,
            "display": counts.get("display_tag") or tag,
            "score": tag_trend_score(counts, cap=cap),
            # MAXバッジ判定用(2026-08-23・PO要望)。「いいね相当」がcapに達している＝
            # これ以上いいねを重ねてもスコアは伸びない状態であることをユーザーへ示す。
            "at_cap": _effective_likes(counts) >= cap,
            "like_count": (counts.get("like_count") or 0) + (counts.get("superlike_count") or 0),
            "shown_count": counts.get("shown_count") or 0,
            "skip_count": counts.get("skip_count") or 0,
            "hate_count": counts.get("hate_count") or 0,
        })
    rows.sort(key=lambda r: (-r["score"], -r["shown_count"], r["tag"]))
    return rows


def _normalized_tags(candidate, normalize):
    seen = []
    for raw in candidate.get("tags") or []:
        t = normalize(raw) if normalize else raw
        if t and t not in seen:
            seen.append(t)
    return seen


def score_candidate(candidate, tag_bounds, author_bounds, normalize=None):
    """候補1件のスコアと、枠分けに使う内訳を返します。

    タグの下限の平均と作者の下限を合成する。判定材料が無いものは中立(0.5)。

    ⚠️ 序盤は作者側が先に飽和しない: タグは1作品に複数付くのでカウントが速く貯まるが、
    作者は1作品につき1件しか増えない。未評価を中立に置いているため序盤は実質タグ主導で
    始まり、作者の判定が貯まるほど効いてくる。これは意図した挙動。
    """
    tags = _normalized_tags(candidate, normalize)
    # ⚠️ 未知のタグ（まだ一度も観測していないもの）を平均から**外してはいけない**。
    # 外すと「既知タグ1つ＋未知タグ9つ」の作品が「既知タグ1つだけ」と同じ扱いになり、
    # 題材探索枠（＝題材の判定材料が薄いものを拾う枠）が機能しなくなる。
    # 未知は (中立, 1.0, 0) として数える——「低い」ではなく「わからない」（合意書§11-1）。
    unknown = (NEUTRAL, 1.0, 0.0)
    tag_vals = [tag_bounds.get(t, unknown) for t in tags]

    if tag_vals:
        subj_lower = sum(v[0] for v in tag_vals) / len(tag_vals)
        subj_upper = sum(v[1] for v in tag_vals) / len(tag_vals)
        # 枠分けには**1タグあたりの平均**を使う。合計だと「タグを10個付けた作品」が
        # それだけで判定材料が濃いことになり、作品ごとのタグ数の差で枠が決まってしまう。
        subj_n = sum(v[2] for v in tag_vals) / len(tag_vals)
    else:
        subj_lower, subj_upper, subj_n = NEUTRAL, 1.0, 0.0

    author_id = str(candidate.get("author_id") or "")
    auth_lower, auth_upper, auth_n = author_bounds.get(
        author_id, preference_bounds({}, include_author_only=True))

    w_s, w_a = SCORE_WEIGHTS["subject"], SCORE_WEIGHTS["author"]
    return {
        "score": w_s * subj_lower + w_a * auth_lower,
        # 探索枠は上限で並べる（同じ幅を下から見るか上から見るかで役割が変わる。§11-1）
        "upside": w_s * subj_upper + w_a * auth_upper,
        "subject_lower": subj_lower, "subject_n": subj_n,
        "author_lower": auth_lower, "author_n": auth_n,
        "author_organic_n": auth_n,
        "n": subj_n + auth_n,
    }


def resolve_ratios(get_setting=None):
    """探索枠の割合を設定から読んで返します。

    ⚠️ ハードコードしない（合意書§11-2）。ユーザーが2本のつまみで変えられる。
    設定値は**パーセント（0〜100）**で保存されている前提。確実枠は残りとして決まるので、
    つまみをどう動かしても3枠の合計は必ず1になる。
    """
    def _read(name, default_fraction):
        if not get_setting:
            return default_fraction
        try:
            raw = get_setting(RATIO_SETTING_KEYS[name], None)
            if raw is None or raw == "":
                return default_fraction
            v = float(raw) / 100.0
        except (TypeError, ValueError):
            return default_fraction
        return min(1.0, max(0.0, v))

    subject = _read("subject", DEFAULT_RATIOS["subject"])
    style = _read("style", DEFAULT_RATIOS["style"])

    total = subject + style
    if total > MAX_EXPLORATION:
        scale = MAX_EXPLORATION / total
        subject *= scale
        style *= scale
    return {"sure": 1.0 - subject - style, "subject": subject, "style": style}


def _author_rejected(author_id, author_preferences):
    """「絵柄が合わない」で下げた作者か。

    ⚠️ 探索枠でも避ける（合意書§11-2）。逸脱していいのは「まだ知らない絵柄」であって
    「嫌いだと言った絵柄」ではない。一緒くたにすると、はっきり拒否したものが再び出てきて
    判定した意味が消える。
    """
    counts = (author_preferences or {}).get(str(author_id or "")) or {}
    return (counts.get("hate_count") or 0) > 0


def author_reject_tier(author_id, author_preferences):
    """「合わない・絵柄」の段階を返します（0=通常 / 1=暫定拒否 / 2=確定拒否）。

    1回では確定させない（合意書§2-2「Hateは作者に1票」）。探索枠から外すのは
    tier1・tier2 の両方で、差が出るのは確実枠の中と最終列での扱いだけ。
    """
    counts = (author_preferences or {}).get(str(author_id or "")) or {}
    hate = counts.get("hate_count") or 0
    if hate <= 0:
        return 0
    return 2 if hate >= AUTHOR_REJECT_CONFIRMED else 1


def _author_reject_deferred(author_id, author_preferences):
    """最終列で通常候補の後ろへ回すか。

    tier2 は無条件。tier1 は**作者軸**が減点後も中立以上なら通常の流れに残す
    ——「累積した肯定的な証拠が中立を上回っているうちは実力どおりに扱う」。
    ⚠️ 合成スコアではなく作者軸で見る: 合成にはタグ側の事情が混ざるうえ、
    `finalize_display_order` はタグの情報を受け取らない（枠を跨いで一貫させるため）。
    ⚠️ ここを外すと、確実枠の在庫が拒否済みだけのときに先頭へ出る（`_interleave` は
    枠の割合で選ぶのでスコアを見ない）。T-0630 が入れた退行検査が守っている点。
    """
    tier = author_reject_tier(author_id, author_preferences)
    if tier >= AUTHOR_REJECT_CONFIRMED:
        return True
    if tier <= 0:
        return False
    counts = (author_preferences or {}).get(str(author_id or "")) or {}
    return author_bounds(counts)[0] - AUTHOR_REJECT_PENALTY < NEUTRAL


def assign_frames(candidates, tag_bounds, author_bounds, author_preferences=None,
                  normalize=None, subject_n_threshold=SUBJECT_UNKNOWN_THRESHOLD):
    """候補を3つの枠へ振り分け、それぞれ枠ごとの並びで返します（合意書§11-2）。

    | 枠 | 選び方 |
    |---|---|
    | 確実枠     | スコアが高い順。拒否済み作者は最後尾 |
    | 題材探索枠 | **作者は好みに合致**、題材タグの判定材料が薄いもの |
    | 絵柄探索枠 | **作者**の判定が少なく、事前情報もないもの |

    「絵柄が合わない」で下げた作者は両方の探索枠から除く（`_author_rejected`）。
    """
    scored = []
    for c in candidates or []:
        info = score_candidate(c, tag_bounds, author_bounds, normalize=normalize)
        scored.append((c, info))

    sure, subject, style = [], [], []
    for c, info in scored:
        author_id = c.get("author_id")
        info["author_reject_tier"] = author_reject_tier(author_id, author_preferences)
        info["author_rejected"] = info["author_reject_tier"] > 0
        if info["author_rejected"]:
            # tier1・tier2 とも探索枠からは外す（合意書§11-2 条件2）。差が出るのは
            # 確実枠の中の順位（下の `_key_lower`）と最終列での扱い（`finalize_display_order`）。
            sure.append((c, info))
            continue
        # 好きな作者の隣から来た候補は、作者が未評価でも「絵柄は担保されている」扱い。
        # 素直に author_n だけを見ると絵柄探索枠へ落ちるが、それでは題材探索枠が
        # いつまでも埋まらない（合意書§11-3）。
        vouched = c.get("source") in AFFINITY_SOURCES
        if vouched and info["subject_n"] < subject_n_threshold:
            subject.append((c, info))
            continue
        counts = (author_preferences or {}).get(str(author_id or "")) or {}
        if (info["author_organic_n"] < AUTHOR_UNKNOWN_THRESHOLD
                and not _author_has_prior(counts) and not vouched):
            style.append((c, info))
            continue
        if info["author_lower"] >= NEUTRAL and info["subject_n"] < subject_n_threshold:
            # 絵柄は合っているので「見られる」ことは担保されつつ、題材が未知なので
            # 判定に情報量がある。これが学習タブの主役の枠。
            subject.append((c, info))
            continue
        sure.append((c, info))

    def _key_lower(item):
        c, info = item
        tier = info.get("author_reject_tier") or 0
        # 確定拒否だけを最後尾へ固定し、暫定拒否は減点したスコアの順に残す。
        # ⚠️ 減点は**このキーの中だけ**。`info["score"]` は書き換えない。
        adjusted = info["score"] - (AUTHOR_REJECT_PENALTY if tier == 1 else 0.0)
        return (1 if tier >= AUTHOR_REJECT_CONFIRMED else 0,
                -adjusted, -info["n"], str(c.get("work_id") or ""))

    def _key_upper(item):
        c, info = item
        return (-info["upside"], -info["n"], str(c.get("work_id") or ""))

    sure.sort(key=_key_lower)
    subject.sort(key=_key_upper)
    style.sort(key=_key_upper)
    return {"sure": sure, "subject": subject, "style": style}


def _interleave(frames, ratios, limit=None):
    """枠ごとの並びを、指定の割合で1本の列へ混ぜます。

    「割合ぶん先頭から取って連結する」のではなく、**列全体にわたって混ぜる**。
    連結だと最初の3割が全部確実枠になり、体験としては「好きなものばかり並ぶ」に
    見えてしまうため。
    """
    pools = {k: list(v) for k, v in frames.items()}
    total = sum(len(v) for v in pools.values())
    if limit is None:
        limit = total
    limit = min(limit, total)

    out = []
    debt = {k: 0.0 for k in pools}
    while len(out) < limit:
        for k in pools:
            debt[k] += ratios.get(k, 0.0)
        # 借りが最も溜まっていて、かつ在庫がある枠から出す
        candidates = [k for k in pools if pools[k]]
        if not candidates:
            break
        pick = max(candidates, key=lambda k: (debt[k], -len(out)))
        debt[pick] -= 1.0
        out.append(pools[pick].pop(0))
    return out


def apply_author_quota(candidates, window=AUTHOR_QUOTA_WINDOW, ratio=AUTHOR_QUOTA_RATIO,
                       recent_author_ids=(), cooldown=0):
    """表示済み履歴も含む窓で作者の占有を抑え、候補は全件返します。

    cooldown指定時は両条件を満たす候補を優先します。両立できなければ
    クォータを優先し、それも満たせる在庫がなければ元の先頭を出します。
    後段でcooldownを掛け直すと窓外の同一作者が繰り上がるため、同時に選びます。
    """
    cap = max(1, math.ceil(ratio * window))
    history = deque(recent_author_ids, maxlen=max(0, window - 1))
    counts = Counter(history)
    cooldown_history = deque(recent_author_ids[-cooldown:] if cooldown else (),
                             maxlen=cooldown)
    remaining = list(candidates)
    ordered = []
    while remaining:
        index = None
        for i, candidate in enumerate(remaining):
            author_id = candidate.get("author_id")
            if author_id and counts[author_id] >= cap:
                continue
            if index is None:
                index = i
            if not author_id or author_id not in cooldown_history:
                index = i
                break
        if index is None:
            index = 0
        candidate = remaining.pop(index)
        ordered.append(candidate)
        author_id = candidate.get("author_id")
        if history.maxlen:
            if len(history) == history.maxlen:
                counts[history[0]] -= 1
            history.append(author_id)
            counts[author_id] += 1
        if author_id:
            cooldown_history.append(author_id)
    return ordered


def finalize_display_order(candidates, recent_author_ids=(), author_preferences=None):
    """表示順の最終整形。**並べ替えの出口はこの1本だけ**（第4版・2026-09-18）。

    (a) 後ろへ回す拒否済み（`_author_reject_deferred`）を切り出し、
    (b) 残りへクォータとcooldownを同時に掛け、
    (c) 切り出した分を同じ処理で末尾へ足す。

    ⚠️ **この後ろで `apply_author_cooldown` を掛け直さないこと。** クォータの窓の外にいる
    同一作者が繰り上がり、窓の保証が壊れる（`apply_author_quota` の docstring と同じ話）。
    `rank_candidates` も `discovery_tab.py` の先読み保護の後段も、この関数を通す。

    `recent_author_ids` には **`AUTHOR_HISTORY_NEEDED` 件**の表示済み作者を渡すこと。
    足りないとクォータは窓を数えられず、cooldown と同じ働きしかしなくなる。
    """
    normal, deferred = [], []
    for candidate in candidates or []:
        target = (deferred if _author_reject_deferred(candidate.get("author_id"),
                                                      author_preferences) else normal)
        target.append(candidate)
    ordered = apply_author_quota(
        normal, recent_author_ids=recent_author_ids, cooldown=AUTHOR_COOLDOWN)
    if deferred:
        tail_history = list(recent_author_ids) + [c.get("author_id") for c in ordered]
        ordered.extend(apply_author_quota(
            deferred, recent_author_ids=tail_history, cooldown=AUTHOR_COOLDOWN))
    return ordered


def apply_author_cooldown(candidates, recent_author_ids=(), cooldown=3):
    """直近の作者を避けながら、候補を元の優先順に近いまま並べ替えます。"""
    history = deque(recent_author_ids[-cooldown:], maxlen=cooldown)
    remaining = list(candidates)
    ordered = []
    while remaining:
        index = next(
            (i for i, candidate in enumerate(remaining)
             if not candidate.get("author_id") or candidate.get("author_id") not in history),
            0,
        )
        candidate = remaining.pop(index)
        ordered.append(candidate)
        author_id = candidate.get("author_id")
        if author_id:
            history.append(author_id)
    return ordered


def rank_candidates(candidates, preferences, author_preferences,
                    get_setting=None, normalize=None, limit=None, total_works=None,
                    recent_author_ids=()):
    """候補を並べ替えて返します。**これがモジュールの入口。**

    ネットワークは1本も使わない（メモリ上の操作なので完全に無料）。判定のたびに
    呼んでよい（合意書§10-2）。

    戻り値は (並べ替え済みの候補リスト, {work_id: 枠名}) のタプル。枠名は
    カード端の「これは冒険枠です」バッジ（合意書§11-2の条件1）に使う。
    """
    if not candidates:
        return [], {}
    # ⚠️ 薄めるのはタグ側だけ（フェーズ3-3）。作者側は total_works を渡さない
    # ——「見送りが8個のタグへ分散する」という問題はタグ固有で、作者は1作品に1人しか
    # 付かないため同じ歪みが起きない。
    tag_bounds = build_bounds(preferences, total_works=total_works)
    author_bounds = build_bounds(author_preferences, include_author_only=True)
    frames = assign_frames(candidates, tag_bounds, author_bounds,
                           author_preferences=author_preferences, normalize=normalize)
    ratios = resolve_ratios(get_setting)

    frame_of = {}
    for name, items in frames.items():
        for c, _info in items:
            frame_of[str(c.get("work_id") or "")] = name

    # 枠内の順位だけでは、確実枠の在庫が拒否済みだけのときに先頭へ出る
    # （`_interleave` は枠の割合で選ぶのでスコアを見ない）。最終整形へ渡して仕分ける。
    ordered = finalize_display_order(
        [c for c, _info in _interleave(frames, ratios)],
        recent_author_ids=recent_author_ids,
        author_preferences=author_preferences,
    )
    # 上限は整形後に適用する。先に切ると繰り上げられる通常候補を失う。
    if limit is not None:
        ordered = ordered[:max(0, limit)]
    return ordered, frame_of
