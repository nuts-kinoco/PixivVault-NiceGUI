"""Budget dispatch and summarize paired UI timings without storing request data."""

import argparse
from dataclasses import dataclass
import json
from math import ceil, isfinite
from pathlib import Path
from statistics import median
from threading import Lock
from time import monotonic, sleep as default_sleep

OPERATIONS = frozenset(("ranking_official", "ranking_personal", "learning_next",
                        "library_search", "viewer_next", "bookmarks", "following",
                        "recommend", "tab_switch"))
CONDITIONS = frozenset(("warm", "cold", "prefetched", "refill", "local"))


class StopRun(RuntimeError):
    """Only fixed diagnostic codes may be exported."""
    REASONS = frozenset(("budget", "http_401", "http_403", "http_429", "captcha",
                         "authentication", "transport", "destination", "product_stop"))

    def __init__(self, reason):
        self.reason = reason if reason in self.REASONS else "transport"
        super().__init__(self.reason)


@dataclass(frozen=True, slots=True)
class DispatchObservation:
    status: int
    challenge: bool = False
    authenticated: bool | None = None

    def validate(self):
        if (type(self.status) is not int or type(self.challenge) is not bool
                or (self.authenticated is not None and type(self.authenticated) is not bool)):
            raise StopRun("transport")
        if self.status in (401, 403, 429):
            raise StopRun(f"http_{self.status}")
        if self.challenge:
            raise StopRun("captcha")
        if self.authenticated is False:
            raise StopRun("authentication")
        if 300 <= self.status < 400:
            raise StopRun("destination")
        if not 200 <= self.status < 300:
            raise StopRun("transport")


class RequestBudget:
    """Both adapters must share one instance at the HTTP-dispatch boundary.

    The transport adapter must disallow redirects and supply one observation
    per HTTP dispatch, including its independent auth and challenge checks.
    The lock remains held until that observation has been validated.
    """
    CAP = 180
    MIN_SECONDS = 0.875

    def __init__(self, *, clock=monotonic, sleep=default_sleep):
        self._clock = clock
        self._sleep = sleep
        self._lock = Lock()
        self._last_start = None
        self._stop = None
        self.count = 0

    def run(self, dispatch):
        with self._lock:
            if self._stop is not None:
                raise StopRun(self._stop)
            if self.count >= self.CAP:
                self._stop = "budget"
                raise StopRun(self._stop)
            if self._last_start is not None:
                earliest = self._last_start + self.MIN_SECONDS
                while (wait := earliest - self._clock()) > 0:
                    self._sleep(wait)
            self._last_start = self._clock()
            self.count += 1
            try:
                response = dispatch()
                if type(response) is not DispatchObservation:
                    raise StopRun("transport")
                response.validate()
                return response
            except StopRun as error:
                self._stop = error.reason
            except Exception:
                self._stop = "transport"
            raise StopRun(self._stop) from None


def _merge(intervals, start, end):
    for a, b in intervals:
        if not all(isfinite(t) for t in (a, b)) or not start <= a <= b <= end:
            raise ValueError("interval outside trial")
    result = []
    for a, b in sorted(intervals):
        if result and a <= result[-1][1]:
            result[-1][1] = max(result[-1][1], b)
        else:
            result.append([a, b])
    return result


def _duration(merged):
    return sum(b - a for a, b in merged)


def trial_metrics(sample):
    start, end = sample["start_ms"], sample["paint_ms"]
    if not isfinite(start) or not isfinite(end) or not start <= end:
        raise ValueError("invalid trial duration")
    network_intervals = sample["network_ms"]
    merged_network = _merge(network_intervals, start, end)
    network = _duration(merged_network)
    api_intervals, image_intervals = sample["api_ms"], sample["image_ms"]
    api = _duration(_merge(api_intervals, start, end))
    image = _duration(_merge(image_intervals, start, end))
    if _merge(api_intervals + image_intervals, start, end) != merged_network:
        raise ValueError("network intervals do not match API and image intervals")
    network_and_pace = _duration(_merge(network_intervals + sample["pace_ms"], start, end))
    return {"total": end - start, "network": network, "api": api, "image": image,
            "pace": network_and_pace - network, "remaining": end - start - network_and_pace,
            "after_network": end - merged_network[-1][1] if merged_network else None}


def _stats(values):
    values = sorted(value for value in values if value is not None)
    if not values:
        return {"n": 0, "median": None, "p95": None}
    return {"n": len(values), "median": round(median(values), 3),
            "p95": round(values[ceil(0.95 * len(values)) - 1], 3)}


def summarize_pairs(samples):
    """Accept measured pairs in order and exclude a final incomplete pair."""
    results = {"original": [], "nice": []}
    pending = None
    selected = None
    for index, sample in enumerate(samples):
        case = sample["operation"], sample["condition"]
        if case[0] not in OPERATIONS or case[1] not in CONDITIONS:
            raise ValueError("unknown operation or condition")
        if selected is not None and selected != case:
            raise ValueError("mixed operation or condition")
        selected = case
        pair, position = divmod(index, 2)
        order = ("original", "nice") if pair % 2 == 0 else ("nice", "original")
        side = order[position]
        if sample["pair"] != pair + 1 or sample["app"] != side:
            raise ValueError("unexpected pair order")
        metrics = trial_metrics(sample)
        if position == 0:
            pending = metrics
        else:
            results[order[0]].append(pending)
            results[side].append(metrics)
    complete = len(results["original"])
    return {
        "case": dict(zip(("operation", "condition"), selected)) if selected else None,
        "complete_pairs": complete,
        "minimum_met": complete >= 10,
        "apps": {
            side: {key: _stats(row[key] for row in rows) for key in rows[0]} if rows else {}
            for side, rows in results.items()
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("samples", type=Path, help="paired interval JSON below the D6 S: scratch directory")
    args = parser.parse_args(argv)
    from preflight import SCRATCH, copied_path

    try:
        path = copied_path(SCRATCH, args.samples)
        with path.open("rb") as stream:
            data = stream.read((1 << 20) + 1)
        if len(data) > 1 << 20:
            raise ValueError("sample file too large")
        samples = json.loads(data)
        fields = {"app", "pair", "operation", "condition", "start_ms", "paint_ms",
                  "network_ms", "api_ms", "image_ms", "pace_ms"}
        if type(samples) is not list or any(type(row) is not dict or row.keys() != fields for row in samples):
            raise ValueError("invalid sample schema")
        report = summarize_pairs(samples)
    except Exception:
        print('{"status": "invalid_samples"}')
        return 2
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
