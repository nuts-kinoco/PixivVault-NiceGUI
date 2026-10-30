"""K09–K12: original personal ranking controls, synthetic vault only.

The fake is intentionally a *local* vault; search never opens a socket or
reads an actual account. Every assertion exercises real NiceGUI components.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime

from nicegui import Client, background_tasks, core, ui
import pytest

from pixivvault_nicegui.external_vault import VaultError
from pixivvault_nicegui.personal_ranking import normalize_personal_filters
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.personal_ranking_view import PersonalRankingView


def raw(work, author, tags, created='2099-01-01T00:00:00+09:00'):
    return {
        'id': str(work), 'title': f'架空作品 {work}',
        'user': {'id': str(author), 'name': f'架空作者 {author}'},
        'image_urls': {'square_medium':
            f'https://i.pximg.net/img-master/img/2026/01/01/00/00/00/{work}_p0_square1200.jpg'},
        'tags': [{'name': tag} for tag in tags], 'page_count': 1,
        'type': 'illust', 'create_date': created,
    }


ROWS = (
    raw('101', '10', ('green', 'sun')),
    raw('102', '10', ('blue', 'sun')),
    raw('103', '20', ('green', 'old'), created='2020-01-01T00:00:00+09:00'),
    raw('104', '20', ('green', 'sun')),
    raw('105', '30', ('blue', 'rain')),
)


class MemoryVault:
    def __init__(self):
        self.open = True
        self.failed = False
        self.calls = []
        self.saved = {}
        self.saved_count = 0
        self.fail_save = False

    def status(self):
        return {'open': self.open, 'mode': 'nana7mi',
                'transport_available': self.open, 'crypto_available': True}

    def close(self):
        self.open = False

    def ranking_preferences(self):
        return deepcopy(self.saved)

    def save_ranking_preferences(self, tags, filters):
        if self.fail_save:
            raise VaultError('synthetic-preference-write-failed')
        self.saved_count += 1
        self.saved = {'tags': list(tags), 'filters': normalize_personal_filters(filters)}

    def search(self, word, bookmark_min, *, sort, r18, exclude_ai, offset):
        self.calls.append({'word': word, 'bookmark_min': bookmark_min, 'sort': sort,
                           'r18': r18, 'exclude_ai': exclude_ai, 'offset': offset})
        if self.failed:
            raise VaultError('synthetic-failure')
        rows = ROWS if bookmark_min == 0 else tuple(
            item for item in ROWS if item['id'] in {'101', '104'})
        return {'illusts': list(deepcopy(rows)), 'next_url': None}


class Images:
    def __init__(self):
        self.closed = False
    def url(self, *args):
        return '/media/demo-0001/0/thumb.png'
    def can_open(self, _):
        return False
    def needs_metadata(self, _):
        return False
    def close(self):
        self.closed = True


@pytest.fixture
async def personal(monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    vault = MemoryVault()
    services = ApplicationServices(count=1)
    services.external_vault = vault
    pins = []
    def acquire(_records):
        pin = Images()
        pins.append(pin)
        return pin
    services.acquire_ranking_images = acquire
    services.personal_ranking_context = lambda: {
        'hidden_work_ids': set(), 'hidden_author_ids': set(),
        'downloaded_ids': set(), 'hated_ids': set(),
        'trend_rows': [{'tag': 'green', 'display': '架空の候補 green'}]}
    client = Client(ui.page('/_k09_k12_personal_fixture'))
    before = set(background_tasks.running_tasks)
    with client:
        view = PersonalRankingView(services)
        view.set_active(True)
    initial = view._context_task
    if initial is not None:
        await initial
    try:
        yield view, vault, services, pins
    finally:
        view.close()
        tasks = set(background_tasks.running_tasks) - before
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        client.delete()
        services.close()


async def test_k09_k11_k12_local_filter_and_zero_clear_are_immediate_no_refetch(personal):
    view, vault, _, pins = personal
    assert view.page is None and not view.gallery.cards
    assert 'タグ' in view.notice.text
    view.tags.set_value('green')
    assert view.fetch_button.enabled
    assert vault.calls == []
    await view.load()
    assert len(vault.calls) == 1
    assert set(view.gallery.cards) == {'101', '102', '103', '104', '105'}
    view.include.set_value('green')
    assert set(view.gallery.cards) == {'101', '103', '104'}
    view.exclude.set_value('old')
    assert set(view.gallery.cards) == {'101', '104'}
    view.date.set_value('1month')
    assert view.date.value == '1month'
    view.exclude.set_value('')
    assert set(view.gallery.cards) == {'101', '104'}
    view.date.set_value('all')
    view.author_max.set_value(1)
    assert set(view.gallery.cards) == {'101', '103'}
    view.include.set_value('missing-tag-000')
    assert not view.gallery.cards
    assert view.gallery.empty.visible
    assert '0件' in view.notice.text
    assert len(vault.calls) == 1, 'local filtering may not issue another search'
    view.clear_filters()
    assert view.include.value == '' and view.exclude.value == ''
    assert view.author_max.value == 0 and view.date.value == 'all'
    assert view.exclusions['exclude_hidden_works'].value is True
    assert set(view.gallery.cards) == {'101', '102', '103', '104', '105'}
    assert len(vault.calls) == 1
    assert not any(pin.closed for pin in pins)  # no new GET/catalog during local filter


async def test_k10_changed_server_preset_never_labels_old_result_as_new(personal):
    view, vault, _, _ = personal
    view.tags.set_value('green')
    await view.load()
    assert '102' in view.gallery.cards
    view.bookmarks.set_value(500)
    assert view.bookmarks.value == 500
    assert len(vault.calls) == 1
    assert not view.gallery.cards, 'old search results must not masquerade as 500+ results'
    assert '取得' in view.notice.text
    await view.load()
    assert vault.calls[-1]['bookmark_min'] == 500
    assert set(view.gallery.cards) == {'101', '104'}
    view.sort.set_value('date_desc')
    assert not view.gallery.cards
    await view.load()
    assert vault.calls[-1]['sort'] == 'date_desc'
    assert set(view.gallery.cards) == {'101', '104'}
    vault.failed = True
    view.bookmarks.set_value(1000)
    await view.load()
    assert not view.gallery.cards and '検索' in view.notice.text
    assert view.fetch_button.enabled
    vault.failed = False
    await view.load()
    assert set(view.gallery.cards) == {'101', '104'}


async def test_saved_filters_tab_reentry_and_disabled_search_boundaries(personal):
    view, vault, _, _ = personal
    assert view.candidates.value is True
    assert not view.fetch_button.enabled, 'cannot fetch without a selected tag'
    before_click=vault.saved_count
    view.add_tag('green')
    assert vault.saved_count==before_click+1, 'one click must persist only once'
    view.include.set_value('sun')
    view.exclude.set_value('old')
    view.date.set_value('1week')
    view.author_max.set_value(1)
    view.bookmarks.set_value(500)
    last_saved=deepcopy(view.saved_filters)
    vault.fail_save=True
    view.save()
    assert '保存できませんでした' in view.notice.text
    assert view.saved_filters==last_saved, 'failed persistence cannot claim saved filters'
    vault.fail_save=False
    view.save()
    assert vault.saved['filters'] == view.current_filters()
    assert vault.saved['tags'] == ['green']
    await view.load()
    assert set(view.gallery.cards) == {'101', '104'}
    view.set_active(False)
    assert not view.active
    assert not view.fetch_button.enabled
    view.set_active(True)
    assert view.include.value == 'sun' and view.exclude.value == 'old'
    assert view.date.value == '1week' and view.author_max.value == 1
    assert set(view.gallery.cards) == {'101', '104'}
    vault.open = False
    view.availability()
    assert not view.fetch_button.enabled
    calls = len(vault.calls)
    await view.load()
    assert len(vault.calls) == calls, 'forged disabled submit must fail closed'
    view.close()
    assert view.closed
