"""Injection into S07 cleanup; do not touch any real DB or network."""
import socket
import json
from pathlib import Path
import pytest
import tools.check_stage5_s07_next_saved_txt as s07
from tools.stage5_offline_parent_fence import OfflineNetworkFence


@pytest.mark.parametrize('failure', ['service_close','source_sha','result_write'])
def test_s07_cleanup_failure_is_distinguished_and_sockets_restored(tmp_path,failure):
    originals=(socket.socket.connect,socket.getaddrinfo,socket.socket.connect_ex)
    report={'success':True,'failure':None,'steps':['synthetic done']}
    class Service:
        def close(self):
            if failure=='service_close': raise OSError('close injection')
    source=tmp_path/'source.sqlite'
    source.write_bytes(b'generated-only')
    output=tmp_path/'result.json'
    real_sha=s07.sha
    def sha(path):
        if failure=='source_sha':raise OSError('sha injection')
        return real_sha(path)
    def write(path,payload):
        if failure=='result_write' and path.name=='result.json':
            raise OSError('receipt injection')
        path.write_text(json.dumps(payload),encoding='utf8')
    with OfflineNetworkFence() as guard:
        result=s07.finalize_s07_run(report,Service(),source,'any-expected-sha',output,
                                   sha_func=sha,write_func=write,network=guard)
        assert result['success'] is False
        assert result['cleanup_errors']
        if failure=='result_write':
            assert (tmp_path/'result-write-failure.json').is_file()
        else:
            assert output.is_file()
    assert originals==(socket.socket.connect,socket.getaddrinfo,socket.socket.connect_ex)


def test_s07_main_and_cleanup_failure_both_preserved(tmp_path):
    report={'success':False,'failure':'SyntheticMainError','steps':[]}
    class Service:
        def close(self): raise OSError('cleanup issue')
    source=tmp_path/'source';source.write_bytes(b'x')
    output=tmp_path/'result.json'
    with OfflineNetworkFence() as guard:
        result=s07.finalize_s07_run(report,Service(),source,'hash',output,network=guard)
    assert result['failure']=='SyntheticMainError'
    assert result['cleanup_errors']
    assert json.loads(output.read_text(encoding='utf8'))['failure']=='SyntheticMainError'


def test_s07_both_receipt_writers_failing_still_restores_socket_state(tmp_path):
    original=(socket.socket.connect,socket.getaddrinfo,socket.socket.connect_ex)
    source=tmp_path/'source';source.write_bytes(b'fixture')
    class Service:
        def close(self):pass
    def unavailable(_path,_payload):raise OSError('simulated disk write failure')
    with pytest.raises(RuntimeError,match='either result receipt'):
        with OfflineNetworkFence() as guard:
            s07.finalize_s07_run({'success':True,'failure':None,'steps':[]},
                Service(),source,s07.sha(source),tmp_path/'result.json',
                write_func=unavailable,network=guard)
    assert original==(socket.socket.connect,socket.getaddrinfo,socket.socket.connect_ex)


def test_s07_swallowed_parent_egress_is_recorded_as_cleanup_failure(tmp_path):
    source=tmp_path/'source';source.write_bytes(b'fixture')
    with OfflineNetworkFence() as guard:
        try:socket.getaddrinfo('outside.invalid',443)
        except PermissionError:pass
        result=s07.finalize_s07_run({'success':True,'failure':None,'steps':[]},
            None,source,s07.sha(source),tmp_path/'result.json',network=guard)
    assert result['parent_outbound_attempts']==1
    assert not result['success']
    assert result['cleanup_errors'][0]['stage']=='offline_parent_fence'
