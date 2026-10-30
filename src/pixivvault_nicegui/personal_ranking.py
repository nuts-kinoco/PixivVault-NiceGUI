"""Adopted original personal filters/paging from 3ac3aa0 ranking_tab.py.

Pure helpers intentionally retain the adopted day-boundary and missing-date rules.
"""
from datetime import datetime
_DATE_PRESETS = (
    ("all", "すべて", None),
    ("1week", "1週間", 7),
    ("1month", "1か月", 31),
    ("6months", "半年", 183),
    ("1year", "1年", 366),
)

_BOOKMARK_PRESETS = ((0, "指定なし"), (500, "500+"), (1000, "1000+"),
                     (5000, "5000+"), (10000, "10000+"))

_SORT_PRESETS = (("popular_desc", "人気順"), ("date_desc", "新着順"), ("date_asc", "投稿が古い順"))

def default_personal_filters():
    """絞り込み条件の既定値。

    ⚠️ exclude_hidden_works だけ既定 True。これは新しい絞り込みではなく、
    **いま既に常時効いている振る舞い**（`#63`・描画直前に db.get_hidden_work_ids() で
    落としている）を絞り込みパネルの表に出したもの。既定を False にすると、刷新した
    とたんに「出さない」が効かなくなったように見える（振る舞いの回帰）。
    """
    return {
        "bookmark_min": 0,          # サーバー側条件（再検索が要る）
        "sort": "popular_desc",     # サーバー側条件（再検索が要る）・2026-08-23
        "date_preset": "all",       # 手元条件
        "tags_include": [],         # 手元条件
        "tags_exclude": [],         # 手元条件
        "per_author_max": 0,        # 手元条件。0＝無制限
        "exclude_downloaded": False,
        "exclude_hidden_authors": False,
        "exclude_hidden_works": True,
        "exclude_hated": False,
    }

def normalize_personal_filters(raw):
    """保存値・外から来た値を、欠けや型違いに強い形へ正規化する。

    ⚠️ 壊れた保存値でタブが開かなくなるほうが、条件が既定へ戻るよりずっと悪い。
    どんな入力でも必ず「使える辞書」を返す（例外を投げない）。
    """
    out = default_personal_filters()
    if not isinstance(raw, dict):
        return out
    valid_presets = {key for key, _label, _days in _DATE_PRESETS}
    for key, default in list(out.items()):
        if key not in raw:
            continue
        value = raw[key]
        if key == "bookmark_min" or key == "per_author_max":
            try:
                n = int(value)
            except (TypeError, ValueError):
                continue
            out[key] = max(0, n)
        elif key == "date_preset":
            if value in valid_presets:
                out[key] = value
        elif key == "sort":
            if value in {k for k, _label in _SORT_PRESETS}:
                out[key] = value
        elif key in ("tags_include", "tags_exclude"):
            if isinstance(value, (list, tuple)):
                tags = []
                for t in value:
                    if isinstance(t, str) and t.strip() and t.strip() not in tags:
                        tags.append(t.strip())
                out[key] = tags
        else:
            out[key] = bool(value)
    return out

def _parse_create_date(value):
    """pixivの create_date（"2021-01-01T00:00:00+09:00"）を datetime にする。

    読めない値は None を返す——**その作品を落とさない**側に倒す（読めないことを
    理由に結果から消すと、原因が画面から見えない）。
    """
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    try:  # 末尾Z（UTC表記）だけ吸収する
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None

