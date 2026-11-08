"""Content cards (self.screen.show_card) and screen rotation.

Run like test_barge_in.py (same stubs).
"""
import asyncio
import json
import sys
import types

import numpy as np

for name in ('sounddevice', 'sherpa_onnx', 'thinksmart_client.webrtc_apm'):
    sys.modules.setdefault(name, types.ModuleType(name))
if 'thinksmart_client.opus' not in sys.modules:
    opus = types.ModuleType('thinksmart_client.opus')
    opus.Encoder = lambda *a: types.SimpleNamespace(encode=lambda pcm: b'')
    opus.Decoder = lambda *a: types.SimpleNamespace(decode=lambda p: np.zeros(0, np.float32))
    sys.modules['thinksmart_client.opus'] = opus
try:
    import thinksmart_client.speex_aec  # noqa: F401  (needs libspeexdsp)
except OSError:
    sys.modules['thinksmart_client.speex_aec'] = types.SimpleNamespace(LinearAec=object)

from thinksmart_client import app, mcp, rotation  # noqa: E402


def make_client():
    c = app.Client.__new__(app.Client)
    c.events = app.Events()
    c.card = None
    c._card_seq = 0
    c.screen = types.SimpleNamespace(poke=lambda: None)
    q = asyncio.Queue()
    c.events.queues.add(q)
    return c, q


def call(c, name, **args):
    reply = asyncio.run(mcp.handle({'id': 1, 'method': 'tools/call', 'params': {'name': name, 'arguments': args}}, client=c))
    return reply['result']


def test_tools_are_listed():
    names = [t['name'] for t in asyncio.run(mcp.handle({'id': 1, 'method': 'tools/list'}))['result']['tools']]
    assert {'self.screen.show_card', 'self.screen.close_card', 'self.home.control', 'self.home.list_devices',
            'self.home.get_state'} <= set(names)
    assert len(names) == len(set(names))


def test_show_and_close_card():
    c, q = make_client()
    r = call(c, 'self.screen.show_card', title='番茄炒蛋', markdown='## 食材\n- 番茄 2 个', mermaid='flowchart TD\nA-->B',
             images=['https://example.com/a.jpg', 'http://insecure/b.jpg', 'javascript:alert(1)'])
    assert r['isError'] is False and json.loads(r['content'][0]['text'])['success']
    event, card = q.get_nowait()
    assert event == 'card' and card['title'] == '番茄炒蛋' and card['images'] == ['https://example.com/a.jpg']
    assert c.card['id'] == 1
    call(c, 'self.screen.close_card')
    assert c.card is None and q.get_nowait() == ('card', None)


def test_card_needs_a_title_and_is_capped():
    c, _ = make_client()
    r = call(c, 'self.screen.show_card', title='  ')
    assert r['isError'] is True
    card = c.show_card('x' * 500, 'y' * 50000)
    assert len(card['title']) == app.CARD_TITLE_MAX and len(card['markdown']) == app.CARD_TEXT_MAX


def test_orientation():
    assert rotation.orientation(9.5, 0.3) == '90'       # in the stand (ROTATION=90 today)
    assert rotation.orientation(-9.5, 0.3) == '270'
    assert rotation.orientation(0.2, -9.6) == 'normal'
    assert rotation.orientation(0.2, 9.6) == '180'
    assert rotation.orientation(0.5, 0.4) is None        # lying flat
    assert rotation.orientation(6.5, 6.0) is None        # in between


def test_rotation_set_validates(monkeypatch):
    saved, events = {}, []
    r = rotation.Rotation(False, lambda e, d: events.append(e), lambda **kw: saved.update(kw))
    calls = []

    async def fake_run(*args):
        calls.append(args)
    monkeypatch.setattr(rotation, '_run', fake_run)
    monkeypatch.setattr(rotation, 'current', lambda: calls and calls[0][1] or '90')
    r.value = '90'
    try:
        asyncio.run(r.set(value='sideways'))
        assert False
    except ValueError:
        pass
    asyncio.run(r.set(auto=True))
    assert saved == {'rotation_auto': True} and r.auto
    asyncio.run(r.set(value='normal'))
    assert calls == [('rotation', 'normal'), ('restart',)] and r.value == 'normal'
    assert r.auto is False and saved['rotation_auto'] is False   # a manual pick turns auto off
