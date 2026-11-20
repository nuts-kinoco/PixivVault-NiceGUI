from pixivvault_nicegui.library_filters import LibraryFilters
from pixivvault_nicegui.library_models import LibrarySnapshot, LibraryWork
from pixivvault_nicegui.library_state import LibraryState
from pixivvault_nicegui.models import Artwork


def work(number, author, title, tags):
    return LibraryWork(Artwork(str(number), title, author, tags=tags, author_id=author), author)


def test_sparse_query_filters_only_possible_matches(monkeypatch):
    works = tuple(work(i, f'author-{i}', 'target' if i == 17 else 'other', ('keep',))
                  for i in range(100))
    state = LibraryState(LibrarySnapshot(works))
    state.set_filters(LibraryFilters(include=frozenset({'keep'})))
    calls = []
    original = LibraryFilters.matches_artwork

    def counted(self, artwork, **kwargs):
        calls.append(artwork.work_id)
        return original(self, artwork, **kwargs)

    monkeypatch.setattr(LibraryFilters, 'matches_artwork', counted)
    state.set_query('target')
    assert [author.author_id for author in state.visible_authors] == ['author-17']
    assert calls == ['17']


def test_query_and_filter_must_match_same_work_and_filtered_invalid_text_is_ignored():
    state = LibraryState(LibrarySnapshot((
        work(1, 'first', 'needle', ('excluded',)),
        work(2, 'first', 'other', ('keep',)),
        work(3, 'second', 'NEEDLE', ('keep',)),
        work(4, 'third', 123, ('excluded',)),
    )))
    state.set_filters(LibraryFilters(include=frozenset({'keep'})))
    state.set_query('needle')
    assert [author.author_id for author in state.visible_authors] == ['second']
