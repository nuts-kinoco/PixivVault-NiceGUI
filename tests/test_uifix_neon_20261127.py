from pixivvault_nicegui.ui.theme import CSS
from pixivvault_nicegui.ui.failed_records_view import CSS as FAILED_CSS
from pathlib import Path


def test_automation_scheduler_expands_in_dialog_only():
    assert '.pv-automation-scroll .pv-scheduler-view' in CSS
    assert 'flex:none; overflow:visible' in CSS.split('.pv-automation-scroll .pv-scheduler-view', 1)[1].split('}', 1)[0]


def test_selected_controls_share_the_library_highlight():
    block = next(block for block in CSS.split('}') if 'rgba(33,150,243,.12)' in block and '.pv-card-size.pv-size-selected' in block)
    assert '.pv-library-sort[aria-pressed=true]' in block
    assert '.pv-learning-mode[aria-pressed=true]' in block


def test_failed_empty_recovery_is_centered_and_outlined():
    source = (Path(__file__).parents[1] / 'src/pixivvault_nicegui/ui/failed_records_view.py').read_text(encoding='utf-8')
    assert 'text-align:center' in FAILED_CSS.split('.pv-failure-empty {', 1)[1].split('}', 1)[0]
    assert 'justify-content:center' in FAILED_CSS.split('.pv-failure-recovery {', 1)[1].split('}', 1)[0]
    assert 'outline dense data-testid=failed-clear-search' in source
