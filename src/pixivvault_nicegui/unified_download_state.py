"""統合ダウンロード画面の「判断」だけを集めたところ（画面を持たない）。

元の `unified_download.py`（UDL）を、入力と出力を変えずに移植したもの。
数字だけの絞り込みは ID 指定として振る舞い、並べ替え 5 択、対象の積み方、
チップの状態、経過時間と完了見込み、最後に取得した日の表示をここで決める。
画面を持たないので、偽の入力で検査できる。
"""
from __future__ import annotations

import unicodedata
from datetime import datetime, timedelta



def normalize_query(text) -> str:
    """入力欄の文字列を、判定に使える形へ均す。

    全角の数字で「123」と打つ人が居るので NFKC で潰す。前後の空白も落とす。
    ⚠️ 中の空白は残す（作者名に空白が入るため）。
    """
    if not text:
        return ""
    return unicodedata.normalize("NFKC", str(text)).strip()


def is_id_query(text) -> bool:
    """「数字だけ」ならID指定として振る舞う（デザインの芯）。

    ⚠️ 作者名が数字だけ、ということは在りうる。そのときは
      「ID指定の行」と「一覧の中の作者」が両方出る作りにして、どちらも選べるようにする
      （ここでは判定しない。片方に倒さないのが答え）。
    """
    q = normalize_query(text)
    return bool(q) and q.isdigit()


SORT_KEYS = ("follow_order", "name", "work_count", "last_downloaded", "favorite")

# #3a（2026-08-15・PO承認・「選択の作法と自動監視」）: 見出しクリックのソートを廃止し、
# 道具行の単一メニュー（5択）＋昇順/降順ボタンへ置き換えた。ラベルはメニューの表示名。
SORT_LABELS = {
    "follow_order": "フォロー登録順",
    "name": "作者名順",
    "work_count": "保存済みの多い順",
    "last_downloaded": "最後に取得した日",
    "favorite": "☆を先に",
}


def sort_authors(authors, work_counts, key, reverse=False):
    """作者一覧を並べ替える。key は SORT_KEYS のいずれか。

    key が None、または SORT_KEYS に無いときは並べ替えない（＝渡された順＝フォロー登録順の
    ままにする。db.get_following_users() の既定が follow_order 昇順＝新しい順のため、
    並べ替えない状態と key="follow_order" 昇順は結果として一致する）。

    ⚠️ #3a: 見出しクリックによる「ID順」ソートは廃止（5択メニューに含まれないため）。
    """
    if key not in SORT_KEYS:
        return list(authors)

    def sort_value(a):
        if key == "name":
            return normalize_query(a.get("name") or "").lower()
        if key == "work_count":
            return work_counts.get(str(a.get("user_id") or ""), 0)
        if key == "last_downloaded":
            # ⚠️ ISO文字列同士の比較で足りる（辞書順=時系列順）。未取得（None/空）は最も古い扱い。
            return a.get("last_downloaded") or ""
        if key == "favorite":
            # ⚠️ 「☆を先に」＝昇順で☆(1)が非☆(0)より前に来るよう符号を反転する。
            return 0 if a.get("is_favorite") else 1
        # key == "follow_order"
        try:
            return int(a.get("follow_order") or 0)
        except (TypeError, ValueError):
            return 0

    return sorted(authors, key=sort_value, reverse=reverse)


