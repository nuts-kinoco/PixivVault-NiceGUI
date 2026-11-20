"""R3 follow-up: the hidden-list title lookup must accept PersonalPage rows (real app log: AttributeError 'items')."""
from types import SimpleNamespace

from pixivvault_nicegui.ui.ranking_card_menus import RankingCardMenus


class _Artwork:
    def __init__(self, work_id, title):
        self.work_id = work_id
        self.title = title


class _Record:
    def __init__(self, work_id, title):
        self._art = _Artwork(work_id, title)

    def as_artwork(self):
        return self._art


def _menus(page):
    menus = object.__new__(RankingCardMenus)
    menus.page = page
    menus._hm = lambda: SimpleNamespace(titles={'1': 'known'})
    return menus


def test_titles_accepts_personal_page_rows():
    page = SimpleNamespace(rows=({'item': SimpleNamespace(record=_Record('2', 'personal'))},))
    assert _menus(page)._titles() == {'1': 'known', '2': 'personal'}


def test_titles_accepts_official_page_items_and_none():
    page = SimpleNamespace(items=(SimpleNamespace(record=_Record('3', 'official')),))
    assert _menus(page)._titles()['3'] == 'official'
    assert _menus(None)._titles() == {'1': 'known'}