def apply_personal_filters(items, filters, hidden_work_ids=None, hidden_author_ids=None,
                           downloaded_ids=None, hated_ids=None, now=None):
    """検索結果（正規化済みの辞書リスト）へ絞り込みを適用する。

    ⚠️⚠️ **突き合わせは必ず正規化後の illust_id / user_id（str）に対して行う。**
    DB側は全テーブル work_id/author_id が TEXT で、書き込み側が str() 変換している。
    一方 pixiv API の生JSONは id が int。生のまま突き合わせると**例外は出ず、除外が
    静かに全件すり抜ける**（＝見ても分からない壊れ方）。この関数は
    _normalize_personal_item() を通った後の辞書だけを受け取る前提。

    戻り値: (残った作品のリスト, 落とした件数)
    ⚠️ 落とした件数は「DBに何件登録されているか」ではなく「**今回の結果から実際に
    落ちた件数**」。DBに1000件あっても今回の結果に1件しか無ければ1件と出す（罠6）。
    """
    items = list(items or [])
    before = len(items)
    if not filters:
        return items, 0

    if filters.get("exclude_hidden_works") and hidden_work_ids:
        items = [it for it in items if it.get("illust_id") not in hidden_work_ids]
    if filters.get("exclude_hidden_authors") and hidden_author_ids:
        items = [it for it in items if it.get("user_id") not in hidden_author_ids]
    if filters.get("exclude_downloaded") and downloaded_ids:
        items = [it for it in items if it.get("illust_id") not in downloaded_ids]
    if filters.get("exclude_hated") and hated_ids:
        items = [it for it in items if it.get("illust_id") not in hated_ids]

    preset = filters.get("date_preset") or "all"
    days = next((d for key, _lbl, d in _DATE_PRESETS if key == preset), None)
    if days:
        base = now or datetime.now()
        kept = []
        for it in items:
            created = _parse_create_date(it.get("create_date"))
            if created is None:
                kept.append(it)   # 日付が読めないものは落とさない
                continue
            # ⚠️ タイムゾーン付き（pixivは "+09:00" 付き）と素のdatetime（datetime.now()）
            # を引き算すると TypeError になる。両方から tzinfo を外して素同士で比べる。
            # 最大で時差ぶん（〜9時間）ずれるが、プリセットの粒度は「日」なので実害が無い
            # （境界の1件が入る/入らないだけ・許容と判断）。
            created_naive = created.replace(tzinfo=None)
            base_naive = base.replace(tzinfo=None)
            if (base_naive - created_naive).days <= days:
                kept.append(it)
        items = kept

    include = [t.lower() for t in (filters.get("tags_include") or [])]
    exclude = [t.lower() for t in (filters.get("tags_exclude") or [])]
    if include or exclude:
        kept = []
        for it in items:
            tags = {str(t).lower() for t in (it.get("tags") or [])}
            if include and not all(t in tags for t in include):
                continue
            if exclude and any(t in tags for t in exclude):
                continue
            kept.append(it)
        items = kept

    per_author = filters.get("per_author_max") or 0
    if per_author > 0:
        seen = {}
        kept = []
        for it in items:
            uid = it.get("user_id") or ""
            n = seen.get(uid, 0)
            if n >= per_author:
                continue
            seen[uid] = n + 1
            kept.append(it)
        items = kept

    return items, before - len(items)

def merge_personal_page(existing_items, new_items, raw_count, has_next,
                        prev_offset=0, append=False):
    """取得した1ページ分を、いまの一覧へ併合して次のページング状態を決める。

    出どころ: docs/design/RANKING_TAB_REDESIGN_SPEC_2026-08-23.md §4-2（選択肢A・PO決定）。
    ⚠️ build_ranking_tab() の外に置くのは、打ち止め判定が「見ても分からない」側の判断
    （CLAUDE.md §8の物差し）だから——画面を見ても、続きがあるのに止まったのか、
    本当に終わったのかは区別が付かない。ここに出して行動テストで固定する。

    引数:
      existing_items: いま画面が持っている（絞り込み前の）正規化済みリスト
      new_items     : 今回届いたぶんの正規化済みリスト（ページ内で並べ替え済み）
      raw_count     : 今回**サーバーが返した生の件数**。offsetはこれで進める
                      （手元の絞り込みで落とした数を引いてはいけない——pixiv側の
                      offsetは手元の絞り込みを知らないため、引くと同じ作品を
                      何度も取り直すことになる）
      has_next      : 応答に next_url があったか（＝pixivが「次がある」と言ったか）
      prev_offset   : 今回のリクエストで使ったoffset
      append        : True なら追記、False なら置き換え（新しい検索）

    戻り値: (併合後のリスト, 次のoffset, 打ち止めか, 実際に増えた件数)

    打ち止めの判定は次の3つのどれか（1つでも当たれば止める）:
      1. next_url が無い……pixiv自身が「次は無い」と言っている（いちばん確か）
      2. 1件も返らなかった
      3. 返ってきたが全部が既出……これ以上進めない。⚠️ これは安全弁でもある——
         仮に offset が効かない環境に当たっても、同じページを延々と取り続けずに
         必ず止まる（重複を画面に出すこともない）
    """
    new_items = list(new_items or [])
    if not append or existing_items is None:
        merged = new_items
        added = len(new_items)
        exhausted = (not has_next) or (raw_count <= 0)
        return merged, raw_count, exhausted, added

    seen = {it.get("illust_id") for it in existing_items}
    fresh = [it for it in new_items if it.get("illust_id") not in seen]
    merged = list(existing_items) + fresh
    exhausted = (not has_next) or (raw_count <= 0) or (not fresh)
    return merged, prev_offset + raw_count, exhausted, len(fresh)

