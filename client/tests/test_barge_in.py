"""Interrupting a reply: only the wake word or the button does it; talking during a reply doesn't.

Runs without the device's audio stack (sound card, libopus, WebRTC APM and sherpa-onnx are stubbed):
  uv run --no-project --with numpy --with soxr --with websockets --with aiohttp --with pytest --with pytest-asyncio \
      python -m pytest -q tests
(on a Mac add DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib so test_alarms also decodes the real tone files)
"""
import asyncio
import time
import sys
import types

import numpy as np
import pytest

for name in ('sounddevice', 'sherpa_onnx', 'thinksmart_client.webrtc_apm'):
    sys.modules.setdefault(name, types.ModuleType(name))


class FakeDecoder:
    def __init__(self, *_):
        pass

    def decode(self, packet):
        return np.zeros(len(packet), dtype=np.float32)   # a "packet" of n bytes = n samples


opus = types.ModuleType('thinksmart_client.opus')
opus.Encoder = lambda *a: types.SimpleNamespace(encode=lambda pcm: b'')
opus.Decoder = FakeDecoder
sys.modules['thinksmart_client.opus'] = opus

from thinksmart_client import app  # noqa: E402
from thinksmart_client.audio import FADE, OUT_RATE, Player  # noqa: E402

PACKET = bytes(int(OUT_RATE * 0.06))   # 60 ms of reply audio
FADE_S = FADE / OUT_RATE                # what an interruption leaves to play: the old audio fading out


class FakeHub:
    def __init__(self):
        self.sent = []
        self.connected = True
        self.features = {}

    async def send(self, msg):
        self.sent.append(msg)
        return True

    def kinds(self):
        return [(m['type'], m.get('state')) for m in self.sent]


def make_client():
    c = app.Client.__new__(app.Client)
    c.cfg = {'follow_up_seconds': 10, 'follow_up': 'always'}   # test_follow_up.py tries the others
    c.events = app.Events()
    c.state = 'idle'
    c.uploading = False
    c.deadline = None
    c.listen_since = 0.0
    c.awaiting_text = False
    c.recognising = False
    c.hub_pending = False
    c.vad_ended = False
    c.aborted_at = c.abort_pending = c.question = None
    c.heard = False
    c.mute_audio = False
    c.listen_after_reply = False
    c.powering_off = False
    c.reply_samples = c.reply_packets = c.muted_packets = 0
    c.recognised = 0
    c.threads, c.entry, c.models = {}, None, {'current': None, 'options': []}
    c.bot_msg = None
    c.decoder = None
    c._seq = 0
    c.hub = FakeHub()
    c.audio = types.SimpleNamespace(player=Player(), aec_hold=False, mic_level=0.0, take_echo_levels=lambda: None,
                                    stalled=lambda: None)
    c.screen = types.SimpleNamespace(hold=lambda: None, release=lambda after=None: None, poke=lambda after=None: None,
                                     level=None, dimmed=False, current_level=lambda: 50)
    c.keys = types.SimpleNamespace(mic_on=True)
    c.sleep, c.sleeping, c.reports = {}, False, 0
    c.screen_off = {'enabled': True, 'minutes': 5}
    c.slides, c._slides_timer = None, None
    c.sounds = {}
    c.verifier = None
    c.speech_end = 0.0
    c.connecting = asyncio.Lock()
    c.volume, c.ring, c.preview = 60, None, None

    async def no_models():
        pass
    c.refresh_models = no_models
    return c


async def ask(c, question='讲个故事'):
    """The user woke it, said something, the hub recognised it and starts answering."""
    await c.start_listening('realtime')
    await c._on_json({'type': 'stt', 'text': question})
    await c._on_json({'type': 'tts', 'state': 'start'})
    await c._on_json({'type': 'tts', 'state': 'sentence_start', 'text': '从前有座山'})


def play(c, seconds):
    """The speaker played this much of what is queued."""
    c.audio.player.pull(int(OUT_RATE * seconds))


@pytest.mark.asyncio
async def test_mic_stays_closed_during_the_reply_and_reopens_after_it():
    c = make_client()
    await ask(c)
    assert not c.uploading and c.state == 'speaking'    # talking now reaches no one; the wake word is listened for
    for _ in range(5):
        c._on_audio(PACKET)
    await c._on_json({'type': 'tts', 'state': 'stop'})
    assert not c.uploading                              # 0.3 s still to play: not yet
    play(c, 0.3)
    await asyncio.sleep(0.1)
    assert c.uploading and c.state == 'listening' and c.deadline
    assert c.hub.kinds()[-1] == ('listen', 'start')


