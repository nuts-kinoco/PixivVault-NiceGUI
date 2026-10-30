"""Pure fixture coverage: no OS registration, app start or HTTP request."""
import argparse
import pytest

from pixivvault_nicegui.uri_launch import (
    InvalidLaunchURI, START_URI, build_protocol_command, parse_start_uri,
)


@pytest.mark.parametrize("value", ["pixivvault://start", "pixivvault://start/", "PIXIVVAULT://START"])
def test_original_extension_wake_uri(value):
    assert parse_start_uri(value) == START_URI


@pytest.mark.parametrize("value", [
    "", "pixivvault:", "pixivvault://", "pixivvault://download/123",
    "pixivvault://start?", "pixivvault://start#", "pixivvault://start?work_id=123",
    "pixivvault://start/path", "pixivvault://start:25010", "pixivvault://user@start",
    "pixivvault://%73tart", "pixivvault://start%2f", " pixivvault://start",
    "pixivvault://start\n", "pixivvault://start\0", "https://start", "--native", None,
])
def test_uri_cannot_carry_commands_credentials_or_download_payload(value):
    with pytest.raises(InvalidLaunchURI) as error:
        parse_start_uri(value)
    assert "work_id" not in str(error.value)


def test_argparse_accepts_registration_placeholder_as_only_positional():
    parser = argparse.ArgumentParser()
    parser.add_argument("--native", action="store_true")
    parser.add_argument("startup_uri", nargs="?", type=parse_start_uri)
    assert parser.parse_args(["--native", "--", START_URI]).startup_uri == START_URI
    assert parser.parse_args([]).startup_uri is None
    with pytest.raises(SystemExit):
        parser.parse_args(["--", START_URI, "--native"])


def test_module_command_preserves_explicit_storage_configuration():
    command = build_protocol_command("S:/Python Env/python.exe", startup_args=(
        "--native", "--preferences-root", "S:/Candidate Data/preferences",
        "--port", "18091",
    ))
    assert command == ('"S:/Python Env/python.exe" -m pixivvault_nicegui --native '
                       '--preferences-root "S:/Candidate Data/preferences" --port 18091 -- "%1"')
    assert command.count("%1") == 1


def test_frozen_and_source_launcher_entries():
    assert build_protocol_command("S:/App/app.exe", frozen=True) == 'S:/App/app.exe -- "%1"'
    assert build_protocol_command("S:/Env/python.exe", script="S:/My App/native_entry.py") == (
        'S:/Env/python.exe "S:/My App/native_entry.py" -- "%1"'
    )
    with pytest.raises(ValueError):
        build_protocol_command("S:/App/app.exe", frozen=True, script="S:/App/entry.py")


@pytest.mark.parametrize("kwargs", [
    {"executable": "python.exe"}, {"executable": "S:python.exe"},
    {"executable": "S:/bad%1/python.exe"}, {"executable": 'S:/bad"/python.exe'},
    {"executable": "S:/python.exe", "script": "relative.py"},
    {"executable": "S:/python.exe", "module": "arbitrary_module"},
    {"executable": "S:/python.exe", "startup_args": "--native"},
    {"executable": "S:/python.exe", "startup_args": ("--",)},
    {"executable": "S:/python.exe", "startup_args": ("pixivvault://start",)},
    {"executable": "S:/python.exe", "startup_args": ("%1",)},
    {"executable": "S:/python.exe", "startup_args": ("bad\nargument",)},
])
def test_registry_command_rejects_ambiguous_or_placeholder_injected_inputs(kwargs):
    with pytest.raises(ValueError):
        build_protocol_command(**kwargs)
