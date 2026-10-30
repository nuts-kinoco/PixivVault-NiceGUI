"""Original extension wake URI and deterministic Windows registration command.

The extension sends only ``pixivvault://start``. Download payloads belong to its
separate loopback POST, never to this URI. This module performs no launch, IPC,
registry access, network request, or resource construction.
"""
from __future__ import annotations

from pathlib import PureWindowsPath
import re
import subprocess

START_URI = "pixivvault://start"


class InvalidLaunchURI(ValueError):
    """A launch argument is outside the adopted wake-only protocol."""


def parse_start_uri(value: str) -> str:
    """Accept the adopted URI (including browser-normalized case/trailing slash).

    Match before URL decoding: delimiters, percent escapes, whitespace, ports,
    userinfo, commands and query/fragment payloads have no adopted semantics.
    Errors intentionally do not echo attacker-supplied arguments.
    """
    if not isinstance(value, str) or not re.fullmatch(
        r"pixivvault://start/?", value, flags=re.IGNORECASE | re.ASCII
    ):
        raise InvalidLaunchURI("対応していないPixivVault起動URIです。")
    return START_URI


def _argument(value: str) -> str:
    if not isinstance(value, str) or not value or any(
        char in value for char in ('\0', '\r', '\n', '"', '%')
    ):
        raise ValueError("起動コマンドの引数が不正です。")
    return value


def _absolute_windows_path(value: str) -> str:
    value = _argument(value)
    # Registry launches cannot depend on the invoking browser's cwd or PATH.
    if not PureWindowsPath(value).is_absolute():
        raise ValueError("起動ファイルは絶対パスで指定してください。")
    return value


def build_protocol_command(
    executable: str,
    *,
    module: str = "pixivvault_nicegui",
    script: str | None = None,
    startup_args: tuple[str, ...] = (),
    frozen: bool = False,
) -> str:
    """Build an inert HKCU command with explicit app configuration and one URI.

    Use ``script`` for an absolute source launcher that establishes imports,
    the installed module for Python environments, or ``frozen`` for a packaged
    executable. Callers provide approved non-secret configuration arguments;
    no ambient ``sys.argv`` or environment variables are copied implicitly.
    A URI cannot add options because the final placeholder follows ``--``.
    WindowsProtocolRegistration still requires an explicit confirmed UI action.
    """
    command = [_absolute_windows_path(executable)]
    if frozen:
        if script is not None:
            raise ValueError("配布実行ファイルとスクリプトは併用できません。")
    elif script is not None:
        command.append(_absolute_windows_path(script))
    else:
        if module != "pixivvault_nicegui":
            raise ValueError("PixivVaultの起動モジュールを指定してください。")
        command.extend(["-m", module])
    if isinstance(startup_args, (str, bytes)):
        raise ValueError("起動引数は文字列の列で指定してください。")
    for argument in startup_args:
        argument = _argument(argument)
        if argument == "--" or argument.lower().startswith("pixivvault:"):
            raise ValueError("起動URIを設定引数に含めることはできません。")
        command.append(argument)
    # list2cmdline uses Windows CRT quoting (including trailing backslashes).
    # No command interpreter is involved. Keep %1 quoted even without spaces.
    return subprocess.list2cmdline(command + ["--"]) + ' "%1"'
