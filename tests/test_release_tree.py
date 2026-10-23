"""Synthetic release trees; the gate never reads a user library or credential."""

import importlib.util
from pathlib import Path
import shutil
import zipfile

import pytest


SPEC = importlib.util.spec_from_file_location("release_gate", Path(__file__).resolve().parents[1] / "tools/check_release_tree.py")
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)
SHA = "a" * 40


def fixture(root):
    root.mkdir()
    (root / "PixivVault-NiceGUI.exe").write_bytes(b"MZsynthetic")
    internal = root / "_internal"
    internal.mkdir()
    (internal / "python313.dll").write_bytes(b"MZruntime")
    with zipfile.ZipFile(internal / "base_library.zip", "w") as archive:
        archive.writestr("collections/__init__.pyc", b"synthetic")
    return root


def test_manifest_copy_matches_and_does_not_modify_distribution(tmp_path):
    source = fixture(tmp_path / "dist")
    before = {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    first = gate.inspect_release(source, source_commit=SHA)
    assert first["passed"] and first["file_count"] == 3
    destination = tmp_path / "copy"
    shutil.copytree(source, destination)
    second = gate.inspect_release(destination, source_commit=SHA, expected=first)
    assert second["passed"] and second["tree_sha256"] == first["tree_sha256"]
    assert before == {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}


@pytest.mark.parametrize("filename", ["cookies.txt", ".env", "pixiv_vault.db", "learning.sqlite3", "gui_error_log.txt.log"])
def test_private_runtime_files_are_rejected(tmp_path, filename):
    root = fixture(tmp_path / "dist")
    (root / filename).write_bytes(b"synthetic fixture only")
    result = gate.inspect_release(root)
    assert not result["passed"]
    assert any(item["path"] == filename for item in result["violations"])


def test_public_certificate_and_legitimate_package_config_are_allowed(tmp_path):
    root = fixture(tmp_path / "dist")
    public = root / "_internal" / "certifi"
    public.mkdir()
    (public / "cacert.pem").write_text("-----BEGIN CERTIFICATE-----\nsynthetic public CA\n")
    (public / "config.json").write_text('{"version": 1}')
    assert gate.inspect_release(root)["passed"]


def test_private_key_or_cookie_material_hidden_under_different_name_is_rejected(tmp_path):
    root = fixture(tmp_path / "dist")
    (root / "unexpected.pem").write_text("-----BEGIN PRIVATE KEY-----\nsynthetic")
    assert not gate.inspect_release(root)["passed"]


def test_missing_required_file_or_tampered_copy_cannot_pass(tmp_path):
    source = fixture(tmp_path / "dist")
    expected = gate.inspect_release(source, source_commit=SHA)
    (source / "PixivVault-NiceGUI.exe").write_bytes(b"MZchanged")
    result = gate.inspect_release(source, source_commit=SHA, expected=expected)
    assert not result["passed"] and {v["code"] for v in result["violations"]} == {"manifest_mismatch"}
    (source / "PixivVault-NiceGUI.exe").unlink()
    assert any(v["code"] == "required_file_missing" for v in gate.inspect_release(source)["violations"])


@pytest.mark.parametrize("member", ["../secret.txt", "cookies.txt", "C:/private/file", ".git/config"])
def test_unsafe_embedded_archive_member_is_rejected(tmp_path, member):
    root = fixture(tmp_path / "dist")
    with zipfile.ZipFile(root / "unexpected.zip", "w") as archive:
        archive.writestr(member, b"synthetic")
    result = gate.inspect_release(root)
    assert not result["passed"]
    assert any(v["code"].startswith("archive_") for v in result["violations"])


def test_bad_commit_label_is_not_accepted(tmp_path):
    root = fixture(tmp_path / "dist")
    assert not gate.inspect_release(root, source_commit="main")["passed"]


def test_linked_entry_is_detected_without_following_it(tmp_path):
    root = fixture(tmp_path / "dist")
    outside = tmp_path / "outside"
    outside.write_bytes(b"not part of distribution")
    # Hardlinks work on the Windows CI runner without symlink privileges.
    (root / "linked.bin").hardlink_to(outside)
    result = gate.inspect_release(root)
    assert not result["passed"]
    assert any(v["code"] == "hard_linked_entry" for v in result["violations"])


@pytest.mark.parametrize("name", ["gui_error_log.txt.1", "pixiv_vault.db-journal", "cookies.txt.bak"])
def test_rotated_or_backup_private_files_are_rejected(tmp_path, name):
    root = fixture(tmp_path / "dist")
    (root / name).write_bytes(b"synthetic confidential fixture")
    assert not gate.inspect_release(root)["passed"]


def test_disguised_cookie_in_archive_body_is_rejected(tmp_path):
    root = fixture(tmp_path / "dist")
    synthetic = (b"# Netscape HTTP Cookie File\n"
                 b".example.invalid\tTRUE\t/\tTRUE\t9999999999\tPHPSESSID\tSYNTHETIC_NOT_A_CREDENTIAL\n")
    with zipfile.ZipFile(root / "_internal" / "base_library.zip", "a") as archive:
        archive.writestr("package/assets/cache.dat", synthetic)
    result = gate.inspect_release(root)
    assert not result["passed"]
    assert any(v["code"].startswith("archive_") for v in result["violations"])


def test_disguised_cookie_in_binary_file_is_rejected(tmp_path):
    root = fixture(tmp_path / "dist")
    (root / "opaque.dat").write_bytes(b"PHPSESSID=SYNTHETIC_NOT_A_CREDENTIAL\n")
    assert not gate.inspect_release(root)["passed"]


def test_material_past_first_megabyte_is_not_skipped(tmp_path):
    root = fixture(tmp_path / "dist")
    (root / "large.dat").write_bytes(b"X" * (1024 * 1024 + 15) + b"\nPHPSESSID=SYNTHETIC_NOT_A_CREDENTIAL\n")
    assert not gate.inspect_release(root)["passed"]


def test_zip_expansion_is_bounded(tmp_path, monkeypatch):
    root = fixture(tmp_path / "dist")
    with zipfile.ZipFile(root / "too-large.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("large.dat", b"X" * 2048)
    monkeypatch.setattr(gate, "MAX_ARCHIVE_MEMBER", 1024)
    assert any(v["code"] == "archive_expansion_limit" for v in gate.inspect_release(root)["violations"])


def test_nested_archive_cannot_pass_uninspected(tmp_path):
    from io import BytesIO
    root = fixture(tmp_path / "dist")
    nested = BytesIO()
    with zipfile.ZipFile(nested, "w") as archive:
        archive.writestr("hidden.txt", b"synthetic")
    with zipfile.ZipFile(root / "nested.zip", "w") as archive:
        archive.writestr("binary.dat", nested.getvalue())
    assert any(v["code"] == "archive_nested_container_uninspected" for v in gate.inspect_release(root)["violations"])


def test_prefixed_zip_cannot_hide_as_an_opaque_file(tmp_path):
    from io import BytesIO
    root = fixture(tmp_path / "dist")
    archive_data = BytesIO()
    with zipfile.ZipFile(archive_data, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("hidden.dat", b"PHPSESSID=SYNTHETIC_NOT_A_CREDENTIAL\n")
    (root / "opaque.dat").write_bytes(b"MZsynthetic-prefix" + archive_data.getvalue())
    result = gate.inspect_release(root)
    assert not result["passed"]
    assert any(v["code"] == "archive_session_cookie_material" for v in result["violations"])


def test_prefixed_nested_zip_is_rejected_without_silent_partial_inspection(tmp_path):
    from io import BytesIO
    root = fixture(tmp_path / "dist")
    archive_data = BytesIO()
    with zipfile.ZipFile(archive_data, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("data.dat", b"synthetic")
    with zipfile.ZipFile(root / "outer.zip", "w") as archive:
        archive.writestr("opaque.dat", b"prefix" + archive_data.getvalue())
    assert any(v["code"] == "archive_nested_container_uninspected"
               for v in gate.inspect_release(root)["violations"])


def test_success_never_claims_a_complete_embedded_secret_audit(tmp_path):
    result = gate.inspect_release(fixture(tmp_path / "dist"), source_commit=SHA)
    assert result["passed"]
    assert result["complete_secret_audit"] is False
    assert "PyInstaller CArchive/PYZ" in result["uninspected_embedded_formats"]
    assert result["source_commit"] == SHA
