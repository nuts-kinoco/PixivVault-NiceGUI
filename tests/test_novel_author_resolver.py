"""Author resolution for original single-novel ID ingress; synthetic only."""
import pytest
from pixivvault_nicegui import pixiv_read_client as mod
from test_pixiv_read_client import novel_body,verified_novel_client,packet,make_client,MockTransport
from test_request_retry import Clock,policy


def test_resolve_same_route_one_get_returns_only_author_and_keeps_pin(tmp_path):
    client,mock=verified_novel_client(tmp_path,packet(novel_body()))
    try:
        author=client.resolve_novel_author('123')
        assert author=='91' and type(author) is str and len(mock.calls)==1
        request=mock.calls[0]
        assert request.full_url=='https://www.pixiv.net/ajax/novel/123' and request.get_method()=='GET' and request.data is None
        # A later source-author change cannot silently replace the resolved pin.
        mock.when('/ajax/novel/123',packet(novel_body(userId='92')))
        with pytest.raises(mod.InvalidPayload):client.get_novel_payload('123',author)
    finally:client.close()


def test_resolver_requires_verified_identity_and_rejects_closed_client(tmp_path):
    mock=MockTransport();client=make_client(tmp_path,mock)
    try:
        with pytest.raises(mod.AuthenticationMissing):client.resolve_novel_author('123')
        assert mock.calls==[]
    finally:client.close()
    with pytest.raises(mod.ClientClosed):client.resolve_novel_author('123')
    assert mock.calls==[]


@pytest.mark.parametrize('bad_id',[None,True,False,0,-1,1.5,'','0','01','１','123/other','123?secret','1'*21])
def test_invalid_input_id_never_sends(tmp_path,bad_id):
    client,mock=verified_novel_client(tmp_path,packet(novel_body()))
    try:
        with pytest.raises(mod.DestinationRejected):client.resolve_novel_author(bad_id)
        assert mock.calls==[]
    finally:client.close()


@pytest.mark.parametrize('body',[
    None,[],{},novel_body(id='124'),novel_body(userId=None),novel_body(userId=True),novel_body(userId='01'),
    novel_body(userId='91/other'),novel_body(isMasked=True),novel_body(content=''),novel_body(content=None),
    novel_body(title=[]),novel_body(seriesNavData=[]),novel_body(textEmbeddedImages=[]),
    novel_body(coverUrl='https://foreign.invalid/private'),
])
def test_complete_body_validation_is_required_and_sanitized(tmp_path,body,capsys):
    client,mock=verified_novel_client(tmp_path,packet(body))
    try:
        with pytest.raises(mod.InvalidPayload) as error:client.resolve_novel_author('123')
        assert str(error.value)=='Pixiv payload invalid' and error.value.__suppress_context__ and len(mock.calls)==1
        captured=capsys.readouterr();assert captured.out==captured.err==''
    finally:client.close()


def test_resolver_has_same_byte_budget_and_deadline(tmp_path,monkeypatch):
    client,_=verified_novel_client(tmp_path,packet(novel_body()));clock=Clock();monkeypatch.setattr(mod.time,'monotonic',clock)
    calls=[]
    def transport(request,timeout,limit,deadline):
        calls.append(1);assert limit==mod.JSON_LIMIT and timeout==10 and deadline==10
        clock.now=deadline;return packet(novel_body())
    client._transport=transport;client.set_retry_policy(policy(clock))
    try:
        with pytest.raises(mod.ReadDeadlineExceeded):client.resolve_novel_author('123')
        assert calls==[1]
        clock.now=0;client._transport=lambda *_:b' '*(mod.JSON_LIMIT+1)
        with pytest.raises(mod.ResponseLimitExceeded):client.resolve_novel_author('123')
    finally:client.close()


def test_resolver_uses_configured_transient_retry_without_changing_identity(tmp_path,monkeypatch):
    client,_=verified_novel_client(tmp_path,packet(novel_body()));clock=Clock();monkeypatch.setattr(mod.time,'monotonic',clock)
    calls=[]
    def transport(*args):
        calls.append(1)
        if len(calls)==1:raise mod.HTTPStatusRejected(429)
        return packet(novel_body())
    client._transport=transport;client.set_retry_policy(policy(clock))
    try:
        assert client.resolve_novel_author('123')=='91' and client.require_identity()=='55'
        assert calls==[1,1] and clock.now==pytest.approx(5)
    finally:client.close()
