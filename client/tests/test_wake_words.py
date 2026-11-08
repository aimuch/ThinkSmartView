"""Custom wake words (wake_words.py): characters -> keyword tokens, the checks, live apply, recording, voice tool.

Run like test_barge_in.py (same stubs).
"""
import asyncio
import json
import sys
import types

import numpy as np
import pytest

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

from thinksmart_client import app, config, mcp, wake_verify, wake_words as ww  # noqa: E402

TOKENS = {line.split()[0] for line in (app.ROOT / 'models' / 'kws-zh' / 'tokens.txt').read_text().splitlines() if line}


def test_defaults_come_out_of_the_table_unchanged():
    for line in config.DEFAULTS['wake_words']:
        label = ww.parse(line)['label']
        assert ww.keyword(label) == line
    assert ww.parse(config.DEFAULTS['wake_words'][1]) == {'label': '哈喽小麦', 'pinyin': 'hā lóu xiǎo mài'}


def test_table_only_has_tokens_the_model_knows():
    for line in ww.TABLE.read_text().splitlines():
        if line.startswith('#'):
            continue
        ch, readings = line.split('\t')
        for r in readings.split('|'):
            assert set(r.split()) <= TOKENS, (ch, r)


def test_polyphones_pick_a_reading():
    assert ww.readings('乐')[:2] == ['l è', 'y uè']
    assert ww.keyword('小乐同学', [0, 1, 0, 0]) == 'x iǎo y uè t óng x ué @小乐同学'
    with pytest.raises(ValueError):
        ww.keyword('小乐', [0, 5])
    assert ww.syllables('y ī ā'.split()) == ['yī', 'ā']


@pytest.mark.parametrize('text, error, warning', [
    ('小天小天。', None, None),
    ('你好小天', None, None),
    ('小天天', None, '三个字'),
    ('小天', '两个字', None),
    ('天', '一个字', None),
    ('', '没听清', None),
    ('Hello小天', '只能用中文', None),
    ('小麦小麦', '已经有', None),
    ('小卖小卖', '读音几乎一样', None),
    ('一二三四五六七八九', '太长', None),
])
def test_checks(text, error, warning):
    r = ww.analyse(text, ['小麦小麦', '哈喽小麦'])
    assert (r['error'] is None) == (error is None) and (error or '') in (r['error'] or '')
    assert (r['warning'] is None) == (warning is None) and (warning or '') in (r['warning'] or '')


def test_analysis_lists_readings_per_character():
    r = ww.analyse('小乐同学')
    assert r['label'] == '小乐同学' and [c['ch'] for c in r['chars']] == list('小乐同学')
    assert r['chars'][1]['pinyin'][:2] == ['lè', 'yuè'] and r['chars'][0]['pinyin'] == ['xiǎo']


class FakeKws:
    def __init__(self, model_dir, keywords, *a):
        self.keywords = list(keywords)
        self.fed = 0

    def feed(self, block):
        self.fed += 1


def make_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'PATH', tmp_path / 'config.json')
    monkeypatch.setattr(app, 'Kws', FakeKws)
    c = app.Client.__new__(app.Client)
    c.events = app.Events()
    c.cfg = {**config.DEFAULTS, 'wake_words': list(config.DEFAULTS['wake_words'])}
    c.kws = FakeKws('', c.cfg['wake_words'])
    c.verifier = None
    c.wake_rec, c.wake_rec_t, c.wake_lock = None, 0.0, asyncio.Lock()
    c.uploading = c.vad_ended = False
    c.state = 'idle'
    c.backlog = None
    c.in_turn = lambda: False
    c.keys = types.SimpleNamespace(mic_on=True)
    c.screen = types.SimpleNamespace(poke=lambda: None)
    c.played = []
    c.play = c.played.append
    q = asyncio.Queue()
    c.events.queues.add(q)
    return c, q


