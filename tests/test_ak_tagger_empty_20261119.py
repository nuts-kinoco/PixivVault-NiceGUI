from types import SimpleNamespace
from nicegui import Client, ui
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.learning_view import LearningView


def test_empty_card_opens_preferences():
    services = ApplicationServices(3)
    client = Client(ui.page('/ak-empty'))
    called = []
    try:
        with client:
            view = LearningView(services, on_open_preferences=lambda: called.append(True))
            texts = [getattr(item, 'text', None) for item in view.empty_card.descendants()]
            assert '画像からのタグ補完にはローカルモデルの導入（任意）が利用できます。' in texts
            handler = next(x.handler for x in view.tagger_settings_button._event_listeners.values() if x.type == 'click')
            handler(SimpleNamespace(args={}))
            assert called == [True]
            view.close()
    finally:
        client.delete()
        services.close()


def test_saved_local_seed_is_visible_in_trend():
    client = Client(ui.page('/ak-saved-seed'))
    try:
        with client:
            view = SimpleNamespace(trend_content=ui.column(), trend_label=ui.label())
            LearningView._render_trend(view, {'judged': 0, 'ready': False, 'rows': (),
                                'local_seed_rows': ({'tag': 'cat', 'display': 'cat', 'count': 3},)})
            texts = [getattr(item, 'text', None) for item in view.trend_content.descendants()]
            assert 'cat' in texts
            assert '保存 3 件' in texts
    finally:
        client.delete()
