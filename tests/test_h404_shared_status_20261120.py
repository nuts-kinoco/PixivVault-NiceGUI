import time

import pytest
from nicegui import Client, ui

from pixivvault_nicegui.managed_network_stop_shared_scope import ManagedUserNetworkStopScope
from pixivvault_nicegui.pixiv_read_client import HTTPStatusRejected, PublicImageReadClient, TransportFailed
from pixivvault_nicegui.preferences import RequestPreferences
from pixivvault_nicegui.request_policy import RequestRetryPolicy
from pixivvault_nicegui.ui.request_status_view import RequestStatusView, retry_status_text
from test_stage5_r1_user_shared_scope import generated_sealed_shared_owner


@pytest.mark.parametrize(('method', 'args', 'path'), [
    ('get_image_bytes', ('https://i.pximg.net/generated/missing.png',), '/generated/missing.png'),
    ('get_discovery_work', ('123',), '/ajax/illust/123'),
    ('get_recommendation_work', ('123',), '/ajax/illust/123'),
    ('get_artwork_pages', ('123',), '/ajax/illust/123/pages'),
    ('get_author_novel_ids', ('789',), '/ajax/user/789/profile/all'),
    ('get_ranking', (), '/ranking.php'),
    ('search_illusts', ('generated',), '/ajax/search/artworks/generated'),
])
def test_missing_item_remains_local_to_the_caller(tmp_path, method, args, path):
    root, cookie = generated_sealed_shared_owner(tmp_path)
    scope = ManagedUserNetworkStopScope(root)
    calls = []

    def missing(request, *_):
        calls.append(request.selector.split('?', 1)[0])
        raise HTTPStatusRejected(404)

    reader = scope.create_reader(cookie, transport=missing)
    reader._verified_identity = '55'
    policy = RequestRetryPolicy(lambda: RequestPreferences(.6, 3, 5.0))
    reader.set_retry_policy(policy)
    client = Client(ui.page('/_h404_status'))
    try:
        with client:
            header = RequestStatusView(policy)
            with pytest.raises(HTTPStatusRejected) as failure:
                getattr(reader, method)(*args)
            header.refresh()

            assert failure.value.status_code == 404
            assert calls == [path]
            assert scope.admit_job(None) is True
            assert not header.label.visible, header.label.text
            assert policy.progress_snapshot() == ()
    finally:
        header.close()
        client.delete()
        reader.close()


@pytest.mark.parametrize(('status', 'attempts', 'expected'), [
    (401, 1, 'HTTP 401'),
    (403, 1, 'HTTP 403'),
    (429, 3, '共有停止しました'),
    (None, 3, '通信エラー'),
])
def test_shared_header_keeps_actionable_failures(status, attempts, expected):
    now = time.monotonic()
    waits = {}
    slept = 0
    calls = []

    def sleep(seconds):
        nonlocal now, slept
        records = policy.progress_snapshot()
        waits.setdefault(records[0]['attempt'], retry_status_text(records))
        slept += seconds
        now += seconds

    def rejected(*_):
        calls.append(1)
        if status is None:
            raise TransportFailed()
        raise HTTPStatusRejected(status)

    reader = PublicImageReadClient(transport=rejected)
    policy = RequestRetryPolicy(lambda: RequestPreferences(.6, 3, 5.0),
                                clock=lambda: now, sleep=sleep, jitter=lambda *_: 0)
    reader.set_retry_policy(policy)
    try:
        with pytest.raises(TransportFailed if status is None else HTTPStatusRejected):
            reader.get_image_bytes('https://i.pximg.net/generated/rejected.png')
        assert len(calls) == attempts
        assert expected in retry_status_text(policy.progress_snapshot())
        if status == 429:
            assert set(waits) == {1, 2}
            assert slept == pytest.approx(10)
            assert all('HTTP 429のため再試行待機中' in text for text in waits.values())
            with pytest.raises(HTTPStatusRejected) as blocked:
                reader.get_image_bytes('https://i.pximg.net/generated/blocked.png')
            assert blocked.value.status_code == 429
            assert len(calls) == attempts
    finally:
        reader.close()