def test_add_remove_reset_apply_at_once(tmp_path, monkeypatch):
    c, q = make_client(tmp_path, monkeypatch)

    async def run():
        view, warning = await c.add_wake_word('小天小天')
        assert warning is None and view['words'][-1] == {'label': '小天小天', 'pinyin': 'xiǎo tiān xiǎo tiān'}
        assert c.kws.keywords[-1] == 'x iǎo t iān x iǎo t iān @小天小天' and view['custom']
        assert json.loads(config.PATH.read_text())['wake_words'][-1].endswith('@小天小天')
        assert c.verifier is not None and '小天小天' in c.verifier.matcher.labels
        with pytest.raises(ValueError, match='已经有'):
            await c.add_wake_word('小天小天')
        for label in ('小麦小麦', '哈喽小麦', '小麦同学'):
            view = await c.remove_wake_word(label)
        assert view['primary'] == '小天小天' and c.wake_label() == '小天小天'
        with pytest.raises(ValueError, match='至少'):
            await c.remove_wake_word('小天小天')
        view = await c.reset_wake_words()
        assert not view['custom'] and 'wake_words' not in json.loads(config.PATH.read_text())
        assert c.kws.keywords == config.DEFAULTS['wake_words']
    asyncio.run(run())
    events = [q.get_nowait()[0] for _ in range(q.qsize())]
    assert events.count('wake_words') == 5   # add, three removes, reset


def test_recording_skips_the_wake_word_and_recognises(tmp_path, monkeypatch):
    c, _ = make_client(tmp_path, monkeypatch)
    sent = []

    def text(self, audio):
        sent.append(audio)
        return '小乐同学'
    monkeypatch.setattr(wake_verify.HubChecker, 'text', text)

    async def run():
        await c.wake_record_start()
        assert c.played == ['report'] and c.wake_view()['recording']
        tone = (0.1 * np.sin(np.arange(320) / 3)).astype(np.float32)
        for _ in range(300):           # 6 s: kept to 4
            c._on_block(tone, tone)
        assert c.kws.fed == 0 and sum(len(b) for b in c.wake_rec) == 4 * 16000
        heard = await c.wake_record_stop()
        assert heard['label'] == '小乐同学' and heard['error'] is None and len(sent) == 1
        assert c.wake_rec is None
        c._on_block(tone, tone)
        assert c.kws.fed == 1         # listening for the wake word again
        view, _ = await c.add_wake_word(heard['label'], [0, 1, 0, 0])
        assert view['words'][-1]['pinyin'] == 'xiǎo yuè tóng xué'
    asyncio.run(run())


def test_silence_and_busy_states(tmp_path, monkeypatch):
    c, _ = make_client(tmp_path, monkeypatch)

    async def run():
        await c.wake_record_start()
        c._on_block(np.zeros(320, np.float32), np.zeros(320, np.float32))
        assert (await c.wake_record_stop())['error'].startswith('没听清')
        c.state = 'speaking'
        with pytest.raises(ValueError, match='对话'):
            await c.wake_record_start()
        c.state, c.keys.mic_on = 'idle', False
        with pytest.raises(ValueError, match='麦克风'):
            await c.wake_record_start()
    asyncio.run(run())


def call(c, **args):
    reply = asyncio.run(mcp.handle({'id': 1, 'method': 'tools/call',
                                    'params': {'name': 'self.wake_word.update', 'arguments': args}}, client=c))
    assert reply['result']['isError'] is False
    return json.loads(reply['result']['content'][0]['text'])


def test_voice_tool(tmp_path, monkeypatch):
    c, _ = make_client(tmp_path, monkeypatch)
    names = [t['name'] for t in asyncio.run(mcp.handle({'id': 1, 'method': 'tools/list'}))['result']['tools']]
    assert 'self.wake_word.update' in names and len(names) == len(set(names))
    assert call(c, action='list')['wake_words'] == ['小麦小麦', '哈喽小麦', '小麦同学']
    r = call(c, action='add', word='小天小天')
    assert r['success'] and r['added']['pinyin'] == 'xiǎo tiān xiǎo tiān' and r['wake_words'][-1] == '小天小天'
    r = call(c, action='add', word='小天')
    assert not r['success'] and '两个字' in r['reason']
    assert call(c, action='add', word='天天乐')['warning']
    assert call(c, action='remove', word='哈喽小麦')['wake_words'] == ['小麦小麦', '小麦同学', '小天小天', '天天乐']
    assert call(c, action='reset')['wake_words'] == ['小麦小麦', '哈喽小麦', '小麦同学']
    assert not call(c, action='nope')['success']
