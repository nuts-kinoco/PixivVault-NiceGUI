"""進捗ボード用データを作る。入力: docs/progress_manual.json（手動）＋台帳・全体テストログ・git（自動）。
出力: docs/status.json と、Artifact へ上げる行データ（artifact_data/*.json）。数値を手で書かない。"""
import json
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(r"S:\Projects\PV-DEV-20261117")
WORK = ROOT / "work"
MANUAL = WORK / "docs" / "progress_manual.json"
LEDGER = WORK / "docs" / "FEATURE_LEDGER_2026-11-17.md"
OUT = ROOT / "artifact_data"
JST = timezone(timedelta(hours=9))


def git(*args, cwd=WORK):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8").stdout.strip()


def ledger_counts():
    counts = {}
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\| (FL-[A-Z]\d+)\s*\|", line)
        if not m:
            continue
        cols = [c.strip() for c in line.strip().strip("|").split("|")]
        # 状態は 4 列目（0 ID, 1 元の機能, 2 元の場所, 3 移植版の場所, 4 状態）。無・弱は先頭語で判定
        status = ""
        for c in cols[3:6]:
            head = re.split(r"[（(\s]", c)[0]
            if head in ("有", "弱", "無", "意図的削除", "対象外", "条件付きで有", "条件付き"):
                status = head
                break
        counts[status or "不明"] = counts.get(status or "不明", 0) + 1
    return counts


def latest_full_log():
    logs = sorted((ROOT / "logs").glob("full_work_*.log"), key=lambda p: p.stat().st_mtime)
    for p in reversed(logs):
        text = p.read_text(encoding="utf-8", errors="replace")
        m = re.findall(r"(\d+) failed, (\d+) passed.*?(?:, (\d+) errors?)?.* in ([\d.]+)s", text)
        if m:
            f, ok, e, sec = m[-1]
            return {"log": p.name, "failed": int(f), "passed": int(ok), "errors": int(e or 0), "seconds": float(sec)}
    return None


def worktrees():
    rows = []
    for line in git("worktree", "list", "--porcelain").split("\n\n"):
        d = dict(l.split(" ", 1) for l in line.splitlines() if " " in l)
        if d.get("worktree"):
            rows.append({"path": d["worktree"].split("/")[-1], "branch": d.get("branch", "").replace("refs/heads/", ""), "head": d.get("HEAD", "")[:7]})
    return rows


def main():
    m = json.loads(MANUAL.read_text(encoding="utf-8"))
    ladder = m["ladder"]
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    phases, exits, items, decisions, running = [], [], [], [], []
    for p in m["phases"]:
        done = sum(1 for e in p["exits"] if e["ok"])
        phases.append({"id": p["id"], "name": p["name"], "goal": p["goal"], "state": p["state"], "approval": p["approval"],
                       "done": done, "total": len(p["exits"]), "pct": round(100 * done / max(1, len(p["exits"])))})
        for e in p["exits"]:
            exits.append({"phase": p["id"], "condition": e["t"], "met": "達成" if e["ok"] else "未", "evidence": e["ev"]})
    for it in m["items"]:
        items.append({"phase": it["phase"], "id": it["id"], "title": it["title"], "stage": it["stage"], "stage_name": ladder[it["stage"]],
                      "checker": it["checker"], "real_app": it["real"], "evidence": it["ev"]})
    for d in m["decisions"]:
        decisions.append({"topic": d["k"], "value": d["v"], "set_by": d["by"], "status": d["st"]})
    for r in m["running"]:
        running.append({"who": r["who"], "what": r["what"]})
    counts = ledger_counts()
    full = latest_full_log()
    head = git("log", "-1", "--format=%h %s")
    meta = [
        {"label": "更新", "value": now},
        {"label": "work の HEAD", "value": head},
        {"label": "台帳（暫定）", "value": json.dumps(counts, ensure_ascii=False)},
        {"label": "直近の全体テスト", "value": json.dumps(full, ensure_ascii=False) if full else "なし"},
        {"label": "停止理由", "value": m.get("stop") or "なし"},
    ]
    status = {"updated": now, "phases": phases, "exits": exits, "items": items, "decisions": decisions, "running": running,
              "ledger_counts_provisional": counts, "full_test": full, "head": head, "worktrees": worktrees(), "stop": m.get("stop", "")}
    (WORK / "docs" / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT.mkdir(exist_ok=True)
    tables = {"phases": phases, "exits": exits, "items": items, "decisions": decisions,
              "runstate": [{"label": x["label"], "value": x["value"]} for x in meta] + [{"label": "実行中: " + r["who"], "value": r["what"]} for r in running]}
    for name, rows in tables.items():
        (OUT / f"{name}.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    print("ok", now, {k: len(v) for k, v in tables.items()})


if __name__ == "__main__":
    sys.exit(main())
