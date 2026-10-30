"""Owned follow-sync integration; no OS GUI, credentials or network."""
from types import SimpleNamespace

import pytest

from pixivvault_nicegui.bootstrap import parse_args
from pixivvault_nicegui.following_sync import FollowingSyncError
from pixivvault_nicegui.native_profile import NativeProfileStore
from pixivvault_nicegui.services import ApplicationServices, get_services


class Reader:
    def __init__(self):
        self.calls = []
        self.closed = False
        self.account = '9001'
        self.rows = {'show': ({'user_id': '41', 'name': 'Public'},),
                     'hide': ({'user_id': '42', 'name': 'Private'},)}

    def require_identity(self):
        self.calls.append('identity')
        return self.account

    verify_identity = require_identity

    def get_following_users(self, uid, rest='show'):
        assert uid == self.account
        self.calls.append(('basic', rest))
        return self.rows[rest]

    def get_following_users_detailed(self, uid, rest='show'):
        assert uid == self.account
        self.calls.append(('detailed', rest))
        return tuple(dict(row, account='synthetic', profile_img='') for row in self.rows[rest])

    def close(self):
        self.closed = True


def test_profile_includes_an_independent_following_root_without_provision(tmp_path):
    cookie = tmp_path / 'credentials' / 'synthetic.txt'
    cookie.parent.mkdir()
    cookie.write_text('synthetic reference', encoding='utf-8')
    parent = tmp_path / 'selected'
    parent.mkdir()
    store = NativeProfileStore(tmp_path / 'profile')
    try:
        saved = store.update(cookie_file=cookie, save_parent=parent, expected_revision=0)
        values = store.native_kwargs(saved)
        config = parse_args(['--native', '--profile-root', str(store.root), '--check'])
        expected = parent / 'PixivVault-NiceGUI' / 'following-sync'
        assert values['following_sync_root'] == expected
        assert config.following_sync_root == expected
        assert not list(parent.iterdir())
        assert expected not in (values['preferences_root'], values['discovery_root'])
    finally:
        store.close()


def test_store_is_app_owned_and_cache_rejects_a_different_root(tmp_path):
    app = SimpleNamespace(state=SimpleNamespace())
    reader = Reader()
    root = tmp_path / 'following'
    service = get_services(app, live_client=reader, following_sync_root=root)
    try:
        assert not reader.calls and service.following_sync is not None
        assert service.following_sync.snapshot('9001').last_synced_at is None
        assert get_services(app, live_client=reader, following_sync_root=root) is service
        with pytest.raises(RuntimeError, match='sidecar|following'):
            get_services(app, live_client=reader, following_sync_root=tmp_path / 'other')
        assert not (tmp_path / 'other').exists()
    finally:
        owner = service.following_sync
        service.close()
    with pytest.raises(FollowingSyncError):
        owner.snapshot('9001')
    assert reader.closed


@pytest.mark.asyncio
async def test_author_choices_use_complete_account_snapshot_and_do_not_treat_unsynced_as_zero(tmp_path):
    reader = Reader()
    service = ApplicationServices(live_client=reader, pixiv_download_root=tmp_path / 'managed',
        following_sync_root=tmp_path / 'following', discovery_root=tmp_path / 'discovery')
    try:
        assert not reader.calls
        initial = await service.followed_download_authors(owner='a' * 32)
        assert {row.user_id for row in initial} == {'41', '42'}
        assert ('basic', 'show') in reader.calls and ('basic', 'hide') in reader.calls
        service.following_sync.sync(reader)
        reader.calls.clear()
        cached = await service.followed_download_authors(owner='a' * 32)
        assert {row.user_id for row in cached} == {'41', '42'}
        assert not any(isinstance(call, tuple) for call in reader.calls)
        await service.refresh_discovery_context()
        assert {row['user_id'] for row in service.discovery_following_snapshot()} == {'41', '42'}
        assert not any(isinstance(call, tuple) for call in reader.calls)
        reader.rows = {'show': (), 'hide': ()}
        service.following_sync.sync(reader)
        reader.calls.clear()
        assert await service.followed_download_authors(owner='a' * 32) == ()
        assert not any(isinstance(call, tuple) for call in reader.calls)
        reader.account = '9002'
        reader.rows = {'show': ({'user_id': '71', 'name': 'Other'},), 'hide': ()}
        choices = await service.followed_download_authors(owner='a' * 32)
        assert {row.user_id for row in choices} == {'71'}
        assert ('basic', 'show') in reader.calls
    finally:
        service.close()


def test_following_initialization_failure_releases_already_created_managed_owner(tmp_path):
    occupied = tmp_path / 'foreign-following'
    occupied.mkdir()
    sentinel = occupied / 'keep.txt'
    sentinel.write_text('do not adopt', encoding='utf-8')
    reader = Reader()
    managed = tmp_path / 'managed'
    with pytest.raises(FollowingSyncError):
        ApplicationServices(live_client=reader, pixiv_download_root=managed,
                            following_sync_root=occupied)
    assert reader.closed and not reader.calls
    assert sentinel.read_text(encoding='utf-8') == 'do not adopt'
    recovered = ApplicationServices(live_client=Reader(), pixiv_download_root=managed)
    recovered.close()


@pytest.mark.parametrize('kind', ['same-managed', 'inside-legacy'])
def test_following_root_fence_rejects_before_creation(tmp_path, kind):
    reader = Reader()
    managed = tmp_path / 'managed'
    kwargs = dict(live_client=reader, pixiv_download_root=managed)
    root = managed
    if kind == 'inside-legacy':
        source = tmp_path / 'frozen'
        source.mkdir()
        db = source / 'old.sqlite3'
        db.write_bytes(b'synthetic boundary fixture; must not be opened')
        images = source / 'images'
        images.mkdir()
        root = source / 'following'
        kwargs.update(library_readonly_db=db, library_image_root=images)
    with pytest.raises(ValueError, match='disjoint'):
        ApplicationServices(**kwargs, following_sync_root=root)
    assert not root.exists() and not managed.exists() and not reader.calls