@pytest.mark.asyncio
async def test_wake_word_during_reply_interrupts_and_reports_what_was_heard():
    c = make_client()
    await ask(c)
    for _ in range(50):                                 # 3 s of reply received
        c._on_audio(PACKET)
    play(c, 1.2)
    await c.wake('voice')
    abort = next(m for m in c.hub.sent if m['type'] == 'abort')
    assert abort['reason'] == 'wake_word_detected'
    assert abs(abort['played_ms'] - 1200) <= 60
    assert c.audio.player.buffered() <= FADE_S and c.mute_audio
    assert c.uploading and c.state == 'listening'      # what comes next is a new question
    assert c.threads[None][-1]['interrupted']
    # the hub's own stop for the abort must not reopen anything or restart the old reply
    await c._on_json({'type': 'tts', 'state': 'stop', 'interrupted': True})
    c._on_audio(PACKET)
    assert c.audio.player.buffered() <= FADE_S
    # the next question is a normal turn
    await c._on_json({'type': 'stt', 'text': '换个问题'})
    await c._on_json({'type': 'tts', 'state': 'start'})
    c._on_audio(PACKET)
    assert c.audio.player.buffered() > 0 and not c.uploading


@pytest.mark.asyncio
async def test_wake_word_while_thinking_interrupts_too():
    c = make_client()
    await c.start_listening('realtime')
    await c._on_json({'type': 'stt', 'text': '帮我写个程序'})
    await c._on_json({'type': 'tts', 'state': 'start'})
    assert c.state == 'thinking'
    await c.wake('voice')
    abort = next(m for m in c.hub.sent if m['type'] == 'abort')
    assert abort['played_ms'] == 0
    assert c.state == 'listening'


@pytest.mark.asyncio
async def test_button_abort_goes_back_to_listening_or_idle():
    c = make_client()
    await ask(c)
    c._on_audio(PACKET)
    await c.abort()
    assert c.hub.kinds()[-1] == ('abort', None)
    assert c.state == 'idle' and not c.uploading and not c.listen_after_reply


@pytest.mark.asyncio
async def test_hub_cutting_the_reply_marks_it_interrupted():
    c = make_client()
    await ask(c)
    for _ in range(5):
        c._on_audio(PACKET)
    await c._on_json({'type': 'tts', 'state': 'stop', 'interrupted': True})
    assert c.audio.player.buffered() <= FADE_S
    assert c.threads[None][-1]['interrupted']


@pytest.mark.asyncio
async def test_newer_question_while_reopening_keeps_the_mic_closed():
    c = make_client()
    await ask(c)
    sent = c.hub.send

    async def slow_send(msg):
        if msg.get('state') == 'start':
            await c._on_json({'type': 'stt', 'text': '还有一句'})   # arrives while we reopen
        return await sent(msg)
    c.hub.send = slow_send
    await c._on_json({'type': 'tts', 'state': 'stop', 'interrupted': True})
    await asyncio.sleep(0.1)
    assert not c.uploading and c.listen_after_reply and c.state == 'thinking'


def test_linear_canceller_removes_echo_and_keeps_the_voice():
    from thinksmart_client.speex_aec import LinearAec
    if LinearAec is object:
        pytest.skip('needs libspeexdsp (brew install speexdsp)')
    rng = np.random.default_rng(1)
    rate, n = 16000, 16000 * 12
    far = rng.normal(0, 0.1, n).astype(np.float32)
    room = np.zeros(1200, np.float32)
    room[[400, 700, 1100]] = (0.3, -0.15, 0.05)          # echo 25-70 ms later, a few reflections
    echo = np.convolve(far, room)[:n].astype(np.float32)
    voice = np.zeros(n, np.float32)
    voice[10 * rate:11 * rate] = 0.03 * np.sin(2 * np.pi * 300 * np.arange(rate) / rate)
    a = LinearAec(rate, 320, tail_ms=500)
    out = np.concatenate([a.process(echo[i:i + 320] + voice[i:i + 320], far[i:i + 320])
                          for i in range(0, n, 320)])
    db = lambda x: 10 * np.log10(np.mean(x ** 2))
    settled = slice(5 * rate, 10 * rate)
    assert db(echo[settled]) - db(out[settled]) > 15          # echo removed
    talk = slice(10 * rate + 1600, 11 * rate)
    assert abs(db(out[talk]) - db(voice[talk] + echo[talk] * 0.1)) < 3   # the voice kept


