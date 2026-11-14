def dedup_group_key(g):
    """グループの同一性を表すキー（メンバーのwork_id・pageの集合）。"""
    return frozenset((it["work_id"], it["page"]) for it in g["items"])


def dedup_execute_move_selections(selections, move_one_item_fn):
    """複数グループにまたがる「選んだものをごみ箱へ移す」の集計・実行ロジック（純粋寄り）。

    selections: [(group, items), ...]。groupは{"user_id":..., "items":[...]}を持つ辞書、
    itemsはそのグループ内で選択されたメンバーのリスト（items自体が空のグループは無視する）。
    move_one_item_fn(item, user_id): 実際の1件移動（ファイルI/O・DB書き込み）を行う関数。
    失敗したら例外を投げる想定（呼び出し元のgui.pyでは_dedup_move_item_to_quarantine）。
    テストではフェイクを渡し、実ファイル・DBに触れずに集計ロジックだけ検証できる。

    戻り値: (moved_by_group, failed)
      moved_by_group: {dedup_group_key(group): [moved_items...]}（1件も移せなかった
                       グループはキーごと含まれない）
      failed: [(item, group, exception), ...]
    """
    moved_by_group = {}
    failed = []
    for group, items in selections:
        if not items:
            continue
        moved = []
        for m in items:
            try:
                move_one_item_fn(m, group["user_id"])
                moved.append(m)
            except Exception as ex:                                       # noqa: BLE001
                failed.append((m, group, ex))
        if moved:
            moved_by_group[dedup_group_key(group)] = moved
    return moved_by_group, failed
