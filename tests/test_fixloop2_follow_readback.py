"""Follow readback uses synthetic transports and preserves uncertain fences."""
from uuid import uuid4
import json
import pytest
from required_network_guard import no_network
from test_original_author_follow import action_stack
from pixivvault_nicegui.pixiv_write_client import WriteIntent, WriteHTTPResponse, _append_follow_response
from pixivvault_nicegui.pixiv_actions import PixivActions


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ['follow', 'unfollow'])
async def test_http200_ambiguous_reply_automatically_observes_state(action_stack, caplog, action, no_network):
    stack = action_stack
    if action == 'unfollow':
        stack.http.following['91'] = 0
    calls = []
    def post(request, *_):
        calls.append('post')
        if action == 'follow': stack.http.following['91'] = 0
        else: stack.http.following.pop('91', None)
        return WriteHTTPResponse(200, b'[{"token":"synthetic-secret","message":"private"}]')
    stack.actions._writer._transport = post
    intent = WriteIntent(uuid4().hex, '7', '91', action)
    outcome = await stack.actions.execute(intent, author_name='Generated')
    assert outcome.status == 'observed'
    assert outcome.remote_state is (action == 'follow')
    assert outcome.reason == 'post_state_verified'
    assert not stack.actions.state('7', 'user', '91').unknown
    assert ('7', 'user', '91') not in stack.actions._writer._blocked
    assert stack.actions._writer.result(intent.operation_id).status == 'indeterminate'
    assert calls == ['post']
    assert 'synthetic-secret' not in caplog.text and 'private' not in caplog.text
    assert 'pixiv_follow_response' in caplog.text


@pytest.mark.asyncio
async def test_http429_does_not_trigger_post_result_readback(action_stack, no_network):
    stack = action_stack
    read_count = []
    def post(request, *_):
        read_count.append(len(stack.http.reads))
        return WriteHTTPResponse(429, b'[]')
    stack.actions._writer._transport = post
    outcome = await stack.actions.execute(WriteIntent(uuid4().hex, '7', '91', 'follow'))
    assert outcome.status == 'indeterminate'
    assert len(stack.http.reads) == read_count[0]


@pytest.mark.asyncio
async def test_http200_success_body_disagreeing_readback_is_fenced(action_stack, no_network):
    stack = action_stack
    stack.actions._writer._transport = lambda *args: WriteHTTPResponse(200, b'{"error":false,"body":{}}')
    outcome = await stack.actions.execute(WriteIntent(uuid4().hex, '7', '91', 'follow'))
    assert outcome.status == 'indeterminate'
    assert outcome.remote_state is False
    assert stack.actions.state('7', 'user', '91').unknown
    assert ('7', 'user', '91') in stack.actions._writer._blocked


@pytest.mark.asyncio
async def test_challenge_response_neither_logs_text_nor_reads_back(action_stack, no_network, caplog):
    stack = action_stack
    read_count = []
    def post(request, *_):
        read_count.append(len(stack.http.reads))
        return WriteHTTPResponse(200, b'{"error":false,"body":{"challenge":"synthetic-secret"}}')
    stack.actions._writer._transport = post
    outcome = await stack.actions.execute(WriteIntent(uuid4().hex, '7', '91', 'follow'))
    assert outcome.status == 'indeterminate'
    assert len(stack.http.reads) == read_count[0]
    assert 'synthetic-secret' not in caplog.text
    assert ('7', 'user', '91') in stack.actions._writer._blocked


@pytest.mark.asyncio
async def test_follow_response_summary_is_persisted_without_sensitive_values(action_stack, no_network, tmp_path):
    stack = action_stack
    actions = PixivActions(stack.reader, response_log_root=tmp_path)
    try:
        actions._writer._transport = lambda *args: WriteHTTPResponse(
            200, b'{"error":false,"body":{"token":"synthetic-secret"},"message":"private-name"}')
        await actions.execute(WriteIntent(uuid4().hex, '7', '91', 'follow'))
        raw = (tmp_path / 'pixiv-follow-response-log.jsonl').read_text(encoding='utf8')
        assert 'synthetic-secret' not in raw and 'private-name' not in raw
        record = json.loads(raw)
        assert record['action'] == 'follow' and record['http_status'] == 200
        assert record['body']['error'] is False and record['body']['message'] == '[redacted]'
    finally:
        assert actions.close(timeout=2)


def test_follow_log_does_not_modify_foreign_file(tmp_path):
    path = tmp_path / 'pixiv-follow-response-log.jsonl'
    path.write_bytes(b'unrelated data')
    with pytest.raises((ValueError, json.JSONDecodeError)):
        _append_follow_response(tmp_path, 'follow', 200, {'format': 'json'})
    assert path.read_bytes() == b'unrelated data'
