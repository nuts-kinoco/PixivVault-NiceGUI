import logging
from pixivvault_nicegui.save_exports import SaveExports
from pixivvault_nicegui.bookmark_projection import _largest_storage


def test_export_warning_logs_do_not_expose_ids_paths_or_folder_names(tmp_path, caplog):
    service=SaveExports.__new__(SaveExports)
    service._log_salt=b"synthetic-salt"
    service.scan_errors={};service._reported_errors={}
    service.scan_warnings={};service._reported_warnings={}
    with caplog.at_level(logging.WARNING):
        service._scan_error('static:99887766', PermissionError('private-path'))
        service._storage_warning('11223344', 'private-folder-name')
        first=tmp_path/'private-folder-name';first.mkdir()
        second=tmp_path/'private-other';second.mkdir()
        assert _largest_storage([first,second], 'folder') in (first,second)
    message=caplog.text
    assert '99887766' not in message and '11223344' not in message
    assert 'private-path' not in message and 'private-folder-name' not in message
    assert 'export item' in message and 'author layout' in message
    placement=[record for record in caplog.records if record.name.endswith('save_exports') and 'author layout' in record.getMessage()]
    direct=[record for record in caplog.records if record.name.endswith('bookmark_projection')]
    assert len(placement)==1 and len(direct)==1
    assert all('一番大きい' in record.getMessage() for record in placement+direct)
    assert all('private-folder-name' not in record.getMessage() for record in placement+direct)
    assert all('11223344' not in record.getMessage() for record in placement+direct)
    assert service.scan_warnings['11223344']=='private-folder-name'


def test_held_warning_keeps_japanese_text_dedup_and_redacts_errors(caplog):
    service=SaveExports.__new__(SaveExports)
    service._log_salt=b"synthetic-salt"
    service.scan_errors={};service._reported_errors={}
    service.scan_warnings={};service._reported_warnings={}
    key='author:99887766'
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            service._scan_error(key,PermissionError('private-path/author-folder'))
        service._scan_error(key,FileNotFoundError('different-private-path'))
    records=[record.getMessage() for record in caplog.records
             if record.name.endswith('save_exports') and '保存後処理を保留' in record.getMessage()]
    assert len(records)==2
    assert all('99887766' not in message and 'private-path' not in message
               and 'author-folder' not in message for message in records)
    assert '必要な保存証跡が見つかりません' in service.scan_errors[key]