def format_last_downloaded(value) -> str:
    """`last_downloaded`（ISO文字列 | None）を一覧の列表示用に整える。

    ⚠️ 材料が無ければ「－」（罠6・無い数字を作らない）。壊れた文字列は先頭16文字を素通し
    （落とすより「変な値が入っている」と分かるほうが良い・#2「未確認」の精神）。
    """
    if not value:
        return "－"
    try:
        return datetime.fromisoformat(str(value)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return str(value)[:16]


def add_targets_bulk(targets, authors):
    """複数の作者を順序と先着の由来を保って追加する。"""
    out = list(targets)
    seen = {str(t.get("user_id")) for t in out}
    for a in authors:
        uid = str(a.get("user_id") or "")
        if uid and uid not in seen:
            out.append({"user_id": uid, "name": a.get("name") or uid, "origin": "follow"})
            seen.add(uid)
    return out


def remove_targets_bulk(targets, user_ids):
    """複数の user_id をまとめて対象から外す。"""
    ids = {str(u) for u in user_ids}
    return [t for t in targets if str(t.get("user_id")) not in ids]


def filter_authors(authors, query):
    """一覧の絞り込み。⚠️ 対象（選択済み）には触らない。

    絞り込みで対象が消えるのは事故（378人を扱う画面で、検索して選択が変わるのは
    取り返しがつかない）。この関数は**一覧の見せ方**しか返さない。
    """
    q = normalize_query(query).lower()
    if not q:
        return list(authors)
    out = []
    for a in authors:
        name = normalize_query(a.get("name") or "").lower()
        uid = str(a.get("user_id") or "")
        if q in name or q in uid:
            out.append(a)
    return out


def add_target(targets, author, origin):
    """対象に1件積む。⚠️ 同じ作者は2重に積まない。

    origin は "id"（ID指定）か "follow"（一覧から）。同じ作者が両方から来たときは、
    **先に積まれたほうの由来を残す**（後から来たほうで書き換えると、画面の札が
    勝手に変わって「自分が積んだもの」が分からなくなる）。

    戻り値: (新しい対象の一覧, 積んだか)
    """
    uid = str(author.get("user_id") or "")
    if not uid:
        return list(targets), False
    for t in targets:
        if str(t.get("user_id")) == uid:
            return list(targets), False
    out = list(targets)
    out.append({"user_id": uid, "name": author.get("name") or uid, "origin": origin})
    return out, True


def remove_target(targets, user_id):
    """対象から1件外す（✕ は「取り消す」だけ。保存済みファイルは消さない）。"""
    uid = str(user_id)
    return [t for t in targets if str(t.get("user_id")) != uid]


def find_author(authors, user_id):
    """一覧の中から user_id の作者を探す（無ければ None）。

    ID指定した相手がフォロー中なら、行の文言を「フォロー中の ◯◯ です」に変えて、
    一覧側も同時に選択済みにする（＝ 同じ作者が2重に積まれない）。
    """
    uid = str(user_id)
    for a in authors:
        if str(a.get("user_id")) == uid:
            return a
    return None



def summarize_targets(targets):
    """対象の内訳（画面の1行に出す）。"""
    n_id = sum(1 for t in targets if t.get("origin") == "id")
    n_follow = len(targets) - n_id
    return {"total": len(targets), "id": n_id, "follow": n_follow}


# --- #41 対象チップの状態（完了／取得中／待機）。docs/design/DOWNLOAD_SCREEN_SPEC_2026-08-14.md §4 -5 ---

def target_status(user_id, run_target_ids, done, work_idx, work_total, current_uid, run_completed):
    """対象1件の状態を返す: ("done"|"in_progress"|"waiting", 作品idx|None, 作品total|None)。

    ⚠️ 作者ごとの確定ダウンロード件数は追跡していない（batch_stats_callback は
    バッチ全体の累積しか返さない）ので、「完了」は件数を出さない——無い数字を
    作らない（罠6）。「取得中」だけ、いまの作者の作品単位カウンタ（work_idx/work_total）
    を使うので実数が出せる。

    run_target_ids: 直近の _start() で実際に積んだ user_id の並び（実行順）。
    done          : 作者単位の完了人数（state["prog"]["done"]）。
    current_uid   : いま処理中の作者（state["last_author"]）。
    run_completed : このバッチが最後まで完了したか（stopping/idle 半ばではない）。
    """
    uid = str(user_id)
    try:
        idx0 = run_target_ids.index(uid)
    except ValueError:
        return ("waiting", None, None)
    if run_completed:
        return ("done", None, None)
    if uid == str(current_uid) and work_total > 0:
        return ("in_progress", work_idx, work_total)
    if idx0 < done:
        return ("done", None, None)
    return ("waiting", None, None)


# --- 進捗パネルの開閉（#39・docs/UNIFIED_DOWNLOAD_PROGRESS_TOGGLE_SPEC_2026-08-14.md）----

def format_hms(dt) -> str:
    """datetime を "21:34:08" の形にする。dt が None なら空文字（罠6・材料が無ければ出さない）。"""
    if dt is None:
        return ""
    return dt.strftime("%H:%M:%S")


def estimate_completion(start_time, done: int, total: int, elapsed_sec: float,
                         work_idx: int = 0, work_total: int = 0):
    """(所要時間の文字列, 完了見込み時刻の文字列 | None) を返す。

    ⚠️ done<=0 または elapsed_sec<=0 のときは見込みを出さない（None）。
    根拠が無い数字を出さない——罠6「実際の状態に繋ぐ」の画面版。

    平均速度 = done / elapsed_sec（バッチ開始からの単純平均）。
    ⚠️ gui.py の `_calc_eta` は直近10件の移動平均だが、ここは開いたときだけ見る
    単発の表示なので単純平均で足りる。数字が gui.py 側のETAと一致しない前提
    （docs/UNIFIED_DOWNLOAD_PROGRESS_TOGGLE_SPEC_2026-08-14.md §6・§10-4）。

    work_idx/work_total（いまの作者の中で何作品目/全何作品か）は、done<=0の
    ときだけ使うフォールバック（#39 (c) 対象1人だと done がずっと0のまま
    「算出中…」になる問題への対策・docs/UNIFIED_DOWNLOAD_ETA_FALLBACK_SPEC_2026-08-14.md）。
    done>0側の計算は変更していない。
    """
    elapsed_sec = float(elapsed_sec or 0)
    minutes, seconds = divmod(int(elapsed_sec), 60)
    elapsed_text = f"{minutes}分{seconds}秒 経過" if minutes else f"{seconds}秒 経過"

    if elapsed_sec <= 0 or start_time is None:
        return elapsed_text, None

    if done > 0 and total > 0:
        speed = done / elapsed_sec                     # 人/秒
        remaining = max(0, total - done)
        eta_sec = remaining / speed if speed > 0 else None
        if eta_sec is None:
            return elapsed_text, None
        completion = start_time + timedelta(seconds=elapsed_sec + eta_sec)
        suffix = " 見込み" if remaining > 0 else ""
        return elapsed_text, format_hms(completion) + suffix

    # フォールバック: 作者単位の完了実績が無いが、作品単位の進捗がある。
    if work_idx > 0 and work_total > 0 and total > 0:
        items_done = work_idx - 1
        if items_done <= 0:
            return elapsed_text, None
        total_items_estimate = work_total * total       # total==1 なら work_total そのもの（正確）
        speed = items_done / elapsed_sec
        remaining_items = max(0, total_items_estimate - items_done)
        eta_sec = remaining_items / speed if speed > 0 else None
        if eta_sec is None:
            return elapsed_text, None
        completion = start_time + timedelta(seconds=elapsed_sec + eta_sec)
        suffix = " 見込み" if remaining_items > 0 else ""
        if total > 1:
            # 対象2人以上・1人目もまだ終わっていない＝他の対象者も同じペースという前提が乗る
            suffix += "（概算）"
        return elapsed_text, format_hms(completion) + suffix

    return elapsed_text, None


# --- バッチエンジンの進捗イベントを、元の画面の state["prog"] 相当へ畳む（移植版で新設）---

class RunProgress:
    """`BatchRunEngine` の進捗イベントから、チップ・進捗帯が使う値を作る。

    終わった人数は元と同じ「idx-1」ではなく、author_done / author_failed を数える
    （失敗した作者も「終わった」に入れる。一覧に出ないまま先へ進むため）。
    """

    def __init__(self, target_ids=()):
        self.target_ids = [str(t) for t in target_ids]
        self.total = len(self.target_ids)
        self.done = 0
        self.current = None
        self.work_idx = 0
        self.work_total = 0
        self.state = "idle"          # idle|running|paused|stopping|done|stopped|failed
        self.pause_reason = ""
        self.counts = {}
        self.kind = "authors"        # "bookmarks": 行は公開(1)/非公開(2)。件数は作品単位
        self.work_title = ""
        self.finished_authors = set()
        self.failed_authors = set()      # 一覧取得などで失敗した作者（元と同じく先へ進むが、「完了」とは言わない）
        self.elapsed = 0.0

    @property
    def completed(self):
        return self.state == "done"

    def apply(self, event):
        name = event.get("event")
        if name == "run_start":
            self.state, self.total = "running", int(event.get("total", self.total))
        elif name == "author_start":
            self.current = str(event["author_id"])
            self.work_idx = self.work_total = 0
            self.work_title = ""
            if self.state == "paused":
                self.state, self.pause_reason = "running", ""
        elif name == "author_listed":
            c = event.get("counters") or {}
            self.work_total = sum(int(c.get(k, 0)) for k in ("n_new", "n_updated", "n_restored", "n_missing"))
        elif name == "work_info":
            self.work_title = str(event.get("title") or "")
        elif name == "work_progress":
            self.work_idx = int(event.get("done", 0)) + 1
            self.work_total = max(self.work_total, int(event.get("queued", 0)))
            if event.get("title"):
                self.work_title = str(event["title"])
        elif name in ("author_done", "author_failed"):
            self.done += 1
            self.finished_authors.add(str(event["author_id"]))
            if name == "author_failed":
                self.failed_authors.add(str(event["author_id"]))
            self.current = None
        elif name == "run_paused":
            self.state, self.pause_reason = "paused", str(event.get("reason", ""))
        elif name == "run_end":
            self.state = str(event.get("outcome", "done"))
            self.elapsed = float(event.get("elapsed", 0.0))
            self.current = None
        self.counts = dict(event.get("totals") or self.counts)
        return self

    def chip(self, user_id):
        return target_status(user_id, self.target_ids, self.done, self.work_idx, self.work_total,
                             self.current, self.completed)


END_MESSAGES = {
    "done": "完了しました。",
    "stopped": "停止しました。",
    "failed": "失敗しました。",
}


def end_message(outcome, error_code=""):
    """実行の終わりに出す 1 行。元は停止を「失敗しました: …中止」と出していたが、移植版は「停止しました。」。"""
    if outcome == "interrupted" and error_code == "bookmark_account_mismatch":
        return "前回の実行とアカウントが異なるため、再開を保留しました。ログイン中のアカウントを確認してください。"
    if outcome == "failed" and error_code == "author_account_mismatch":
        return "確認したアカウントが異なるため、作者の取得を中止しました。ログイン中のアカウントを確認してください。"
    if outcome == "failed" and error_code:
        return f"失敗しました: {error_code}"
    return END_MESSAGES.get(outcome, "")


PAUSE_TEXT = {
    "": "一時停止しています。",
    "user": "一時停止しています。",
    "network_stopped": "通信が止まっているため、一時停止しています。復旧後に「再開」を押してください。",
    "network_gate_unavailable": "通信の安全確認ができないため、一時停止しています。",
}


def status_text(state, *, pause_reason="", index=0, total=0, outcome_code=""):
    """進捗帯の見出し 1 行。停止は元の文言（UD:1070）を維持し、終わったら「停止しました。」。"""
    if state == "idle":
        return "待機中"
    if state == "running":
        return f"{index} / {total} 人目…" if total else "実行中…"
    if state == "paused":
        return PAUSE_TEXT.get(pause_reason, PAUSE_TEXT["user"])
    if state == "stopping":
        return "停止しています（いまの作者が終わるまで待っています）…"
    return end_message(state, outcome_code)


INTERRUPTED_TEXT = "前回は途中で止まりました。続きから再開するか、破棄できます。"
