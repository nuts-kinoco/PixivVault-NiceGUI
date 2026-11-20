"""P4 OLD45 download entry, acceptance, and failure reporting on generated data."""
import asyncio
from email.message import Message
from io import BytesIO
import json

from nicegui import Client, ui
import pytest

from pixivvault_nicegui import extension_bridge
from pixivvault_nicegui.job_failure import job_failure_text
from pixivvault_nicegui.managed_novel_jobs import PinnedNovelSource
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.shell import DemoShell
from test_extension_snapshot_and_stop_messages import Server
from test_f37_library_bulk_download import DetailSource
from test_managed_download_services import terminal
from test_managed_download_view import record


@pytest.mark.asyncio
async def test_single_id_in_default_download_details_reveals_the_checkbox_and_queues(tmp_path):
    source = DetailSource()
    source.detail = record(202)
    services = ApplicationServices(live_client=source, pixiv_download_root=tmp_path / 'managed')
    client = Client(ui.page('/_p4b_single_id'))
    try:
        with client:
            shell = DemoShell(services)
            download = shell.job_views['download']
            assert download.open_details()
            view = download.managed
            assert view.author_panel.visible and not view.bookmark_panel.visible
            view.direct_input.set_value('202')
            assert await view.lookup_work_id()
            assert view.bookmark_panel.visible and not view.author_panel.visible
            assert 'チェックを付けて' in view.notice.text
            assert '202' in view._choices
            assert not view.submit_button.enabled and not services.jobs.snapshot()

            view.select('202', True)
            assert view.submit_button.enabled and await view.submit()
            assert '1件をキューへ追加しました' in view.notice.text
            job, = services.jobs.snapshot()
            assert job.target_id == '202'
            result = await asyncio.to_thread(terminal, services.static_jobs, job.job_id)
            assert result.done == result.total == 2
            assert services.managed_downloads.verified_saved_receipt('202') is not None
    finally:
        client.delete()
        await asyncio.to_thread(services.close)


@pytest.mark.asyncio
async def test_practical_novel_download_details_shows_guidance_and_saves_selected_format(tmp_path):
    class Source(DetailSource):
        def require_identity(self):
            return self.verify_identity()

        def get_novel_payload(self, work_id, author_id):
            assert author_id == '23'
            if work_id == '999':
                raise OSError('synthetic private exception detail')
            assert work_id == '123'
            return dict(id=work_id, userId=author_id, userName='架空の作者',
                        title='架空の小説', content='架空の小説本文')

    source = Source()
    services = ApplicationServices(
        live_client=source, pixiv_download_root=tmp_path / 'managed',
        novel_fixture_source=PinnedNovelSource(source, {}),
        novel_fixture_root=tmp_path / 'novels',
    )
    client = Client(ui.page('/_p4b_novel_lookup'))
    try:
        with client:
            shell = DemoShell(services)
            download = shell.job_views['download']
            assert download.open_details()
            download.legacy.select('novel')
            view = download.legacy.views['novel']
            view.author_input.set_value('23')
            view.change_work_id('999')
            assert not await view.lookup()
            assert '小説ID・作者ID' in view.notice.text
            assert '合成ID' not in view.notice.text
            assert 'synthetic private exception detail' not in view.notice.text
            assert not view.submit_button.enabled and not services.jobs.snapshot()

            view.change_work_id('123')
            view.change_format('BOTH')
            assert await view.lookup() and view.submit_button.enabled
            assert '架空の小説本文' in view.preview_body.text
            assert await view.submit()
            assert 'キューに追加しました' in view.notice.text
            owner = services.novel_jobs
            job, = owner.jobs.snapshot()
            await asyncio.to_thread(terminal, owner.jobs, job.job_id)
            saved = owner.current('123', 'BOTH')
            assert saved is not None and set(saved.output_names) == {
                '架空の小説(123).epub', '架空の小説(123).txt',
            }
            folder = (owner.store.root / saved.relative_receipt).parent
            assert '架空の小説本文' in (folder / '架空の小説(123).txt').read_text(encoding='utf-8-sig')
    finally:
        client.delete()
        await asyncio.to_thread(services.close)


def post_extension_download(bridge):
    body = b'{"type":"work","work_id":"202","is_novel":false}'
    server = bridge._server
    handler = server.handler.__new__(server.handler)
    handler.server = server
    handler.path = '/download'
    handler.client_address = ('127.0.0.1', 5)
    handler.headers = Message()
    handler.headers['Host'] = f'127.0.0.1:{server.server_port}'
    handler.headers['Origin'] = 'chrome-extension://' + 'a' * 32
    handler.headers['Content-Type'] = 'application/json'
    handler.headers['Content-Length'] = str(len(body))
    handler.rfile, handler.wfile = BytesIO(body), BytesIO()
    handler.request_version = 'HTTP/1.1'
    handler.command = 'POST'
    handler.requestline = 'POST /download HTTP/1.1'
    handler.do_POST()
    headers, data = handler.wfile.getvalue().split(b'\r\n\r\n', 1)
    assert headers.split(b'\r\n', 1)[0].split()[1] == b'200'
    response = json.loads(data)
    assert response['status'] == 'ok'
    return response['jobId']


@pytest.mark.asyncio
@pytest.mark.parametrize('failure, expected_code', [
    (None, ''),
    (PermissionError, 'permission_denied'),
    (RuntimeError, 'runner_failed'),
])
async def test_extension_posts_to_real_managed_queue_and_shows_safe_failure(
    tmp_path, monkeypatch, failure, expected_code,
):
    class Source(DetailSource):
        def get_image_bytes(self, url):
            if failure is not None:
                raise failure('synthetic private exception detail')
            return super().get_image_bytes(url)

    source = Source()
    source.detail = record(202)
    services = ApplicationServices(live_client=source, pixiv_download_root=tmp_path / 'managed')
    client = Client(ui.page('/_p4b_extension'))
    try:
        monkeypatch.setattr(extension_bridge, '_Server', Server)
        monkeypatch.setattr(extension_bridge, '_port_answers', lambda _port: False)
        commands = services.enable_extension_commands()
        services.start_extension_bridge()
        assert services.extension_bridge_status == 'running'
        parent_id = post_extension_download(services.extension_bridge)
        state = 'error' if failure else 'done'
        parent = await asyncio.to_thread(terminal, commands.jobs, parent_id, state)
        child, = services.static_jobs.snapshot()
        assert child.target_id == '202' and child.state == state
        assert child.error_code == expected_code
        assert parent.error_code == expected_code
        with client:
            shell = DemoShell(services)
            shell.show('queue')
            queue = shell.job_views['queue']
            assert parent_id in queue.rows and child.job_id in queue.rows
            if failure:
                shell.show('failed')
                failed = shell.job_views['failed']
                phrase = job_failure_text(expected_code) or '処理に失敗しました'
                for view in (queue, failed):
                    reason = view.rows[parent_id]['result']
                    assert reason.visible and phrase in reason.text
                    assert 'synthetic private exception detail' not in reason.text
                assert services.managed_downloads.verified_saved_receipt('202') is None
            else:
                assert services.managed_downloads.verified_saved_receipt('202') is not None
    finally:
        client.delete()
        await asyncio.to_thread(services.close)
