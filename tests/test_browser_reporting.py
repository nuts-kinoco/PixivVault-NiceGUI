"""故意の障害を理由に無関係なブラウザエラーを隠さない。"""

from types import SimpleNamespace

from tools.browser_reporting import capture_console_error


def test_only_matching_injected_failure_is_expected():
    result = {'expecting_injected_image_failure': True,
              'console_errors': [], 'expected_injected_console_errors': []}
    def message(path, text='Failed to load resource: 503'):
        return SimpleNamespace(type='error', text=text, location={'url': path})

    capture_console_error(message('http://127.0.0.1:1234/media/demo-0030/0/view.png'), result)
    assert len(result['expected_injected_console_errors']) == 1
    assert result['console_errors'] == []
    capture_console_error(message('/media/demo-0001/0/view.png'), result)
    capture_console_error(message('/media/demo-0030/0/view.png', 'TypeError'), result)
    result['expecting_injected_image_failure'] = False
    capture_console_error(message('/media/demo-0030/0/view.png'), result)
    assert len(result['console_errors']) == 3
