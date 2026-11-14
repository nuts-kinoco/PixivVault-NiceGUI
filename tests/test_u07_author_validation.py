"""Manual author selection must verify existence before adding a target."""
import inspect
from types import SimpleNamespace

import pytest

import pixivvault_nicegui.ui.managed_download_view as module


class Label:
    def set_text(self, value):
        self.text = value

    def set_enabled(self, value):
        self.enabled = value


def make_view(monkeypatch, lookup):
    monkeypatch.setattr(module, '_browser_event', lambda *_a, **_kw: True)
    view = module.ManagedDownloadView.__new__(module.ManagedDownloadView)
    view.active, view.closed, view.author_loading, view.epoch = True, False, False, 1
    view.author_input = SimpleNamespace(value='99999999999')
    view.author_targets, view.author_revision = (), 0
    view.author_status_label, view.author_add_button = Label(), Label()
    view._render_author_targets = lambda: None
    view._render_follow_list = lambda: None
    view.services = SimpleNamespace(live_client=SimpleNamespace(get_author_artworks=lookup))
    return view


@pytest.mark.asyncio
async def test_unknown_author_is_not_added_and_id_is_reported(monkeypatch):
    def unknown(_):
        raise ValueError('synthetic author not found')
    view = make_view(monkeypatch, unknown)
    result = view._author_add_event(SimpleNamespace(args={'epoch': '1'}))
    if inspect.isawaitable(result):
        await result
    assert view.author_targets == ()
    assert '99999999999' in view.author_status_label.text


@pytest.mark.asyncio
async def test_verified_author_is_added_after_lookup(monkeypatch):
    calls = []
    view = make_view(monkeypatch, lambda uid: calls.append(uid) or ())
    await view._author_add_event(SimpleNamespace(args={'epoch': '1'}))
    assert calls == ['99999999999']
    assert tuple(target.user_id for target in view.author_targets) == ('99999999999',)
