from contextlib import closing
from email.utils import formatdate
from hashlib import sha256
import json
import os
import sqlite3
import sys
from types import SimpleNamespace

import pytest

from pixivvault_nicegui import extension_cookie_store as cookies
from pixivvault_nicegui import legacy_library_copies as copies
from pixivvault_nicegui import ugoira_storage as storage
from pixivvault_nicegui.cookie_expiry_refresh import CookieExpiryCoordinator
from pixivvault_nicegui.pixiv_read_client import _ReadResponse
from pixivvault_nicegui.ugoira_fixtures import build_ugoira_fixture
from pixivvault_nicegui.ugoira_mp4 import UgoiraMp4Error
from pixivvault_nicegui.ugoira_practical import PracticalUgoiraJobs
from test_ag_z04_20261119 import setup, reader, fetch


def generation(root, index, previous, expiry):
    token = f'{index:032x}'
    folder = root / 'generations' / token
    folder.mkdir()
    raw = (cookies._HEADER + f'.pixiv.net\tTRUE\t/\tTRUE\t{expiry}\tPHPSESSID\t777_old\n').encode('ascii')
    (folder / 'cookies.txt').write_bytes(raw)
    packet = dict(version=2, generation=token, bytes=len(raw),
                  sha256=sha256(raw).hexdigest(), cookie_count=1,
                  previous=None if previous is None else previous.parent.name)
    (folder / 'generation.json').write_bytes(json.dumps(packet).encode('ascii'))
    return folder / 'cookies.txt'


def legacy_fixture(tmp_path, count, select=True):
    expiry, external, profile, store, binding = setup(tmp_path)
    root = store.root
    binding.close()
    store.close()
    chain = []
    for index in range(1, count + 1):
        chain.append(generation(root, index, chain[-1] if chain else None, expiry))
    if select:
        profile.update(cookie_file=chain[-1], expected_revision=profile.snapshot().revision)
    return expiry, external, profile, root, chain


def adopted(profile):
    with closing(sqlite3.connect(profile.path)) as con:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE name='native_cookie_adoptions'").fetchone():
            return set()
        return {row[0] for row in con.execute('SELECT cookie_path FROM native_cookie_adoptions')}


def renew(profile, store, expiry, count=1):
    old = profile.snapshot()
    binding = CookieExpiryCoordinator(profile, lambda: store)
    client, _ = reader(old.cookie_file, binding, [])
    try:
        for index in range(count):
            header = 'PHPSESSID=777_old; Domain=.pixiv.net; Path=/; Secure; Expires=' + formatdate(expiry + 7200 + index, usegmt=True)
            client._transport = lambda *args: _ReadResponse(b'{"error":false,"body":{}}', (header,))
            fetch(client)
        return profile.snapshot().revision - old.revision
    finally:
        client.close()
        binding.close()


def test_independent_valid_legacy_chain(tmp_path):
    expiry, _, profile, root, chain = legacy_fixture(tmp_path, 7)
    foreign = generation(root, 50, None, expiry)
    foreign_before = foreign.read_bytes()
    try:
        with_store = cookies.ExtensionCookieStore(root, profile=profile)
        try:
            assert adopted(profile) == set(map(str, chain))
            assert with_store.take_legacy_migration_notice()
            assert renew(profile, with_store, expiry, 4) == 4
            assert len(list((root / 'generations').iterdir())) == 8
            assert foreign.read_bytes() == foreign_before
            selected = profile.snapshot().cookie_file
            meta = json.loads((selected.parent / 'generation.json').read_bytes())
            assert (root / 'generations' / meta['previous'] / 'cookies.txt').is_file()
            assert not chain[0].exists()
        finally:
            with_store.close()
        before = adopted(profile)
        with_store = cookies.ExtensionCookieStore(root, profile=profile)
        try:
            assert adopted(profile) == before
            assert not with_store.take_legacy_migration_notice()
        finally:
            with_store.close()
    finally:
        profile.close()


def test_independent_indeterminate_migration_stays_one_time(tmp_path):
    _, _, profile, root, chain = legacy_fixture(tmp_path, 4, select=False)
    try:
        first = cookies.ExtensionCookieStore(root, profile=profile)
        try:
            assert first.take_legacy_migration_notice()
            assert not adopted(profile)
        finally:
            first.close()
        profile.update(cookie_file=chain[-1], expected_revision=profile.snapshot().revision)
        second = cookies.ExtensionCookieStore(root, profile=profile)
        try:
            observed = len(adopted(profile))
            print(f'later_reopen_adoptions={observed}')
            assert not adopted(profile)
        finally:
            second.close()
    finally:
        profile.close()
