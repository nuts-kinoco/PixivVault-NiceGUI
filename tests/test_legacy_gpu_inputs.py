"""Legacy folder/ZIP -> owned vectors -> same UI search, no source writes."""
from hashlib import sha256
from pathlib import Path
from zipfile import ZipFile
import sqlite3
import pytest

from test_local_media_adapter import png
from test_managed_similarity import embeddings, vector
from pixivvault_nicegui.egara_inference import EGARA_BB_SHA256, MODEL
from pixivvault_nicegui.services import ApplicationServices


def generated_library(root, *, archive=False, preexisting=False):
    source = root/'source'; source.mkdir()
    db = source/'library.sqlite3'
    with sqlite3.connect(db) as connection:
        connection.executescript('''
            CREATE TABLE works(work_id TEXT PRIMARY KEY,title TEXT,page_count INTEGER,
                tags TEXT,user_id TEXT,is_deleted INTEGER DEFAULT 0);
            CREATE TABLE following_users(user_id TEXT PRIMARY KEY,name TEXT);
            INSERT INTO works VALUES('101','first',2,'[]','12',0);
            INSERT INTO works VALUES('102','second',1,'[]','13',0);
            INSERT INTO following_users VALUES('12','one');
            INSERT INTO following_users VALUES('13','two');
            CREATE TABLE work_embeddings(work_id TEXT,kind TEXT,model TEXT,page INTEGER,
                dim INTEGER,vec BLOB,PRIMARY KEY(work_id,kind,model,page));
        ''')
        if preexisting:
            for kind in ('style','composition'):
                connection.execute('INSERT INTO work_embeddings VALUES(?,?,?,?,?,?)',
                    ('101',kind,MODEL,1,1024,vector()))
    # SQLite's transaction context does not close the connection. A frozen
    # input fixture must release its writer before a mandatory read-only pin
    # can be admitted; depending on cyclic GC made this nondeterministic.
    connection.close()
    images = root/'old-images'; images.mkdir()
    paths = []
    for wid, aid, count in (('101','12',2),('102','13',1)):
        if archive:
            target = images/f'author({aid}).zip'
            with ZipFile(target,'w') as stream:
                for page in range(count):
                    stream.writestr(f'title({wid})_{page+1}.png',png('red'))
            paths.append(target)
        else:
            folder = images/f'author({aid})'; folder.mkdir()
            for page in range(count):
                path = folder/f'title({wid})_{page+1}.png';path.write_bytes(png('red'))
                paths.append(path)
    model = source/'egara.onnx'; model.write_bytes(b'fake model reference only')
    return db,images,model,tuple(paths)


class FakeEngine:
    model_sha256 = EGARA_BB_SHA256
    provider_active = 'cpu'
    fallback_reason = None
    instances = []
    def __init__(self, *args, **kwargs):
        self.calls = 0
        self.closed = False
        self.instances.append(self)
    def infer(self, data):
        assert data.startswith(b'\x89PNG') and not self.closed
        self.calls += 1
        return embeddings()
    def close(self):
        self.closed = True


@pytest.fixture
def fake_engine(monkeypatch):
    FakeEngine.instances = []
    monkeypatch.setattr('pixivvault_nicegui.egara_inference.EgaraInference', FakeEngine)
    return FakeEngine


@pytest.mark.parametrize('archive', [False,True])
def test_legacy_all_pages_resume_and_source_preservation(tmp_path, fake_engine, archive):
    db,images,model,paths = generated_library(tmp_path,archive=archive)
    before = {p:(sha256(p.read_bytes()).hexdigest(),p.stat().st_mtime_ns) for p in (db,model,*paths)}
    services = ApplicationServices(library_readonly_db=db,library_image_root=images)
    try:
        services.configure_similarity_index(tmp_path/'new-index',model_file=model,provider='cpu')
        assert not fake_engine.instances
        assert set(services._similarity_pages()) == {('101',1),('101',2),('102',1)}
        for wid,page in services._similarity_pages():
            assert services.analyze_saved_for_similarity(wid,page) is True
        assert fake_engine.instances[0].calls == 3
        for wid,page in services._similarity_pages():
            assert services.analyze_saved_for_similarity(wid,page) is False
        assert fake_engine.instances[0].calls == 3
        result = services.similarity_source.search('101',2)
        assert result['results'] == [(('102',1),1.)]
        assert result['excluded_same_work'] == 1
        assert services.similarity_analysis_status()['provider'] == 'cpu'
    finally:
        services.close()
    assert fake_engine.instances[0].closed
    assert {p:(sha256(p.read_bytes()).hexdigest(),p.stat().st_mtime_ns) for p in before} == before
    assert not any(Path(str(db)+suffix).exists() for suffix in ('-wal','-shm','-journal'))


def test_original_valid_vectors_are_reused_without_loading_a_model(tmp_path, fake_engine):
    db,images,model,paths = generated_library(tmp_path,preexisting=True)
    services = ApplicationServices(library_readonly_db=db,library_image_root=images)
    try:
        services.configure_similarity_index(tmp_path/'new-index',model_file=model)
        assert services.analyze_saved_for_similarity('101',1) is False
        assert not fake_engine.instances
        assert services.similarity_source.search('101',1)['status'] == 'ok'
    finally:
        services.close()


def test_changed_legacy_file_cannot_reappear_from_old_analysis(tmp_path, fake_engine):
    db,images,model,paths = generated_library(tmp_path)
    services = ApplicationServices(library_readonly_db=db,library_image_root=images)
    try:
        services.configure_similarity_index(tmp_path/'new-index',model_file=model)
        services.analyze_saved_for_similarity('101',1)
        services.analyze_saved_for_similarity('102',1)
        assert services.similarity_source.search('102')['results'][0][0] == ('101',1)
        paths[0].write_bytes(png('blue'))
        assert services.similarity_source.search('102')['results'] == []
        assert services.analyze_saved_for_similarity('101',1) is True
        assert services.similarity_source.search('102')['results'][0][0] == ('101',1)
    finally:
        services.close()


def test_cancelled_legacy_analysis_does_not_load_or_publish(tmp_path,fake_engine):
    db,images,model,paths = generated_library(tmp_path)
    services = ApplicationServices(library_readonly_db=db,library_image_root=images)
    try:
        services.configure_similarity_index(tmp_path/'new-index',model_file=model)
        with pytest.raises(ValueError):
            services.analyze_saved_for_similarity('101',1,is_current=lambda:False)
        assert not fake_engine.instances
        assert services.similarity_index.owned_keys() == frozenset()
    finally:
        services.close()
    with pytest.raises(ValueError):
        services.analyze_saved_for_similarity('101',1)
