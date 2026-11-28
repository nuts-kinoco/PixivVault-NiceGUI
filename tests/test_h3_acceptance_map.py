import json
from tools.h3_acceptance_map import capture_key, records


def test_conditions_keep_state_size_theme_and_independent_entrances():
    assert capture_key('U-S01_1280x800_dark_empty') == 'empty/1280x800-dark-100/download'
    assert capture_key('U-S18_1280x800_dark_normal') == capture_key('U-S18-automation_1280x800_dark_normal')
    assert capture_key('U-S18-scheduler_1280x800_dark_normal') != capture_key('U-S18-automation_1280x800_dark_normal')
    assert capture_key('U-S27-row-confirm_1920x1080_light_normal') == 'normal/1920x1080-light-100/failed-delete-confirm'


def test_claimed_state_without_image_and_runtime_provenance_is_not_accepted(tmp_path):
    key = capture_key('U-S01_1280x800_dark_empty')
    folder = tmp_path / key
    folder.mkdir(parents=True)
    (folder / 'metrics.json').write_text(json.dumps({'capture': {'state_proven': True, 'product': 'missing.png'}}))
    assert not records(['U-S01_1280x800_dark_empty'], tmp_path)[0]['state_proven']
