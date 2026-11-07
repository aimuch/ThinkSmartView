"""The page's threads come from the hub's saved conversations and survive a closed connection.

Run like test_barge_in.py (it stubs the audio stack; importing it first does that here too).
"""
import asyncio

import pytest

from test_barge_in import make_client


def history_client(answers):
    c = make_client()
    c.cfg.update(device_id='dev-1')
    c.history_loaded = False
    c.models = {'current': {'entry': 'DeepSeekLLM'}, 'options': []}
    calls = []

    async def hub_http(path, body=None):
        calls.append((path, body))
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer
    c._hub_http = hub_http
    c.calls = calls
    return c


def published(c, event):
    return [data for ev, data in getattr(c.events, 'log', []) if ev == event]


@pytest.fixture(autouse=True)
def record_events(monkeypatch):
    from thinksmart_client import app

    orig = app.Events.publish

    def publish(self, event, data):
        self.__dict__.setdefault('log', []).append((event, data))
        return orig(self, event, data)
    monkeypatch.setattr(app.Events, 'publish', publish)


SAVED = {'ok': True, 'models': {
    'DeepSeekLLM': [{'id': 1, 'role': 'user', 'text': '我叫小明', 'time': 1.0, 'interrupted': False},
                    {'id': 2, 'role': 'bot', 'text': '你好小明', 'time': 2.0, 'interrupted': True}],
    'ClaudeCLI': [{'id': 3, 'role': 'user', 'text': '看看日志', 'time': 3.0, 'interrupted': False}]}}


@pytest.mark.asyncio
async def test_load_history_when_not_connected():
    c = history_client([SAVED])
    c.hub.connected = False
    await c.load_history()
    assert c.calls[0][0] == '/kiosk/chat/history?device=dev-1&limit=50'
    msgs = c.threads['DeepSeekLLM']
    assert [(m['id'], m['role'], m['text']) for m in msgs] == [('h1', 'user', '我叫小明'), ('h2', 'bot', '你好小明')]
    assert msgs[1]['interrupted'] and 'interrupted' not in msgs[0]
    assert c.history_loaded
    # reset: every model's thread was replaced, the page drops the ones it kept for switching back
    assert published(c, 'thread')[-1] == {'entry': 'DeepSeekLLM', 'messages': msgs, 'reset': True}


@pytest.mark.asyncio
async def test_no_reload_while_connected_and_hub_down_keeps_threads():
    c = history_client([SAVED])
    await c.load_history()          # connected: this connection's messages are being added
    assert c.calls == [] and c.threads == {}
    c = history_client([OSError('refused')])
    c.hub.connected = False
    c.threads = {'DeepSeekLLM': [{'id': 1}]}
    await c.load_history()
    assert c.threads == {'DeepSeekLLM': [{'id': 1}]} and not c.history_loaded


@pytest.mark.asyncio
async def test_closed_connection_keeps_the_thread_and_reloads():
    c = history_client([SAVED])
    c.add_message('user', '新问题')
    c.hub.connected = False
    await c._on_closed()
    assert published(c, 'thread')[-1]['messages'][-1]['text'] == '新问题'   # not cleared any more
    await asyncio.sleep(0)    # the reload task
    assert [m['text'] for m in c.threads['DeepSeekLLM']] == ['我叫小明', '你好小明']


@pytest.mark.asyncio
async def test_voice_clear_message_empties_the_page():
    c = history_client([])
    c.add_message('user', '清除聊天记录')
    await c._on_json({'type': 'chat_history', 'state': 'cleared'})
    assert c.threads == {} and published(c, 'thread')[-1] == {'entry': 'DeepSeekLLM', 'messages': [], 'reset': True}


@pytest.mark.asyncio
async def test_clear_history_from_settings():
    c = history_client([{'ok': True, 'deleted': 4, 'grok': []}, {'ok': True, 'deleted': 0, 'grok': []},
                        OSError('refused')])
    c.add_message('user', '问')
    assert (await c.clear_history())['deleted'] == 4
    assert c.calls[0] == ('/kiosk/chat/clear', {'device': 'dev-1', 'memory_only': False})
    assert c.threads == {} and published(c, 'thread')[-1]['reset']
    c.add_message('user', '又问')
    await c.clear_history(memory_only=True)    # only the hub's summary: the messages stay
    assert c.calls[1][1]['memory_only'] is True and c.threads['DeepSeekLLM'][0]['text'] == '又问'
    assert await c.clear_history() == {'ok': False, 'error': '连不上小智网关'}
