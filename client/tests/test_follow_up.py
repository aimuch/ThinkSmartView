"""Which replies keep the microphone open for a follow-up (config follow_up, default 'question')."""
import asyncio

import pytest

from test_barge_in import PACKET, make_client, play
from thinksmart_client import app


async def reply(c, *sentences, mode='question', question='明天要带伞吗'):
    """Ask, hear the whole reply, let it finish playing."""
    c.cfg['follow_up'] = mode
    await c.start_listening('realtime')
    await c._on_json({'type': 'stt', 'text': question})
    await c._on_json({'type': 'tts', 'state': 'start'})
    for s in sentences:
        await c._on_json({'type': 'tts', 'state': 'sentence_start', 'text': s})
        c._on_audio(PACKET)
    await c._on_json({'type': 'tts', 'state': 'stop'})
    play(c, 1)
    await asyncio.sleep(0.1)


@pytest.mark.parametrize('text, asks', [
    ('明天有雨，记得带伞', False),
    ('好的，已经帮你设好了七点的闹钟', False),
    ('你说的是哪个城市？', True),
    ('要我放旅行的照片吗', True),                          # the model left the question mark out
    ('要不要设个闹钟？比如明早七点', True),               # a short add-on after the question
    ('想听哪一个？我可以先讲第一个，也可以换成别的故事，你挑一个就行', False),   # the question was long ago
    ('还需要别的吗？😊', True),
    ('你说的是「旅行@2024」那个相册？」', True),
    ('', False),
])
def test_asks_back(text, asks):
    assert app.asks_back(text) is asks


@pytest.mark.asyncio
async def test_an_answer_closes_the_mic():
    c = make_client()
    await reply(c, '明天有雨', '记得带伞')
    assert not c.uploading and c.state == 'idle' and not c.listen_after_reply
    assert ('listen', 'start') not in c.hub.kinds()[1:]    # only the question's own listening
    assert c.hub.connected                                   # waking again keeps the conversation


@pytest.mark.asyncio
async def test_a_question_back_keeps_listening():
    c = make_client()
    await reply(c, '你想去哪个城市', '北京还是上海？')
    assert c.uploading and c.state == 'listening' and c.deadline
    assert c.hub.kinds()[-1] == ('listen', 'start')


@pytest.mark.asyncio
async def test_waking_after_an_answer_is_the_next_turn():
    c = make_client()
    await reply(c, '明天有雨，记得带伞')
    c.chime = lambda source: None

    async def connected():
        return True
    c.ensure_connected = connected
    await c.wake('voice')
    assert c.uploading and c.state == 'listening'


@pytest.mark.asyncio
@pytest.mark.parametrize('mode, text, listens', [
    ('always', '明天有雨，记得带伞', True),
    ('never', '你说的是哪个城市？', False),
    ('bogus', '明天有雨，记得带伞', False),     # an unknown value means the default
])
async def test_other_modes(mode, text, listens):
    c = make_client()
    await reply(c, text, mode=mode)
    assert c.uploading is listens and c.state == ('listening' if listens else 'idle')


@pytest.mark.asyncio
async def test_a_turn_without_words_still_reopens():
    """Nothing shown (the model was unreachable): the user will want to say it again right away."""
    c = make_client()
    c.events.publish = lambda *a: None
    await reply(c)
    assert c.uploading and c.state == 'listening'