@pytest.mark.asyncio
async def test_question_shown_before_the_models_are_asked():
    """The question reaches the page at once; the hub's model list (slow) comes after."""
    c = make_client()
    c.models = {'current': {'entry': 'DeepSeekLLM'}, 'options': []}
    shown = []
    c.events.publish = lambda event, data: shown.append((event, data))
    asked = asyncio.Event()

    async def slow_models():
        await asked.wait()
        c.models = {'current': {'entry': 'ClaudeCLI'}, 'options': []}   # the hub switched: called by name
        c.entry = 'ClaudeCLI'
    c.refresh_models = slow_models
    await c.start_listening('realtime')
    await c._on_json({'type': 'vad', 'state': 'end'})
    assert c.status()['recognising'] and c.state == 'listening'
    await c._on_json({'type': 'stt', 'text': '嗨 Claude 你好'})
    assert [d['text'] for e, d in shown if e == 'message'] == ['嗨 Claude 你好']
    assert c.state == 'thinking' and not c.status()['recognising']
    asked.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    # the question moved to the thread of the model that answers it
    assert [m['text'] for m in c.threads['ClaudeCLI']] == ['嗨 Claude 你好'] and c.threads['DeepSeekLLM'] == []
    assert shown[-1] == ('thread', {'entry': 'ClaudeCLI', 'messages': c.threads['ClaudeCLI']})


@pytest.mark.asyncio
async def test_tool_call_note_is_not_a_question():
    c = make_client()
    await ask(c)
    await c._on_json({'type': 'stt', 'text': '% get_weather'})
    assert [m['text'] for t in c.threads.values() for m in t if m['role'] == 'user'] == ['讲个故事']
    assert c.recognised == 1 and c.state == 'speaking'


def toasts(c):
    shown = []
    c.events.publish = lambda event, data: shown.append(data['text']) if event == 'toast' else None
    return shown


async def tick(c, seconds=0.25):
    """Run the client's timer loop for a moment."""
    t = asyncio.create_task(c._ticker())
    await asyncio.sleep(seconds)
    t.cancel()


@pytest.mark.asyncio
async def test_hub_recognised_nothing_while_waiting_for_text():
    """A cough after the mic closed: the hub says so and "didn't catch that" comes at once, not 15 s later."""
    c = make_client()
    shown = toasts(c)
    await c.start_listening('realtime')
    await c._on_json({'type': 'vad', 'state': 'end'})
    c.uploading, c.awaiting_text = False, True       # the client's own deadline closed the mic meanwhile
    c.set_state('thinking')
    assert c.status()['recognising']
    await c._on_json({'type': 'vad', 'state': 'empty'})
    assert c.state == 'idle' and not c.awaiting_text and shown == ['没听清，可以再说一次「小麦小麦」']


@pytest.mark.asyncio
async def test_hub_recognised_nothing_while_the_mic_is_open():
    c = make_client()
    await c.start_listening('realtime')
    await c._on_json({'type': 'vad', 'state': 'end'})
    assert c.status()['recognising']
    await c._on_json({'type': 'vad', 'state': 'empty'})
    assert c.state == 'listening' and c.uploading and not c.status()['recognising']   # still listening


@pytest.mark.asyncio
async def test_deadline_does_not_wait_when_the_hub_answered_everything():
    c = make_client()
    shown = toasts(c)
    c.hub.features = {'vad_end': True}
    await c.start_listening('realtime')
    c.speech_end = time.monotonic()                   # they coughed
    await c._on_json({'type': 'vad', 'state': 'end'})
    await c._on_json({'type': 'vad', 'state': 'empty'})
    c.deadline = time.monotonic() - 1
    await tick(c)
    assert c.state == 'idle' and not c.awaiting_text and shown == ['没听清，可以再说一次「小麦小麦」']


@pytest.mark.asyncio
async def test_deadline_waits_for_text_from_an_older_hub_or_while_recognising():
    for features, pending in (({}, False), ({'vad_end': True}, True)):
        c = make_client()
        c.hub.features = features
        await c.start_listening('realtime')
        c.speech_end = time.monotonic()
        if pending:
            await c._on_json({'type': 'vad', 'state': 'end'})
        c.deadline = time.monotonic() - 1
        await tick(c)
        assert c.state == 'thinking' and c.awaiting_text, features


