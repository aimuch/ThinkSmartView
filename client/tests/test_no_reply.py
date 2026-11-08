"""A turn that ends without a word (no network: neither the model nor the hub's spoken apology got
through) says so on the page; a normal or interrupted reply doesn't.

Run like test_barge_in.py (same stubs).
"""
import pytest

from test_barge_in import make_client
from thinksmart_client import app


def toasts(c):
    seen = []
    publish = c.events.publish
    c.events.publish = lambda kind, data: (seen.append(data) if kind == 'toast' else None, publish(kind, data))
    return seen


@pytest.mark.asyncio
@pytest.mark.parametrize('online, text', [(False, '网络还没连上，稍后再试'),
                                          (True, '没收到回答，可能是网络或模型服务连不上，稍后再试')])
async def test_turn_without_a_reply_is_reported(monkeypatch, online, text):
    monkeypatch.setattr(app, 'has_default_route', lambda: online)
    c = make_client()
    seen = toasts(c)
    await c._on_json({'type': 'stt', 'text': '今天星期几'})
    await c._on_json({'type': 'tts', 'state': 'start'})
    await c._on_json({'type': 'tts', 'state': 'stop'})
    assert seen == [{'kind': 'error', 'text': text}]


@pytest.mark.asyncio
async def test_normal_and_interrupted_replies_say_nothing(monkeypatch):
    monkeypatch.setattr(app, 'has_default_route', lambda: False)
    c = make_client()
    seen = toasts(c)
    await c._on_json({'type': 'stt', 'text': '讲个故事'})
    await c._on_json({'type': 'tts', 'state': 'start'})
    await c._on_json({'type': 'tts', 'state': 'sentence_start', 'text': '从前有座山'})
    await c._on_json({'type': 'tts', 'state': 'stop'})
    await c._on_json({'type': 'stt', 'text': '算了'})
    await c._on_json({'type': 'tts', 'state': 'start'})
    await c._on_json({'type': 'tts', 'state': 'stop', 'interrupted': True})
    assert seen == []
