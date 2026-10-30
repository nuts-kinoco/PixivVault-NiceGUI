"""Playwright-free structural checks for the strict Stage4 browser gate.

The actual Chromium overlay/clip/opacity cases are exercised separately by
tools/check_stage4_geometry_guard.py in the browser CI job.
"""
import pytest

from tools.check_stage4_visual_geometry_browser import TABS, pctl, require_tabs


def _valid_rows():
    return [{
        "tab": key,
        "elapsed_ms": 1.0,
        "geometry": {
            "horizontalOverflow": False,
            "navOverlap": [],
            "blockedNav": [],
            "invisibleNavLabels": [],
            "blockedControls": [],
            "unreachableControls": [],
            "clippedTextLabels": [],
            "nav": [{"id": f"tab-{key}"}],
        },
    } for key in TABS]


def test_complete_mandatory_navigation_is_accepted():
    assert require_tabs(_valid_rows()) is True


@pytest.mark.parametrize("fault", ["absent", "skipped", "hidden", "overflow", "overlay", "clip"])
def test_missing_unusable_or_covered_mandatory_navigation_is_rejected(fault):
    rows = _valid_rows()
    if fault == "absent":
        rows.pop()
    elif fault == "skipped":
        rows[0]["skipped"] = True
    elif fault == "hidden":
        rows[0]["geometry"]["nav"] = []
    elif fault == "overflow":
        rows[0]["geometry"]["horizontalOverflow"] = True
    elif fault == "overlay":
        rows[0]["geometry"]["blockedControls"] = ["visible-control"]
    else:
        rows[0]["geometry"]["clippedTextLabels"] = ["visible-label"]
    with pytest.raises(AssertionError):
        require_tabs(rows)


def test_percentile_interpolation_and_empty_sample():
    assert pctl([], 0.95) is None
    assert pctl([10.0, 20.0, 30.0], 0.5) == 20.0
    assert pctl([10.0, 20.0, 30.0], 0.95) == 29.0
