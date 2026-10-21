"""ブラウザ試験のエラー分類。Playwrightの実行環境には依存しない。"""


def capture_console_error(message, result: dict) -> None:
    """故障注入した画像の503だけを、予期しないコンソール例外から区別する。"""
    if message.type != 'error':
        return
    location = message.location.get('url', '')
    if (result.get('expecting_injected_image_failure', False)
            and '/media/demo-0030/0/view.png' in location
            and '503' in message.text):
        result['expected_injected_console_errors'].append(
            {'url': location, 'message': message.text})
    else:
        result['console_errors'].append(message.text)
