"""Synthetic filename-only presence proofs; never open original media."""

from zipfile import ZipFile

import pytest

from pixivvault_nicegui.library_models import LibrarySnapshot, LibraryWork
from pixivvault_nicegui.local_media_adapter import (
    MediaUnavailable, ReadonlyLocalMediaCatalog,
)
from pixivvault_nicegui.models import Artwork


def make_catalog(root, *, known=True, titles=('作品', '別作品'), pages=(2, 1)):
    works = tuple(LibraryWork(Artwork(str(101 + idx), title, '作者', page_count=count),
                              '12', page_count_known=known)
                  for idx, (title, count) in enumerate(zip(titles, pages)))
    return ReadonlyLocalMediaCatalog(root, LibrarySnapshot(works))


def test_complete_pages_in_folder_and_zip_are_present_without_decoding(tmp_path, monkeypatch):
    from pixivvault_nicegui import local_media_adapter as module
    monkeypatch.setattr(module.Image, 'open', lambda *_args, **_kwargs:
                        pytest.fail('presence must not decode'))
    folder = tmp_path / '作者(12)'
    folder.mkdir()
    (folder / '作品(101)_1.png').write_bytes(b'not an image')
    with ZipFile(tmp_path / '別名(12).zip', 'w') as archive:
        archive.writestr('作品(101)_2.jpg', b'not an image')
    catalog = make_catalog(tmp_path)
    assert catalog.inspect_presence('101') is True
    catalog.close()


def test_partial_missing_proven_only_with_other_confirmed_author_media(tmp_path):
    folder = tmp_path / '作者(12)'
    folder.mkdir()
    (folder / '別作品(102).png').write_bytes(b'other')
    catalog = make_catalog(tmp_path)
    assert catalog.inspect_presence('101') is False
    assert catalog.inspect_presence('102') is True
    catalog.close()


def test_empty_author_and_unknown_page_count_are_unknown(tmp_path):
    catalog = make_catalog(tmp_path)
    assert catalog.inspect_presence('101') is None
    assert catalog.inspect_presence('102') is None
    catalog.close()
    folder = tmp_path / '作者(12)'
    folder.mkdir()
    (folder / '作品(101)_1.png').write_bytes(b'first')
    (folder / '別作品(102).png').write_bytes(b'other')
    catalog = make_catalog(tmp_path, known=False)
    assert catalog.inspect_presence('101') is None
    catalog.close()


def test_unattributed_legacy_name_prevents_false_missing(tmp_path):
    folder = tmp_path / '作者(12)'
    folder.mkdir()
    (folder / '同名_1.png').write_bytes(b'uncertain')
    (folder / '確定(103).png').write_bytes(b'confirmed')
    catalog = make_catalog(tmp_path, titles=('同名', '同名', '確定'), pages=(1, 1, 1))
    assert catalog.inspect_presence('101') is None
    assert catalog.inspect_presence('102') is None
    assert catalog.inspect_presence('103') is True
    catalog.close()


def test_duplicate_distinct_sources_never_prove_presence(tmp_path):
    folder = tmp_path / '作者(12)'
    folder.mkdir()
    (folder / '作品(101)_1.png').write_bytes(b'first')
    with ZipFile(tmp_path / 'コピー(12).zip', 'w') as archive:
        archive.writestr('作品(101)_1.png', b'other')
    catalog = make_catalog(tmp_path)
    assert catalog.inspect_presence('101') is None
    catalog.close()


def test_unreadable_author_fails_closed_and_closing_invalidates(tmp_path):
    with ZipFile(tmp_path / '壊れた(12).zip', 'w') as archive:
        archive.writestr('../escape.png', b'bad')
    catalog = make_catalog(tmp_path)
    assert catalog.inspect_presence('101') is None
    catalog.close()
    with pytest.raises(MediaUnavailable):
        catalog.inspect_presence('101')


def test_file_disappearing_after_index_is_unknown_not_missing(tmp_path):
    folder = tmp_path / '作者(12)'
    folder.mkdir()
    first = folder / '作品(101)_1.png'
    first.write_bytes(b'first')
    (folder / '別作品(102).png').write_bytes(b'other')
    catalog = make_catalog(tmp_path)
    catalog._ensure_index()
    first.unlink()
    assert catalog.inspect_presence('101') is None
    catalog.close()


def test_zip_corruption_after_index_is_unknown_not_missing(tmp_path):
    path = tmp_path / '作者(12).zip'
    with ZipFile(path, 'w') as archive:
        archive.writestr('作品(101)_1.png', b'first')
        archive.writestr('別作品(102).png', b'other')
    catalog = make_catalog(tmp_path)
    catalog._ensure_index()
    path.write_bytes(b'bad archive')
    assert catalog.inspect_presence('101') is None
    catalog.close()


def test_one_existing_page_is_enough_author_evidence_for_other_missing_page(tmp_path):
    folder = tmp_path / '作者(12)'
    folder.mkdir()
    (folder / '作品(101)_1.png').write_bytes(b'first')
    catalog = make_catalog(tmp_path)
    assert catalog.inspect_presence('101') is False
    catalog.close()


def test_unrecognized_page_name_with_known_id_is_not_a_missing_proof(tmp_path):
    folder = tmp_path / '作者(12)'
    folder.mkdir()
    (folder / '作品(101)').mkdir()
    (folder / '作品(101)' / '101_p999.png').write_bytes(b'could be a page')
    (folder / '別作品(102).png').write_bytes(b'other')
    catalog = make_catalog(tmp_path)
    assert catalog.inspect_presence('101') is None
    catalog.close()
