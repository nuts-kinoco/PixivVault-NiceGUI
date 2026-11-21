"""Run the existing d6real comparison with at most 30 new GETs."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "d6real-20261121"))

from preflight import SCRATCH, copied_path
from timing import RequestBudget

MAX_NEW_GETS = 30
BUDGET_FILE = "d6api-budget.json"


def install_run_cap(root):
    budget = json.loads((root / BUDGET_FILE).read_text(encoding="ascii"))
    prior = budget["prior_attempts"]
    if type(prior) is not int or not 0 <= prior < RequestBudget.CAP:
        raise ValueError("invalid shared count")
    RequestBudget.CAP = min(RequestBudget.CAP, prior + MAX_NEW_GETS)


def compare():
    from prepare_live import prepare
    import rank_measure

    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--prior-attempts", type=int, required=True)
    parser.add_argument("--owner-open-at", required=True)
    args = parser.parse_args()
    root = copied_path(SCRATCH, args.root)
    confirmation = datetime.fromisoformat(args.owner_open_at)
    if confirmation.utcoffset() is None:
        parser.error("operator confirmation must include its time zone")
    if not 0 <= args.prior_attempts <= RequestBudget.CAP - MAX_NEW_GETS:
        parser.error("the shared ledger must have room for all 30 GETs")
    result = prepare(root, args.prior_attempts)
    (root / "d6api-prepare.json").write_text(json.dumps(result), encoding="ascii")
    if result["status"] != "ready":
        return 2
    (root / BUDGET_FILE).write_text(json.dumps({
        "prior_attempts": args.prior_attempts,
        "owner_open_at": confirmation.isoformat(),
        "max_new_gets": MAX_NEW_GETS,
    }), encoding="ascii")
    install_run_cap(root)
    # The existing harness starts child interpreters here. Each child applies
    # the same cap before delegating to the unchanged d6real server.
    rank_measure.HERE = HERE
    return rank_measure.measure(root, 10)


def run():
    if len(sys.argv) == 4 and sys.argv[2] in ("original", "nice"):
        from ranking_server import main

        root = copied_path(SCRATCH, Path(sys.argv[1]))
        install_run_cap(root)
        return main()
    return compare()


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except Exception as error:
        # Setup and I/O errors can carry copied paths or request text.
        print(type(error).__name__, file=sys.stderr)
        raise SystemExit(2) from None
