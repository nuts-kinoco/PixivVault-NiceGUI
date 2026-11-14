from contextlib import contextmanager
import sqlite3
import os
import json
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from nicegui import Client, ui
from required_network_guard import no_network
from test_author_media_view import parent
from pixivvault_nicegui.author_media import AuthorMediaService
from pixivvault_nicegui.ui.author_media_view import AuthorMediaView


@pytest.fixture(scope='session', autouse=True)
def import_provenance():
    import pixivvault_nicegui.author_media as service
    import pixivvault_nicegui.ui.author_media_view as view
    root = Path(os.environ['U11_SOURCE_ROOT']).resolve()
    imports = {}
    for module in (service, view):
        path = Path(module.__file__).resolve()
        assert path.is_relative_to(root), str(path)
        imports[module.__name__] = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    evidence = {'pid': os.getpid(), 'imports': imports, 'env': {key:os.environ.get(key) for key in ('TEMP','TMP','TMPDIR','XDG_CACHE_HOME','MPLCONFIGDIR','PYTHONDONTWRITEBYTECODE')}}
    (Path(os.environ['U11_EVIDENCE']) / 'actual-process.json').write_text(json.dumps(evidence,indent=2))


@contextmanager
def component(tmp_path):
    source = AuthorMediaService(SimpleNamespace(
        closing=False, managed_downloads=SimpleNamespace(root=tmp_path)))
    source.enable_zip_settings()
    p, _ = parent()
    p.services.author_media = source
    p.services.save_exports = object()
    client = Client(ui.page('/_u11_failmsg'))
    try:
        with client:
            yield AuthorMediaView(p), source
    finally:
        client.delete()
        source.close()


@pytest.mark.asyncio
async def test_actual_revision_conflict_reports_reason_and_preserves_other_save(tmp_path):
    with component(tmp_path) as (view, source):
        assert await view.zip_setting(False)
        source.configure_author_zip('91', False, expected_revision=0)
        view.zip_choice.set_value('on')
        assert not await view.zip_setting(True)
        assert '別の画面で変更' in view.setting_status.text
        assert '再読込' in view.setting_status.text
        assert '保存しました' not in view.setting_status.text
        assert source.author_zip_setting('91') is False
        assert source.author_target('91').revision == 1
        assert not view.busy


@pytest.mark.asyncio
@pytest.mark.parametrize('failure,reason', [('readonly','データベースが読み取り専用'), ('locked','データベースが使用中'), ('permission','保存先ファイルにアクセスできません')])
async def test_storage_failure_has_specific_reason(tmp_path, monkeypatch, failure, reason):
    with component(tmp_path) as (view, source):
        assert await view.zip_setting(False)
        before = source.path.read_bytes()
        connect = sqlite3.connect
        blocker = connect(source.path)
        if failure == 'locked': blocker.execute('BEGIN IMMEDIATE')
        def failing_connection(*args, **kwargs):
            if failure == 'permission': raise PermissionError('private-account-cookie-path')
            if failure == 'readonly': return connect(source.path.as_uri() + '?mode=ro', uri=True)
            return connect(source.path, timeout=0)
        try:
            with monkeypatch.context() as patch:
                patch.setattr(sqlite3, 'connect', failing_connection)
                view.zip_choice.set_value('on')
                assert not await view.zip_setting(True)
        finally:
            blocker.close()
        assert '保存できません' in view.setting_status.text
        assert reason in view.setting_status.text
        assert 'private-account-cookie-path' not in view.setting_status.text
        assert '保存しました' not in view.setting_status.text
        assert source.path.read_bytes() == before
        assert source.author_zip_setting('91') is None
        assert source.author_target('91').revision == 0
        assert not view.busy


@pytest.mark.asyncio
async def test_save_before_loading_explains_required_action(tmp_path):
    with component(tmp_path) as (view, source):
        assert not await view.zip_setting(True)
        assert '先にこの作者の設定を読み込んで' in view.setting_status.text
        assert source.author_target('91').revision == 0
        assert not view.busy


@pytest.mark.asyncio
async def test_successful_save_and_reload_keep_product_revision_contract(tmp_path):
    with component(tmp_path) as (view, source):
        assert await view.zip_setting(False)
        view.zip_choice.set_value('on')
        assert await view.zip_setting(True)
        assert view.setting_status.text == 'ZIP設定を保存しました'
        assert view.zip_revision == ('91', 1)
        assert source.author_zip_setting('91') is True
        assert await view.zip_setting(False)
        assert view.zip_choice.value == 'on' and not view.busy


@pytest.mark.asyncio
@pytest.mark.parametrize('saving', [False, True])
async def test_unclassified_failure_is_not_success_and_does_not_expose_exception(tmp_path, monkeypatch, saving):
    with component(tmp_path) as (view, source):
        assert await view.zip_setting(False)
        def failed(*args, **kwargs):
            raise RuntimeError('private-account-cookie-path')
        monkeypatch.setattr(source, 'configure_author_zip' if saving else 'author_zip_setting', failed)
        assert not await view.zip_setting(saving)
        assert ('保存できませんでした' if saving else '読み込めませんでした') in view.setting_status.text
        assert 'private-account-cookie-path' not in view.setting_status.text
        assert source.author_target('91').revision == 0
        assert not view.busy
