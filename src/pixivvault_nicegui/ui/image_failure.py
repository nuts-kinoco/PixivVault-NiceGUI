"""A visible QImg failure slot instead of a silent missing thumbnail."""
from nicegui import ui


def image_failure_slot(image):
    with image.add_slot('error'):
        with ui.column().classes('absolute-full flex-center q-pa-sm').props(
                'role=status data-testid=thumbnail-unavailable'):
            ui.icon('broken_image').style('font-size:32px')
            ui.label('画像を取得できませんでした').classes('text-caption text-center')
            ui.tooltip('通信の混雑や取得失敗で画像を表示できません。処理が終わってから画面を再表示してください。')
    return image
