"""Complete S10 snapshots have one admission point before registry publication.

Generated native profile/HTTP only; real service, registry, reader and lifecycle.
The post-publication barriers are observation points, never replacement results.
"""
from contextlib import contextmanager
from threading import Event, Thread, current_thread

import pytest

from test_native_bookmark_export_binding import native_context


@pytest.mark.asyncio
async def test_admitted_bookmark_publication_survives_independent_reverification(
        native_context, monkeypatch):
    context = native_context.build()
    service, reader, exports = context.service, context.reader, context.exports
    old_token = service.images._current
    entered, release = Event(), Event()
    errors, published = [], []
    actual_transport = reader._transport
    actual_publish = service.images.publish

    def transport(request, *args):
        if current_thread() is worker and request.full_url == 'https://www.pixiv.net/':
            entered.set()
            assert release.wait(8), 'owned re-verification gate was not released'
        return actual_transport(request, *args)

    def verify_again():
        try:
            assert reader.verify_identity() == '55'
        except BaseException as error:
            errors.append(error)

    worker = Thread(target=verify_again)

    def publish_then_reverify(local, remote):
        token = actual_publish(local, remote)
        published.append((token, remote.client))
        # An ordinary second client starts real verification after the new
        # version was published and the unpinned prior version was pruned.
        worker.start()
        assert entered.wait(4)
        assert reader._verified_identity is None
        assert old_token not in service.images._versions
        return token

    monkeypatch.setattr(reader, '_transport', transport)
    monkeypatch.setattr(service.images, 'publish', publish_then_reverify)
    failure = None
    observed = None
    try:
        try:
            remote, local = await service.refresh_bookmark_cache()
            observed = (remote, local, exports.account, reader._verified_identity,
                        service.images._current, service._remote_records)
        except Exception as error:
            failure = error
    finally:
        release.set()
        if worker.ident is not None:
            worker.join(10)
    assert not worker.is_alive() and not errors
    assert failure is None, (type(failure).__name__, str(failure))
    assert observed is not None and len(published) == 1
    remote, local, account, identity_during_other_get, token, records = observed
    assert tuple(item.work_id for item in remote) == ('701',)
    assert tuple(item.work_id for item in records) == ('701',)
    assert local == service._cached_local_bookmarks
    assert account == '55' and identity_during_other_get is None
    assert (token, reader) == published[0]
    assert reader._verified_identity == '55'
    assert not exports.jobs.snapshot() and not service.static_jobs.snapshot()
    assert service.pixiv_actions is None


@pytest.mark.asyncio
async def test_close_after_bookmark_admission_drains_the_accepted_publication(
        native_context, monkeypatch):
    context = native_context.build()
    service, exports = context.service, context.exports
    entered, release = Event(), Event()
    errors, published = [], []
    actual_publish = service.images.publish
    actual_close_actions = service._close_pixiv_actions

    def before_resource_close():
        actual_close_actions()
        entered.set()
        assert release.wait(8), 'owned close gate was not released'

    def close_owner():
        try:
            service.close()
        except BaseException as error:
            errors.append(error)

    worker = Thread(target=close_owner)

    def publish_then_close(local, remote):
        token = actual_publish(local, remote)
        published.append(token)
        worker.start()
        assert entered.wait(4)
        assert service.closing and not service.closed
        return token

    monkeypatch.setattr(service, '_close_pixiv_actions', before_resource_close)
    monkeypatch.setattr(service.images, 'publish', publish_then_close)
    failure = None
    observed = None
    try:
        try:
            remote, local = await service.refresh_bookmark_cache()
            observed = (remote, local, exports.account, service.images._current,
                        service._remote_records)
        except Exception as error:
            failure = error
    finally:
        release.set()
        if worker.ident is not None:
            worker.join(10)
    assert not worker.is_alive() and not errors
    assert failure is None, (type(failure).__name__, str(failure))
    assert observed is not None and len(published) == 1
    remote, local, account, token, records = observed
    assert tuple(item.work_id for item in remote) == ('701',)
    assert tuple(item.work_id for item in records) == ('701',)
    assert account == '55' and token == published[0]
    assert local == service._cached_local_bookmarks
    assert service.closed and context.reader._closed and exports.stop.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['invalidated', 'closing'])
async def test_late_change_before_bookmark_admission_keeps_all_previous_state(
        native_context, monkeypatch, change):
    context = native_context.build()
    service, exports = context.service, context.exports
    old = (service.images._current, service._remote_records,
           service._cached_remote_bookmarks, service._cached_local_bookmarks)
    original_binding = exports.verified_account_binding

    @contextmanager
    def change_at_admission(account):
        with original_binding(account):
            # All route/catalog reads have finished, but the service's final
            # admission guard has not run. Nothing may publish in this case.
            if change == 'invalidated':
                context.reader._verified_identity = None
            else:
                service.closing = True
            yield

    monkeypatch.setattr(exports, 'verified_account_binding', change_at_admission)
    try:
        with pytest.raises(RuntimeError):
            await service.refresh_bookmark_cache()
        assert (service.images._current, service._remote_records,
                service._cached_remote_bookmarks, service._cached_local_bookmarks) == old
        assert exports.account is None and not exports.jobs.snapshot()
    finally:
        service.closing = False
