from tools.p5_design_evidence import SCREENS
from tools import ui_tone_evidence as tone
from tools.ui_tone_evidence import comparison, toolbar_rows
from tools.ui_tone_report import toolbar


def test_new_tabs_have_no_flet_tab_counterpart():
    assert SCREENS['scheduler'] is None
    assert SCREENS['metadata'] is None


def test_toolbar_rows_group_overlapping_controls_left_to_right():
    controls = [
        {'label': 'right', 'rect': [80, 6, 30, 30]},
        {'label': 'left', 'rect': [0, 0, 70, 42]},
        {'label': 'next', 'rect': [0, 42, 40, 20]},
    ]
    assert [[item['label'] for item in row] for row in toolbar_rows(controls)] == [
        ['left', 'right'], ['next'],
    ]


def test_comparison_preserves_unobservable_values():
    assert comparison(5, 2) == 3
    assert comparison(0, 2) == -2
    assert comparison(None, 2) is None
    assert comparison(4, None) is None


def test_owned_process_check_ignores_reused_pid(monkeypatch):
    monkeypatch.setattr(tone.os, 'getpid', lambda: 100)
    times = {100:10,200:20}
    records = {200:(100,'node.exe')}
    monkeypatch.setattr(tone, 'creation_time', times.get)
    monkeypatch.setattr(tone, 'process_snapshot', lambda: records)
    owned = tone.OwnedProcesses()
    owned.observe()
    assert owned.observed == {200:(100,'node.exe',20)}

    times.update({200:30,300:40})
    records.update({200:(999,'node.exe'),300:(200,'msedge.exe')})
    owned.observe()
    assert 300 not in owned.observed


def test_toolbar_report_combines_nested_controls_on_one_visual_line():
    groups = [{'rows':[{'controls':[{'rect':[x,0,30,32]}]}]} for x in (5,45)]
    assert toolbar({'toolbars':groups}) == (1,2,[2])
