"""docs/inventory.json の各項目を、台帳(FL-ID)へ対応づけて docs/ledger_map.json を更新する。

ledger_map.json の構成:
  meta     : 生成元(台帳の項目数・移植版の commit など)
  auto     : {inv_id: {fl, method, width, ...}}  このスクリプトが毎回作り直す(手作業は入らない)
  rules    : [{kind, file, name_regex, fl, reason, ...}]  手で書いた分類規則(n/a 理由つき)。再生成しても保持する
  manual   : {inv_id: "FL-xxx" | "n/a:理由"}      手で決めた個別の対応。再生成しても保持する
  port_text_missing : 移植版 src に文言が見つからない UI 文言の inv_id(挙動の同値ではなく文言の存在確認)
解決の優先順位は tools/check_inventory_coverage.py の resolve() が正(manual > 明確な自動 > rules > 広い範囲の自動)。
使い方: python -B tools/build_ledger_map.py [--port S:\\Projects\\PV-DEV-20261117\\work]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
LEDGER = HERE / "docs" / "FEATURE_LEDGER_2026-11-17.md"
ADDENDA = HERE / "docs" / "FEATURE_LEDGER_ADDENDA_2026-11-17.md"
INV = HERE / "docs" / "inventory.json"
MAP = HERE / "docs" / "ledger_map.json"
DEFAULT_PORT = r"S:\Projects\PV-DEV-20261117\work"

TOL = 3            # 行の許容誤差(元が更新されたときの数行のずれ)
NEAR_Q = 40        # qualname 対応の最大距離(行)
NEAR = 120         # nearest 対応の最大距離(行)
WIDE = 150         # この行数を超える範囲での一致は「広い範囲」(取りこぼしを隠しうる)として別扱い
ID_RE = re.compile(r"\bFL-[A-Z]+\d*[A-Za-z]?\d*\b")
FILE_RE = re.compile(r"([A-Za-z0-9_]+\.py)")
NUM_RE = re.compile(r"\d+(?:\s*-\s*\d+)?")


def split_row(line: str):
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    return cells


def parse_refs(cell: str):
    """'a.py:1-5, 9, b.py:3' -> [(a.py,1,5),(a.py,9,9),(b.py,3,3)]"""
    refs = []
    cur = None
    # トークン化: ファイル名の直後の :数字列 を読む
    pos = 0
    for m in re.finditer(r"([A-Za-z0-9_]+\.py):?\s*((?:\d+(?:\s*-\s*\d+)?(?:\s*[,、]\s*(?!\s*[A-Za-z])\d+(?:\s*-\s*\d+)?)*)?)", cell):
        f = m.group(1)
        nums = m.group(2)
        for n in NUM_RE.finditer(nums or ""):
            t = n.group(0).replace(" ", "")
            if "-" in t:
                a, b = t.split("-")
                refs.append((f, int(a), int(b)))
            else:
                refs.append((f, int(t), int(t)))
    return refs


def parse_ledger(path: Path):
    rows = {}
    sec_t = {}
    section = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        if not line.startswith("|"):
            continue
        cells = split_row(line)
        if section[:2] in ("Q.", "R.", "S."):
            continue
        if section.startswith("T."):
            if len(cells) >= 2 and cells[0] not in ("設定キー", "---", "") and not set(cells[0]) <= set("-"):
                keys = re.findall(r"[A-Za-z0-9_]{3,}", cells[0].replace("`", ""))
                fls = ID_RE.findall(cells[1]) or []
                # "FL-C05, C06, O02" の省略形を展開
                fls = expand_ids(cells[1])
                for k in keys:
                    sec_t.setdefault(k, []).extend(fls)
            continue
        if len(cells) >= 5 and re.match(r"^FL-[A-Z]+\d+", cells[0]):
            fid = cells[0]
            rows[fid] = {
                "id": fid, "section": section, "orig": cells[1], "origin": cells[2],
                "port": cells[3] if len(cells) > 3 else "", "state": cells[4] if len(cells) > 4 else "",
                "note": cells[5] if len(cells) > 5 else "",
                "refs": parse_refs(cells[2]),
            }
    return rows, sec_t


def expand_ids(cell: str):
    """'FL-C05, C06, O02' / 'FL-A09, A11' -> ['FL-C05','FL-C06','FL-O02',...]"""
    out = []
    pref = None
    for tok in re.findall(r"FL-[A-Z]+\d+|(?<![A-Za-z-])[A-Z]\d+(?![A-Za-z0-9])", cell):
        if tok.startswith("FL-"):
            out.append(tok)
            pref = tok
        elif pref:
            out.append("FL-" + tok)
    return out


def parse_addenda_ids(path: Path):
    ids = {}
    if not path.exists():
        return ids
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\|\s*(FL-Z\d+)\s*\|", line)
        if m:
            ids[m.group(1)] = line
    return ids


def backtick_names(text: str):
    return {n for n in re.findall(r"`([A-Za-z_][A-Za-z0-9_\.]*)(?:\(\))?`", text) if len(n) >= 4}


def base_name(it):
    nm = it["name"]
    if it["kind"] in ("api_public", "db_method"):
        return nm.split(".")[-1]
    if it["kind"] == "handler":
        m = re.match(r"^(?:partial:)?([A-Za-z_][A-Za-z0-9_]*)", nm)
        return m.group(1) if m and not nm.startswith("lambda") else ""
    if it["kind"] == "background":
        m = re.search(r"->([A-Za-z_][A-Za-z0-9_]*)|\(([A-Za-z_][A-Za-z0-9_]*)\)|@([A-Za-z_][A-Za-z0-9_]*)", nm)
        return (m.group(1) or m.group(2) or m.group(3)) if m else ""
    return ""


def port_text_index(port: Path):
    src = port / "src" / "pixivvault_nicegui"
    blob = []
    for p in sorted(src.rglob("*.py")):
        try:
            blob.append(p.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
    return "\n".join(blob)


def build_auto(inv, rows, sec_t, port_blob):
    items = inv["items"]
    refs_by_file: dict[str, list] = {}
    names_by_fl: dict[str, set] = {}
    name_index: dict[str, list] = {}
    for fid, r in rows.items():
        for f, a, b in r["refs"]:
            refs_by_file.setdefault(f, []).append((a, b, fid))
        for n in backtick_names(r["orig"] + " " + r["origin"] + " " + r["note"]):
            name_index.setdefault(n, []).append(fid)
    text_rows = [(fid, r["orig"] + " " + r["origin"]) for fid, r in rows.items()]
    auto = {}
    missing = []
    for it in items:
        k, f, ln = it["kind"], it["file"], it["line"]
        hit = None
        if k == "setting_key" and it["name"] in sec_t:
            hit = {"fl": sec_t[it["name"]][0], "also": sec_t[it["name"]][1:], "method": "section-T"}
        if hit is None:
            bn = base_name(it)
            if bn and bn in name_index:
                fls = name_index[bn]
                hit = {"fl": fls[0], "also": fls[1:], "method": "name"}
        if hit is None and k in ("ui_text", "menu_item", "snack", "label_const", "text_literal") and len(it["name"]) >= 4:
            lit = re.sub(r"\{\}", "", it["name"]).strip()
            if len(lit) >= 6:
                cands = [fid for fid, t in text_rows if lit in t and (not rows[fid]["refs"] or any(rf == f for rf, _, _ in rows[fid]["refs"]))]
                if len(cands) == 1:      # 台帳の複数の行に同じ文言が出るときは、先頭を採らず対応づけない
                    hit = {"fl": cands[0], "method": "text"}
        if hit is None and f in refs_by_file:
            best = None
            for a, b, fid in refs_by_file[f]:
                if a - TOL <= ln <= b + TOL:
                    w = b - a + 1
                    if best is None or w < best[0]:
                        best = (w, fid)
            if best:
                hit = {"fl": best[1], "method": "range" if best[0] <= WIDE else "range-wide", "width": best[0]}
        if hit:
            auto[it["id"]] = hit
        it_hit = hit
        if k in ("ui_text", "menu_item", "snack", "label_const", "text_literal"):
            lit = max(re.split(r"\{\}", it["name"]), key=len).strip()
            if len(lit) >= 2 and lit not in port_blob:
                missing.append(it["id"])
    # 2 段目: 同じ関数(qualname)内で、確かな対応を持つ最も近い項目(NEAR_Q 行以内)と同じ行へ寄せる(method=qualname)
    #   外側の巨大な関数全体の多数決はしない(別機能の文言が最頻の行へ寄ってしまうため)
    strong_items: dict = {}
    for it in items:
        h = auto.get(it["id"])
        if h and h["method"] in ("range", "text", "name", "section-T"):
            strong_items.setdefault((it["file"], it["qualname"]), []).append((it["line"], h["fl"]))
    skip_kinds = ("setting_key", "db_method", "db_table", "api_public", "net_endpoint")
    for it in items:
        if it["id"] in auto or it["kind"] in skip_kinds:
            continue
        best = None
        for ln_, fl_ in strong_items.get((it["file"], it["qualname"]), []):
            d = abs(ln_ - it["line"])
            if d <= NEAR_Q and (best is None or d < best[0]):
                best = (d, fl_)
        if best:
            auto[it["id"]] = {"fl": best[1], "method": "qualname", "distance": best[0]}
    # 3 段目: 同じファイルで最も近い台帳の範囲(NEAR 行以内)。弱い対応として別集計する
    for it in items:
        if it["id"] in auto or it["kind"] in skip_kinds:
            continue
        best = None
        for a_, b_, fid in refs_by_file.get(it["file"], []):
            d = 0 if a_ <= it["line"] <= b_ else min(abs(it["line"] - a_), abs(it["line"] - b_))
            if best is None or d < best[0] or (d == best[0] and b_ - a_ < best[2]):
                best = (d, fid, b_ - a_)
        if best and best[0] <= NEAR:
            auto[it["id"]] = {"fl": best[1], "method": "nearest", "distance": best[0]}
    return auto, sorted(missing)


def git_head(p: Path):
    try:
        return subprocess.run(["git", "-C", str(p), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        return ""


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=DEFAULT_PORT)
    ap.add_argument("--ledger", default=str(LEDGER))
    ap.add_argument("--inventory", default=str(INV))
    ap.add_argument("--map", default=str(MAP))
    a = ap.parse_args(argv)
    inv = json.loads(Path(a.inventory).read_text(encoding="utf-8"))
    rows, sec_t = parse_ledger(Path(a.ledger))
    port = Path(a.port)
    blob = port_text_index(port) if (port / "src").exists() else ""
    auto, missing = build_auto(inv, rows, sec_t, blob)
    old = {}
    mp = Path(a.map)
    if mp.exists():
        old = json.loads(mp.read_text(encoding="utf-8"))
    out = dict(old)  # rules / manual / table_rules / column_rules / na_unused を保持
    out.update({
        "meta": {
            "ledger_rows_A_to_P": len(rows),
            "port_commit": git_head(port),
            "port_note": "移植版の文言存在確認に使った work のコミット(読取のみ)",
            "inventory_total": inv["meta"]["total"],
            "source_commit": inv["meta"]["source_commit"],
            "wide_threshold_lines": WIDE,
        },
        "auto": auto,
        "port_text_missing": missing,
    })
    out.setdefault("rules", [])
    out.setdefault("manual", {})
    mp.write_text(json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    by = {}
    for v in auto.values():
        by[v["method"]] = by.get(v["method"], 0) + 1
    print("ledger rows:", len(rows), " auto:", len(auto), by, " port_text_missing:", len(missing))
    return 0


if __name__ == "__main__":
    sys.exit(main())
