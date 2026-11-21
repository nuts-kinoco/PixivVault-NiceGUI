"""The D6 measurement harness uses only offline inputs in tests."""

from pathlib import Path
import sqlite3
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "verify" / "d6real-20261121"))
from preflight import inspect_copy
from timing import DispatchObservation, RequestBudget, StopRun, summarize_pairs


def test_preflight_uses_only_copy_and_reports_no_source_paths(tmp_path):
    profile = tmp_path / "profile-native"
    profile.mkdir()
    with sqlite3.connect(profile / "profile.sqlite3") as db:
        db.execute("""CREATE TABLE native_profile_state
            (id, cookie_file, save_parent, library_readonly_db, library_image_root)""")
        db.execute("INSERT INTO native_profile_state VALUES (1, ?, ?, NULL, NULL)", (
            "S:/outside/secret-account/profile-native-credentials/one/cookies.txt",
            "S:/outside/secret-account/native-images",
        ))
    candidate = tmp_path / "profile-native-credentials" / "one" / "cookies.txt"
    candidate.parent.mkdir(parents=True)
    candidate.write_text("malformed and private", encoding="utf-8")

    result = inspect_copy(tmp_path)

    assert result["status"] == "blocked"
    assert result["stored_paths"] == {
        "cookie": "outside_copy", "save": "outside_copy",
        "legacy_db": "unset", "legacy_images": "unset",
    }
    assert result["copied_cookie"] == "invalid"
    assert result["profile_stop"]["state"] == "history_unverified"
    assert result["legacy_library_match"] is False
    assert result["pixiv_requests"] == 0
    assert "secret-account" not in str(result)
    assert "malformed" not in str(result)
    assert sorted(p.name for p in profile.iterdir()) == ["profile.sqlite3"]


@pytest.mark.parametrize("outcome", ["http_401", "http_403", "http_429", "captcha", "authentication"])
def test_abnormal_response_stops_both_versions(outcome):
    budget = RequestBudget()
    calls = []

    def rejected():
        calls.append("original")
        raise StopRun(outcome)

    with pytest.raises(StopRun, match=f"^{outcome}$"):
        budget.run(rejected)
    with pytest.raises(StopRun, match=f"^{outcome}$"):
        budget.run(lambda: calls.append("nice"))

    assert calls == ["original"]
    assert budget.count == 1


def test_transport_failure_hides_raw_exception():
    budget = RequestBudget()

    def fail():
        raise ValueError("private URL, Cookie, and response")

    with pytest.raises(StopRun, match="^transport$") as caught:
        budget.run(fail)
    assert caught.value.__suppress_context__


def test_pace_is_shared_and_cap_is_hard():
    class Clock:
        now = 25.0

        def read(self):
            return self.now

        def sleep(self, seconds):
            self.now += seconds

    clock = Clock()
    budget = RequestBudget(clock=clock.read, sleep=clock.sleep)
    started = []

    def observe():
        started.append(clock.read())
        return DispatchObservation(status=200)

    for _ in range(180):
        budget.run(observe)
    with pytest.raises(StopRun, match="^budget$"):
        budget.run(observe)
    assert len(started) == budget.count == 180
    assert all(b - a >= 0.875 for a, b in zip(started, started[1:]))


def test_pair_timing_excludes_overlap_and_keeps_pairs_ordered():
    def pair(index):
        order = ("original", "nice") if index % 2 == 0 else ("nice", "original")
        return [
            {"app": side, "pair": index + 1, "operation": "ranking_official",
             "condition": "warm", "start_ms": 10, "paint_ms": 110,
             "network_ms": [[25, 70], [50, 80]], "pace_ms": [[15, 30]],
             "api_ms": [[25, 70]], "image_ms": [[50, 80]]}
            for side in order
        ]

    samples = [item for i in range(10) for item in pair(i)]
    cherry_picked = summarize_pairs(samples[:-1])
    assert cherry_picked["complete_pairs"] == 9
    assert cherry_picked["minimum_met"] is False
    result = summarize_pairs(samples)
    assert result["minimum_met"] is True
    assert result["apps"]["original"]["total"] == {"n": 10, "median": 100, "p95": 100}
    assert result["apps"]["nice"]["network"] == {"n": 10, "median": 55, "p95": 55}
    assert result["apps"]["nice"]["api"] == {"n": 10, "median": 45, "p95": 45}
    assert result["apps"]["nice"]["pace"] == {"n": 10, "median": 10, "p95": 10}
    assert result["apps"]["nice"]["remaining"] == {"n": 10, "median": 35, "p95": 35}
    with pytest.raises(ValueError, match="unexpected pair order"):
        summarize_pairs([samples[1], samples[0]])
    samples[0]["network_ms"] = [[25, 111]]
    with pytest.raises(ValueError, match="outside trial"):
        summarize_pairs(samples)

def test_summary_reports_media_and_post_network_separately():
    from timing import trial_metrics

    result = trial_metrics({
        "start_ms": 0, "paint_ms": 100, "network_ms": [[10, 50], [45, 80]],
        "api_ms": [[10, 50]], "image_ms": [[45, 80]], "pace_ms": [[0, 10]]
    })
    assert result == {"total": 100, "network": 70, "api": 40, "image": 35,
                      "pace": 10, "remaining": 20, "after_network": 20}


def test_cli_summary_never_emits_private_input(tmp_path, capsys, monkeypatch):
    import json
    import preflight
    from timing import main

    monkeypatch.setattr(preflight, "SCRATCH", tmp_path)
    data = tmp_path / "samples.json"
    data.write_text(json.dumps([{"url": "https://example.invalid/private-id"}]))
    assert main([str(data)]) == 2
    assert capsys.readouterr().out.strip() == '{"status": "invalid_samples"}'


@pytest.mark.parametrize(("observation", "reason"), [
    (DispatchObservation(status=401), "http_401"),
    (DispatchObservation(status=403), "http_403"),
    (DispatchObservation(status=429), "http_429"),
    (DispatchObservation(status=302), "destination"),
    (DispatchObservation(status=200, challenge=True), "captcha"),
    (DispatchObservation(status=200, authenticated=False), "authentication"),
])
def test_http_status_stops_before_next_dispatch(observation, reason):
    budget = RequestBudget()
    with pytest.raises(StopRun, match=f"^{reason}$"):
        budget.run(lambda: observation)
    with pytest.raises(StopRun, match=f"^{reason}$"):
        budget.run(lambda: pytest.fail("a second request was admitted"))
    assert budget.count == 1


def test_summary_rejects_mixed_operation_or_cache_condition():
    sample = {"app": "original", "pair": 1, "operation": "ranking_official",
              "condition": "warm", "start_ms": 0, "paint_ms": 20,
              "network_ms": [], "api_ms": [], "image_ms": [], "pace_ms": []}
    different = {**sample, "app": "nice", "operation": "bookmarks"}
    with pytest.raises(ValueError, match="mixed operation or condition"):
        summarize_pairs([sample, different])
    different = {**sample, "app": "nice", "condition": "cold"}
    with pytest.raises(ValueError, match="mixed operation or condition"):
        summarize_pairs([sample, different])
