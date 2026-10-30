"""Bounded generated GETs: artwork restrictions are not shared auth failures."""
import pytest

from pixivvault_nicegui.pixiv_read_client import (
    ArtworkUnavailable, AuthenticationMissing, HTTPStatusRejected, InvalidPayload,
)
from test_pixiv_direct_work import reader, detail


@pytest.mark.parametrize('changes', [{'isMasked': True}, {'illustType': 2}])
def test_known_unsupported_artwork_is_typed_row_restriction(tmp_path, changes):
    client, _, _ = reader(tmp_path, detail(**changes), [])
    with client:
        client.verify_identity()
        with pytest.raises(ArtworkUnavailable):
            client.get_artwork_download_record('123')


@pytest.mark.parametrize('changes', [{'isMasked': 'unknown'}, {'illustType': 77},
                                    {'title': None}, {'id': '999'}])
def test_unknown_schema_or_identity_remains_systemic(tmp_path, changes):
    client, _, _ = reader(tmp_path, detail(**changes), [])
    with client:
        client.verify_identity()
        with pytest.raises(InvalidPayload) as error:
            client.get_artwork_download_record('123')
        assert not isinstance(error.value, ArtworkUnavailable)


@pytest.mark.parametrize('rejection', [403, 'api-error'])
@pytest.mark.parametrize('identity_ok', [True, False])
def test_ambiguous_rejection_is_row_local_only_after_fresh_identity(tmp_path, rejection, identity_ok):
    client, _, _ = reader(tmp_path, detail(), [])
    calls = []
    with client:
        client.verify_identity()
        def transport(request, timeout, limit, deadline):
            calls.append(request.full_url)
            assert request.get_method() == 'GET'
            if request.full_url.endswith('/ajax/illust/123'):
                if rejection == 403:
                    raise HTTPStatusRejected(403)
                return b'{"error":true,"body":null}'
            if request.full_url == 'https://www.pixiv.net/':
                if not identity_ok:
                    return b'<html>Not logged in</html>'
                return b'<meta id="meta-global-data" content=\'{"userData":{"id":"55"}}\'>'
            if request.full_url.endswith('/ajax/user/extra'):
                return b'{"error":false,"body":{}}'
            pytest.fail('unexpected extra request')
        client._transport = transport
        expected = ArtworkUnavailable if identity_ok else AuthenticationMissing
        with pytest.raises(expected):
            client.get_artwork_download_record('123')
    assert calls[0].endswith('/ajax/illust/123')
    assert calls[1] == 'https://www.pixiv.net/'
    assert len(calls) == (3 if identity_ok else 2)
