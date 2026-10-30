"""CoS unit checks for GUI harness configuration; these do not claim GUI acceptance."""
from fastapi import FastAPI
from tools.check_p3_save_exports_browser import ExportReader,factory
from pixivvault_nicegui.bookmark_remote_adapter import fetch_all_bookmarks


def test_fixture_uses_both_bookmark_routes_and_real_metadata():
    reader=ExportReader()
    result=fetch_all_bookmarks(reader,'91')
    assert result.ok
    assert {(r.kind,r.remote_id,r.visibility) for r in result.records}=={
        ('illustration','701','show'),('illustration','702','show'),('novel','501','show'),('novel','502','hide')}
    assert reader.get_novel_payload('501','91')['content'].endswith('[uploadedimage:1]')


def test_server_factory_has_isolated_export_root_and_owned_scanner_hook(tmp_path):
    app=FastAPI();callbacks=[];app.on_startup=callbacks.append
    services,reader=factory(app,tmp_path/'owned-data')
    try:
        assert services.save_exports is not None
        assert services.save_exports.account=='91'
        assert services.save_exports.root==tmp_path/'owned-data'/'save-exports'
        assert services.save_exports.jobs in services.jobs.owners
        assert callbacks==[services.save_exports.start]
        assert services.save_exports.thread is None
        assert not reader.calls
        assert {'zip_all_after_download','bookmark_download_mode','use_work_folder'}<=services.preference_consumers()
    finally:services.close()


def test_fixture_bookmark_routes_support_product_keyword_and_catalog_positional_contracts():
    reader=ExportReader()
    for method in (reader.get_bookmarked_works,reader.get_bookmarked_novels):
        for visibility in ('show','hide'):
            positional=method('91',visibility)
            keyword=method(my_user_id='91',rest_type=visibility)
            assert positional==keyword
    assert reader.get_bookmarked_works('91',rest_type='hide')==()
    assert [row['id'] for row in reader.get_bookmarked_works('91',rest_type='show')]==['701','702']
    assert [row['id'] for row in reader.get_bookmarked_novels('91',rest_type='show')]==['501']
    assert [row['id'] for row in reader.get_bookmarked_novels('91',rest_type='hide')]==['502']