from dataclasses import dataclass
from .ranking_source import RankingQuery, parse_item
from .external_vault import VaultError

@dataclass(frozen=True)
class PersonalPage:
    rows: tuple
    offset: int
    exhausted: bool
    key: tuple


def normalize_item(raw):
    if type(raw) is not dict:raise VaultError('invalid-search-result')
    user=raw.get('user',{});urls=raw.get('image_urls',{})
    if type(user) is not dict or type(urls) is not dict or type(raw.get('tags',[])) is not list:raise VaultError('invalid-search-result')
    tags=[t.get('name') for t in raw.get('tags',[]) if type(t) is dict and type(t.get('name')) is str]
    typ=raw.get('type','illust');typ={'illust':0,'manga':1,'ugoira':2}.get(typ,typ)
    normalized={'id':str(raw.get('id','')),'title':raw.get('title',''),'user_id':str(user.get('id','')),
        'user_name':user.get('name',''),'thumbnail_url':urls.get('square_medium') or urls.get('medium'),'tags':tags,
        'illust_type':typ,'x_restrict':raw.get('x_restrict',0)}
    if 'page_count' in raw:normalized['page_count']=raw['page_count']
    if 'illust_ai_type' in raw:normalized['ai_type']=raw['illust_ai_type']
    item=parse_item(normalized,RankingQuery(tag='personal'))
    if item is None:raise VaultError('invalid-search-result')
    return {'illust_id':item.record.remote_id,'user_id':item.record.author_id,'tags':tags,
        'create_date':raw.get('create_date',''),'item':item}


class PersonalRankingSource:
    def __init__(self,vault):self.vault=vault
    def fetch(self,tags,filters,*,r18=False,exclude_ai=False,previous=None,author_counts=None):
        tags=tuple(dict.fromkeys(t.strip() for t in tags if type(t) is str and t.strip()))
        if not tags:raise VaultError('missing-tags')
        filters=normalize_personal_filters(filters)
        key=(tags,r18,exclude_ai,filters['bookmark_min'],filters['sort'])
        if previous is not None and (previous.key!=key or previous.exhausted):raise VaultError('stale-page')
        offset=previous.offset if previous else 0
        packet=self.vault.search(' '.join(tags),filters['bookmark_min'],sort=filters['sort'],r18=r18,exclude_ai=exclude_ai,offset=offset)
        raw=packet.get('illusts')
        if type(raw) is not list:raise VaultError('invalid-search-result')
        rows=[normalize_item(i) for i in raw]
        counts=author_counts or {}
        rows.sort(key=lambda w:(-counts.get(w['user_id'],0),w['user_id'],w['illust_id']))
        merged,next_offset,exhausted,_=merge_personal_page(previous.rows if previous else None,rows,len(raw),bool(packet.get('next_url')),prev_offset=offset,append=previous is not None)
        return PersonalPage(tuple(merged),next_offset,exhausted,key)
