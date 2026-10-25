"""Synthetic offline tests for the novel storage candidate; no user data/network."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
import pytest
from pixivvault_nicegui.media_parity_policy import NovelDocument, parse_novel_detail
import pixivvault_nicegui.novel_storage as storage
from pixivvault_nicegui.novel_storage import ROOT_MARKER, NovelExportStore, NovelStorageError, NovelStorageIOError, NovelStorageIntegrityError
from pixivvault_nicegui.novel_export import render_novel_epub, render_novel_txt

def novel(content="first line\nsecond <script> & text", title="Synthetic <title>"):
    return parse_novel_detail(
        {"id": "123", "userId": "91", "title": title, "content": content,
         "seriesNavData": None, "textEmbeddedImages": {}, "coverUrl": ""},
        work_id="123", expected_author_id="91")

def test_txt_epub_and_both_publish_only_complete_verified_outputs(tmp_path):
    txt_store = NovelExportStore(tmp_path / "txt")
    txt = txt_store.save(novel(), author_name="Synthetic Author", output_format="TXT")
    assert txt.output_names == ("novel.txt",)
    assert txt_store.reconcile(txt.relative_receipt, expected_sha256=txt.receipt_sha256).saved == txt
    epub_store = NovelExportStore(tmp_path / "epub")
    epub = epub_store.save(novel(), author_name="Synthetic Author", output_format="EPUB")
    assert epub.output_names == ("novel.epub",)
    assert epub_store.reconcile(epub.relative_receipt).state == "complete"
    both_store = NovelExportStore(tmp_path / "both")
    both = both_store.save(novel(), author_name="Synthetic Author", output_format="BOTH")
    assert both.output_names == ("novel.epub", "novel.txt")
    folder = tmp_path / "both" / "outputs" / Path(both.relative_receipt).parts[1]
    assert (folder / "novel.txt").read_bytes() == render_novel_txt(novel(), author_name="Synthetic Author")
    assert (folder / "novel.epub").read_bytes() == render_novel_epub(novel(), author_name="Synthetic Author")
    assert both_store.reconcile(both.relative_receipt, expected_sha256=both.receipt_sha256).state == "complete"

def test_both_serializer_failure_creates_no_stage_or_output(tmp_path, monkeypatch):
    store = NovelExportStore(tmp_path / "store")
    def fail_epub(*args, **kwargs):
        raise ValueError("private synthetic title should not escape")
    monkeypatch.setattr(storage, "render_novel_epub", fail_epub)
    with pytest.raises(NovelStorageError) as caught:
        store.save(novel(), author_name="Author", output_format="BOTH")
    assert "private synthetic title" not in str(caught.value)
    assert list((tmp_path / "store" / ".staging").iterdir()) == []
    assert list((tmp_path / "store" / "outputs").iterdir()) == []

def test_mid_write_failure_preserves_incomplete_stage_and_never_publishes(tmp_path, monkeypatch):
    store = NovelExportStore(tmp_path / "store")
    original = storage._write_new
    def fail_receipt(path, data):
        if path.parent.name.startswith("novel-") and path.name == "receipt.json":
            raise OSError("synthetic private path must not escape")
        return original(path, data)
    monkeypatch.setattr(storage, "_write_new", fail_receipt)
    with pytest.raises(NovelStorageError) as caught:
        store.save(novel(), author_name="Author", output_format="BOTH")
    assert "synthetic private path" not in str(caught.value)
    stages = list((tmp_path / "store" / ".staging").iterdir())
    assert len(stages) == 1
    assert (stages[0] / "novel.txt").is_file()
    assert not (stages[0] / "receipt.json").exists()
    assert list((tmp_path / "store" / "outputs").iterdir()) == []
    assert store._usage()[0] == 1

def test_repeated_save_is_non_overwriting_and_old_receipt_stays_valid(tmp_path):
    store = NovelExportStore(tmp_path / "store")
    first = store.save(novel(), author_name="Author", output_format="TXT")
    first_file = tmp_path / "store" / first.relative_receipt.replace("/receipt.json", "/novel.txt")
    first_bytes = first_file.read_bytes()
    second = store.save(novel(), author_name="Author", output_format="TXT")
    assert first.relative_receipt != second.relative_receipt
    assert first_bytes == first_file.read_bytes()
    assert store.reconcile(first.relative_receipt, expected_sha256=first.receipt_sha256).state == "complete"
    assert store.reconcile(second.relative_receipt, expected_sha256=second.receipt_sha256).state == "complete"

def test_corrupt_output_is_not_reconciled_as_complete(tmp_path):
    store = NovelExportStore(tmp_path / "store")
    saved = store.save(novel(), author_name="Author", output_format="BOTH")
    folder = tmp_path / "store" / saved.relative_receipt.replace("/receipt.json", "")
    (folder / "novel.epub").write_bytes(b"truncated")
    with pytest.raises(NovelStorageIntegrityError):
        store.reconcile(saved.relative_receipt)

def test_missing_generation_is_missing_but_incomplete_published_generation_is_error(tmp_path):
    store = NovelExportStore(tmp_path / "store")
    missing = "outputs/novel-123-" + "a" * 32 + "/receipt.json"
    assert store.reconcile(missing).state == "missing"
    saved = store.save(novel(), author_name="Author", output_format="TXT")
    folder = tmp_path / "store" / saved.relative_receipt.replace("/receipt.json", "")
    (folder / "completion.json").unlink()
    with pytest.raises(NovelStorageIntegrityError):
        store.reconcile(saved.relative_receipt)

def test_receipt_path_escape_and_other_media_namespace_are_rejected(tmp_path):
    store = NovelExportStore(tmp_path / "store")
    for path in ("../receipt.json", "outputs/../receipt.json",
                 "outputs/novel-123-" + "A" * 32 + "/receipt.json",
                 "ugoira/frames/receipt.json", 12, None):
        with pytest.raises(NovelStorageError):
            store.reconcile(path)
    saved = store.save(novel(), author_name="Author", output_format="TXT")
    folder = tmp_path / "store" / saved.relative_receipt.replace("/receipt.json", "")
    (folder / "unexpected.bin").write_bytes(b"unknown retained data")
    with pytest.raises(NovelStorageError):
        store._usage()

def test_invalid_root_marker_and_symlink_root_fail_closed(tmp_path):
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / ROOT_MARKER).write_text("wrong")
    with pytest.raises(NovelStorageError):
        NovelExportStore(bad)
    target = tmp_path / "real"
    NovelExportStore(target)
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink privilege unavailable on this Windows account")
    with pytest.raises(NovelStorageError):
        NovelExportStore(link)

def test_hardlinked_output_is_rejected_during_reconcile_when_supported(tmp_path):
    store = NovelExportStore(tmp_path / "store")
    saved = store.save(novel(), author_name="Author", output_format="TXT")
    folder = tmp_path / "store" / saved.relative_receipt.replace("/receipt.json", "")
    alias = tmp_path / "outside.txt"
    try:
        os.link(folder / "novel.txt", alias)
    except OSError:
        pytest.skip("hardlink creation unavailable on this filesystem")
    with pytest.raises(NovelStorageIntegrityError):
        store.reconcile(saved.relative_receipt)




def test_rename_success_then_directory_fsync_failure_returns_reconcile_coordinates(tmp_path, monkeypatch):
    store = NovelExportStore(tmp_path / "store")
    original = storage._fsync_dir
    def fail_after_publish(path):
        if Path(path).name == "outputs":
            raise OSError("synthetic post-rename durability fault")
        return original(path)
    monkeypatch.setattr(storage, "_fsync_dir", fail_after_publish)
    with pytest.raises(storage.NovelStoragePublicationUncertain) as caught:
        store.save(novel(), author_name="Author", output_format="BOTH")
    error = caught.value
    assert error.relative_receipt.startswith("outputs/novel-123-")
    assert len(error.receipt_sha256) == 64
    result = store.reconcile(error.relative_receipt, expected_sha256=error.receipt_sha256)
    assert result.state == "complete"
    assert result.saved.output_names == ("novel.epub", "novel.txt")
    assert len(list((tmp_path / "store" / "outputs").iterdir())) == 1

@pytest.mark.parametrize("fail_name", ["outputs", ".staging"])
def test_partial_root_initialization_recovers_only_owned_missing_namespace(tmp_path, monkeypatch, fail_name):
    root = tmp_path / "root"
    original = Path.mkdir
    failed = False
    def fail_one(self, *args, **kwargs):
        nonlocal failed
        if self == root / fail_name and not failed:
            failed = True
            raise OSError("synthetic init interruption")
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, "mkdir", fail_one)
    with pytest.raises(storage.NovelStorageIOError):
        NovelExportStore(root)
    assert failed
    assert (root / ROOT_MARKER).read_bytes()
    monkeypatch.setattr(Path, "mkdir", original)
    recovered = NovelExportStore(root)
    assert (root / "outputs").is_dir()
    assert (root / ".staging").is_dir()
    assert recovered._usage() == (0, 0, 0)

def test_marker_replace_interruption_recovers_valid_owned_temp_marker(tmp_path, monkeypatch):
    root = tmp_path / "root"
    original = storage.os.replace
    failed = False
    def fail_marker(source, destination):
        nonlocal failed
        if Path(destination) == root / ROOT_MARKER and not failed:
            failed = True
            raise OSError("synthetic marker publish interruption")
        return original(source, destination)
    monkeypatch.setattr(storage.os, "replace", fail_marker)
    with pytest.raises(storage.NovelStorageIOError):
        NovelExportStore(root)
    assert (root / ".init-marker.tmp").read_bytes() == storage._MARKER
    assert not (root / ROOT_MARKER).exists()
    monkeypatch.setattr(storage.os, "replace", original)
    NovelExportStore(root)
    assert (root / ROOT_MARKER).read_bytes() == storage._MARKER
    assert not (root / ".init-marker.tmp").exists()


def test_marker_write_interruption_before_temp_creation_recovers_empty_root(tmp_path, monkeypatch):
    root = tmp_path / "root"
    original = storage._write_new
    failed = False
    def fail_before_temp(path, data):
        nonlocal failed
        if Path(path) == root / ".init-marker.tmp" and not failed:
            failed = True
            raise OSError("synthetic marker write interruption")
        return original(path, data)
    monkeypatch.setattr(storage, "_write_new", fail_before_temp)
    with pytest.raises(storage.NovelStorageIOError):
        NovelExportStore(root)
    assert list(root.iterdir()) == []
    monkeypatch.setattr(storage, "_write_new", original)
    NovelExportStore(root)
    assert (root / ROOT_MARKER).read_bytes() == storage._MARKER

def test_partial_owned_marker_prefix_is_completed_but_unknown_root_data_is_preserved(tmp_path, monkeypatch):
    root = tmp_path / "root"
    original = storage._write_new
    failed = False
    def fail_after_prefix(path, data):
        nonlocal failed
        if Path(path) == root / ".init-marker.tmp" and not failed:
            failed = True
            path.write_bytes(data[:17])
            raise OSError("synthetic partial marker write")
        return original(path, data)
    monkeypatch.setattr(storage, "_write_new", fail_after_prefix)
    with pytest.raises(storage.NovelStorageIOError):
        NovelExportStore(root)
    assert (root / ".init-marker.tmp").read_bytes() == storage._MARKER[:17]
    monkeypatch.setattr(storage, "_write_new", original)
    NovelExportStore(root)
    assert (root / ROOT_MARKER).read_bytes() == storage._MARKER
    assert not (root / ".init-marker.tmp").exists()

def test_complete_temp_marker_with_unknown_sibling_is_refused_and_preserved(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    marker_tmp = root / ".init-marker.tmp"
    marker_tmp.write_bytes(storage._MARKER)
    unknown = root / "keep.bin"
    unknown.write_bytes(b"preserve unknown entry")
    with pytest.raises(NovelStorageError):
        NovelExportStore(root)
    assert marker_tmp.read_bytes() == storage._MARKER
    assert unknown.read_bytes() == b"preserve unknown entry"
    assert not (root / ROOT_MARKER).exists()
def test_unowned_partial_root_is_not_adopted_or_deleted(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    unknown = root / "keep.bin"
    unknown.write_bytes(b"unknown user data")
    with pytest.raises(NovelStorageError):
        NovelExportStore(root)
    assert unknown.read_bytes() == b"unknown user data"

def test_separate_process_saves_serialize_under_root_lock(tmp_path):
    root = tmp_path / "store"
    NovelExportStore(root)  # Initialize before starting competing writers.
    child = (
        "from pathlib import Path; import sys; "
        "from pixivvault_nicegui.media_parity_policy import parse_novel_detail; "
        "from pixivvault_nicegui.novel_storage import NovelExportStore; "
        "d=parse_novel_detail({'id':'123','userId':'91','title':'Synthetic','content':'body',"
        "'seriesNavData':None,'textEmbeddedImages':{},'coverUrl':''},work_id='123',expected_author_id='91'); "
        "r=NovelExportStore(Path(sys.argv[1])).save(d,author_name='Author',output_format='TXT'); "
        "print(r.relative_receipt)"
    )
    env = os.environ.copy()
    env["TEMP"] = os.environ["TEMP"]
    env["TMP"] = os.environ["TMP"]
    env["PYTHONPATH"] = os.environ["PYTHONPATH"]
    first = subprocess.Popen([sys.executable, "-X", "utf8", "-c", child, str(root)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    second = subprocess.Popen([sys.executable, "-X", "utf8", "-c", child, str(root)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    out1, err1 = first.communicate(timeout=30)
    out2, err2 = second.communicate(timeout=30)
    assert first.returncode == 0, err1
    assert second.returncode == 0, err2
    assert out1.strip() != out2.strip()
    store = NovelExportStore(root)
    assert store.reconcile(out1.strip()).state == "complete"
    assert store.reconcile(out2.strip()).state == "complete"
    assert len(list((root / "outputs").iterdir())) == 2
def test_unknown_receipt_schema_cannot_be_recertified_as_complete(tmp_path):
    store = NovelExportStore(tmp_path / "store")
    saved = store.save(novel(), author_name="Author", output_format="TXT")
    folder = tmp_path / "store" / saved.relative_receipt.replace("/receipt.json", "")
    receipt_path = folder / "receipt.json"
    completion_path = folder / "completion.json"
    obj = json.loads(receipt_path.read_bytes())
    obj["schema"] = 999
    forged = storage._json_bytes(obj)
    receipt_path.write_bytes(forged)
    completion_path.write_bytes(storage._json_bytes({
        "schema": 1, "generation": folder.name,
        "receipt_sha256": hashlib.sha256(forged).hexdigest(),
    }))
    with pytest.raises(NovelStorageIntegrityError):
        store.reconcile(saved.relative_receipt)
def test_transient_io_is_distinct_from_corruption_and_never_succeeds(tmp_path, monkeypatch):
    store = NovelExportStore(tmp_path / "store")
    saved = store.save(novel(), author_name="Author", output_format="TXT")
    original = storage._read_bounded
    def fail_receipt(path, limit):
        if Path(path).name == "receipt.json":
            raise OSError("private synthetic filesystem path")
        return original(path, limit)
    monkeypatch.setattr(storage, "_read_bounded", fail_receipt)
    with pytest.raises(NovelStorageIOError) as caught:
        store.reconcile(saved.relative_receipt)
    assert "private synthetic filesystem path" not in str(caught.value)
def test_reconcile_expected_receipt_digest_mismatch_is_not_success(tmp_path):
    store = NovelExportStore(tmp_path / "store")
    saved = store.save(novel(), author_name="Author", output_format="EPUB")
    with pytest.raises(NovelStorageError):
        store.reconcile(saved.relative_receipt, expected_sha256="0" * 64)

def test_forged_document_and_invalid_format_fail_without_creating_stage(tmp_path):
    store = NovelExportStore(tmp_path / "store")
    forged = NovelDocument("123", "91", "title", "body", None)
    for fmt in ("MP4", "", "txt", None):
        with pytest.raises(NovelStorageError):
            store.save(forged, author_name="Author", output_format=fmt)
    assert list((tmp_path / "store" / ".staging").iterdir()) == []
    with pytest.raises(NovelStorageError):
        store.save({}, author_name="Author", output_format="TXT")

def test_quota_counts_stages_and_refuses_without_deleting_existing_data(tmp_path, monkeypatch):
    store = NovelExportStore(tmp_path / "store")
    stage = tmp_path / "store" / ".staging" / ("novel-123-" + "b" * 32)
    stage.mkdir()
    (stage / "novel.txt").write_bytes(b"preserve me")
    before = (stage / "novel.txt").read_bytes()
    monkeypatch.setattr(storage, "_MAX_GENERATIONS", 1)
    with pytest.raises(NovelStorageError):
        store.save(novel(), author_name="Author", output_format="TXT")
    assert (stage / "novel.txt").read_bytes() == before
    assert store._usage()[0] == 1
