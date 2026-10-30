"""S03 discovery consumes current, account-scoped local snapshots; no network."""
import pytest

from pixivvault_nicegui.discovery_learning import DiscoveryLearningService
from pixivvault_nicegui.services import ApplicationServices


def author(uid):
    return dict(user_id=uid, name=f'Synthetic {uid}',
                account=f'synthetic_{uid}', profile_img='')


class Reader:
    def __init__(self, public=('41',), private=(), account='9001'):
        self.account = account
        self._verified_identity = account
        self.rows = {'show': tuple(author(uid) for uid in public),
                     'hide': tuple(author(uid) for uid in private)}
        self.calls = []
        self.closed = False

    def require_identity(self):
        self.calls.append(('identity', self.account))
        self._verified_identity = self.account
        return self.account

    def get_following_users(self, uid, rest='show'):
        assert uid == self.account
        self.calls.append(('basic', rest))
        return self.rows[rest]

    def get_following_users_detailed(self, uid, rest='show'):
        assert uid == self.account
        self.calls.append(('detailed', rest))
        return self.rows[rest]

    def get_user_works(self, uid, **kwargs):
        self.calls.append(('works', uid))
        return []

    def get_recommended_users(self, uid, **kwargs):
        self.calls.append(('related', uid))
        return {'users': [], 'works': []}

    def get_ranking(self, **kwargs):
        self.calls.append(('ranking', None))
        return []

    def search_recommendation_illusts(self, word, **kwargs):
        self.calls.append(('search', word))
        return {'works': []}

    def close(self):
        self.closed = True


def application(tmp_path, reader):
    return ApplicationServices(live_client=reader,
        following_sync_root=tmp_path / 'following',
        discovery_root=tmp_path / 'discovery')


async def local_call(discovery, fn):
    return await discovery._wait(discovery._submit(fn))


async def seed_legacy_following(discovery):
    # These are the same local rows retained by the one-time legacy seed.
    # Only this test's owned discovery DB is written.
    await local_call(discovery,
        lambda: discovery._database().save_following_users([author('41')]))


def source_ids(reader, source):
    return {value for kind, value in reader.calls if kind == source}


def assert_no_following_list_reads(reader):
    assert not any(kind in ('basic', 'detailed') for kind, _ in reader.calls)


@pytest.mark.asyncio
async def test_next_batch_uses_sync_from_another_client_without_refresh_get(tmp_path):
    reader = Reader()
    services = application(tmp_path, reader)
    try:
        await seed_legacy_following(services.discovery)
        await services.refresh_discovery_context()
        await services.discovery.fetch_candidates()
        assert source_ids(reader, 'works') == {'41'}
        assert source_ids(reader, 'related') == {'41'}

        # A different UI/client commits to the same app-owned store. There is
        # intentionally no callback to refresh the first client's discovery.
        synced = services.following_sync.sync(Reader(public=('42',)))
        assert [row.user_id for row in synced.following] == ['42']
        reader.calls.clear()
        batch = await services.discovery.fetch_candidates()
        assert not batch.failed
        assert source_ids(reader, 'works') == {'42'}
        assert source_ids(reader, 'related') == {'42'}
        assert_no_following_list_reads(reader)
    finally:
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('refresh_context', [False, True])
async def test_synced_zero_never_falls_back_to_retained_seed_rows(tmp_path, refresh_context):
    reader = Reader()
    services = application(tmp_path, reader)
    try:
        await seed_legacy_following(services.discovery)
        await services.refresh_discovery_context()
        synced = services.following_sync.sync(Reader(public=()))
        assert synced.last_synced_at is not None and synced.following == ()
        if refresh_context:
            await services.refresh_discovery_context()
        reader.calls.clear()
        batch = await services.discovery.fetch_candidates()
        assert not batch.failed
        assert source_ids(reader, 'works') == set()
        assert source_ids(reader, 'related') == set()
        assert_no_following_list_reads(reader)
        old_rows = await local_call(services.discovery,
            lambda: services.discovery._database().get_following_users())
        assert [row['user_id'] for row in old_rows] == ['41']
    finally:
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['omitted-argument', 'unsynced-application'])
async def test_unknown_following_keeps_existing_local_fallback(tmp_path, mode):
    reader = Reader()
    services = application(tmp_path, reader) if mode == 'unsynced-application' else None
    discovery = services.discovery if services else DiscoveryLearningService(tmp_path / 'discovery', reader)
    try:
        await seed_legacy_following(discovery)
        if services:
            assert services.following_sync.snapshot('9001').last_synced_at is None
            assert services.discovery_following_snapshot() is None
            assert reader.calls == []
        batch = await discovery.fetch_candidates()
        assert not batch.failed
        assert source_ids(reader, 'works') == {'41'}
        assert source_ids(reader, 'related') == {'41'}
        assert_no_following_list_reads(reader)
    finally:
        (services if services else discovery).close()


def test_provider_reads_current_account_without_identity_or_list_get(tmp_path):
    reader = Reader()
    services = application(tmp_path, reader)
    try:
        services.following_sync.sync(Reader(public=('41',), account='9001'))
        services.following_sync.sync(Reader(public=('71',), account='9002'))
        assert reader.calls == []
        assert [row['user_id'] for row in services.discovery_following_snapshot()] == ['41']
        reader._verified_identity = '9002'
        assert [row['user_id'] for row in services.discovery_following_snapshot()] == ['71']
        reader._verified_identity = '9003'
        assert services.discovery_following_snapshot() is None
        reader._verified_identity = None
        assert services.discovery_following_snapshot() is None
        assert reader.calls == []
    finally:
        services.close()