@pytest.mark.asyncio
async def test_push_to_talk_with_nothing_recognised_ends_at_once():
    c = make_client()
    shown = toasts(c)
    await c.ptt_start()
    await c.ptt_stop()
    assert c.state == 'thinking' and c.status()['recognising']
    await c._on_json({'type': 'vad', 'state': 'empty'})
    assert c.state == 'idle' and shown == ['没听清，可以再说一次「小麦小麦」']


# ---- 网关拦下的句子（core/utils/turn_filter.py，设计画布第 52 块） ----

@pytest.mark.asyncio
async def test_hub_filtered_a_second_wake_word_keeps_listening():
    c = make_client()
    shown = toasts(c)
    played = []
    c.play = played.append
    await c.start_listening('realtime')
    await c._on_json({'type': 'vad', 'state': 'end'})
    c.uploading, c.awaiting_text = False, True       # the mic had closed while it was being recognised
    await c._on_json({'type': 'vad', 'state': 'empty', 'reason': 'wake'})
    assert c.state == 'listening' and c.uploading and not c.awaiting_text and shown == []
    assert played == ['wake'] and c.hub.kinds()[-1] == ('listen', 'start') and c.deadline


@pytest.mark.asyncio
async def test_hub_filtered_filler_and_close():
    c = make_client()
    shown = toasts(c)
    await c.start_listening('realtime', follow_up=True)
    assert c.hub.sent[-1] == {'type': 'listen', 'state': 'start', 'mode': 'realtime', 'follow_up': True, 'lock': True}
    await c._on_json({'type': 'vad', 'state': 'end'})
    await c._on_json({'type': 'vad', 'state': 'empty', 'reason': 'filler'})       # 「嗯」：窗口照开
    assert c.state == 'listening' and c.uploading and shown == []
    await c._on_json({'type': 'vad', 'state': 'end'})
    await c._on_json({'type': 'vad', 'state': 'empty', 'reason': 'close'})        # 「谢谢」：收尾
    assert c.state == 'idle' and not c.uploading and shown == []


def test_listen_modes_scenes_and_settings(monkeypatch):
    c = make_client()
    c.cfg = {'follow_up': 'question', 'follow_up_seconds': 10}
    c.cooking = None
    c.study = types.SimpleNamespace(enabled=False)
    saved = {}
    monkeypatch.setattr(app.config, 'save', lambda **kw: saved.update(kw))
    assert c.wants_follow_up('今天晴。') is False and c.wants_follow_up('要提醒你吗？') is True
    c.study.enabled = True
    assert c.wants_follow_up('今天晴。') is True and c.listen_view()['scene'] == 'study'   # 学习陪伴：每次都接着听
    c.study.enabled, c.sleeping = False, True
    assert c.wants_follow_up('要提醒你吗？') is False                                     # 睡眠时段：每句都要唤醒
    c.sleeping = False
    v = c.set_listen('never', 20, False)
    assert v['follow_up'] == 'never' and v['seconds'] == 20 and v['lock'] is False
    assert saved == {'follow_up': 'never', 'follow_up_seconds': 20, 'follow_lock': False}
    assert c.follow_up_seconds() == 20 and c.wants_follow_up('要提醒你吗？') is False
    with pytest.raises(ValueError):
        c.set_listen('sometimes')
    with pytest.raises(ValueError):
        c.set_listen(seconds=7)


# ---- 就近唤醒（设计画布第 55 块） ----

def test_wake_loudness():
    t = np.arange(16000) / 16000
    loud = (0.3 * np.sin(2 * np.pi * 300 * t)).astype(np.float32)
    quiet = loud * 0.05
    assert app.wake_loudness(loud) > app.wake_loudness(quiet) + 20
    assert app.wake_loudness(np.zeros(0, np.float32)) == -100.0


@pytest.mark.asyncio
async def test_a_nearer_device_answers(monkeypatch):
    c = make_client()
    c.backlog = []
    calls = []

    async def lose(audio):
        calls.append(len(audio))
        return False
    c._claim_wake = lose

    async def no_kids():
        return False
    c.kids_refuse = no_kids
    c.cooking = None
    await c.voice_wake('小麦小麦', np.zeros(16000, np.float32))
    assert calls == [16000] and c.state == 'idle' and c.hub.sent == [] and c.backlog is None
    c.uploading = True                                   # 接着听的时候：不用问网关
    calls.clear()
    c.verifier = None

    async def woke(source='button', chimed=False):
        calls.append(source)
    c.wake = woke
    await c.voice_wake('小麦小麦', np.zeros(16000, np.float32))
    assert calls == ['voice']
