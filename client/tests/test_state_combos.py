"""Bugs that only showed when states combined (state review, 2026-11-01).

Run like test_barge_in.py (same stubs).
"""
import asyncio

import pytest

import test_barge_in
from test_connect import SlowHub
from test_models import client_with_hub


def make_client():
    c = test_barge_in.make_client()
    c.tools_listed = asyncio.Event()
    c.play = lambda name: None
    return c


def recorder(c):
    log = []
    orig = c.events.publish

    def publish(event, data):
        log.append((event, data))
        return orig(event, data)
    c.events.publish = publish
    return log


@pytest.mark.asyncio
async def test_ptt_released_before_connected_listens_like_a_tap():
    # held the mic button just over 350 ms and let go before the hub's hello came back: a manual
    # listen would never end (no deadline, the hub's VAD is off), so it listens like a tap instead
    c = make_client()
    c.hub = SlowHub()
    start = asyncio.create_task(c.ptt_start())
    await asyncio.sleep(0.01)
    await c.ptt_stop()
    await start
    assert c.hub.sent[-1] == {'type': 'listen', 'state': 'start', 'mode': 'realtime'}
    assert c.uploading and c.state == 'listening' and c.deadline


@pytest.mark.asyncio
async def test_ptt_held_too_long_is_stopped(monkeypatch):
    from thinksmart_client import app as app_mod
    monkeypatch.setattr(app_mod, 'MAX_LISTEN', 0.2)
    c = make_client()
    await c.ptt_start()
    assert c.uploading and c.listen_mode == 'manual' and c.deadline is None
    ticker = asyncio.create_task(c._ticker())
    await asyncio.sleep(0.45)
    ticker.cancel()
    assert not c.uploading and {'type': 'listen', 'state': 'stop'} in c.hub.sent


@pytest.mark.asyncio
async def test_report_read_during_the_listening_window_closes_the_mic():
    c = make_client()
    await c.wake('voice')                   # "小麦小麦" because the page said a report is waiting
    # the hub reads it on its own: reports.speak -> speak_txt sends no stt and no "tts start"
    await c._on_json({'type': 'tts', 'state': 'sentence_start', 'text': '孙悟空：日志查完了'})
    assert c.state == 'speaking'
    assert not c.uploading and c.listen_after_reply   # follow-up window after it, like any reply


@pytest.mark.asyncio
async def test_switch_while_thinking_interrupts_the_old_model():
    c, calls = client_with_hub({'ok': True, 'selected': 'DeepSeekLLM', 'options': [
        {'kind': 'api', 'vendor': 'deepseek', 'label': 'DeepSeek', 'entry': 'DeepSeekLLM', 'model': 'deepseek-chat'},
        {'kind': 'cli', 'vendor': 'claude-code', 'label': 'Claude Code', 'entry': 'ClaudeCLI', 'model': ''}]})
    c._apply_models({'selected': 'ClaudeCLI', 'options': [
        {'kind': 'cli', 'vendor': 'claude-code', 'label': 'Claude Code', 'entry': 'ClaudeCLI', 'model': ''}]})
    await c.start_listening('realtime')
    await c._on_json({'type': 'stt', 'text': '帮我写首诗'})
    assert c.state == 'thinking'
    await c.set_model('api', 'deepseek', 'DeepSeekLLM')
    assert any(m.get('type') == 'abort' for m in c.hub.sent)
    assert c.threads['ClaudeCLI'][-1]['interrupted']   # the question stays with the old model, marked
    assert c.question is None and c.state != 'thinking'


@pytest.mark.asyncio
async def test_question_cleared_mid_turn_does_not_come_back_on_abort():
    c = make_client()
    c.models = {'current': {'entry': 'DeepSeekLLM'}, 'options': []}
    c.cfg['device_id'] = 'dev-1'

    async def hub_http(path, body=None):
        return {'ok': True, 'deleted': 1}
    c._hub_http = hub_http
    await c.start_listening('realtime')
    await c._on_json({'type': 'stt', 'text': '问一个问题'})
    await c.clear_history()
    log = recorder(c)
    await c.abort()
    assert not [d for e, d in log if e == 'message']


@pytest.mark.asyncio
async def test_normal_reply_and_push_to_talk_unaffected_by_the_report_rule():
    c = make_client()
    await c.wake('voice')
    await c._on_json({'type': 'stt', 'text': '你好'})          # the stt closes the mic as before
    await c._on_json({'type': 'tts', 'state': 'start'})
    await c._on_json({'type': 'tts', 'state': 'sentence_start', 'text': '你好呀'})
    assert c.state == 'speaking' and not c.uploading and c.listen_after_reply
    c = make_client()
    await c.ptt_start()                                        # held: the report rule leaves it alone
    await c._on_json({'type': 'tts', 'state': 'sentence_start', 'text': '孙悟空：日志查完了'})
    assert c.uploading and c.listen_mode == 'manual'


@pytest.mark.asyncio
async def test_report_after_an_abort_on_the_same_connection_is_heard():
    # interrupted a Grok Bot reply (it goes on in the cloud), stayed connected; then its report comes
    c = make_client()
    await c.wake('voice')
    await c._on_json({'type': 'stt', 'text': '讲个故事'})
    await c._on_json({'type': 'tts', 'state': 'start'})
    await c._on_json({'type': 'tts', 'state': 'sentence_start', 'text': '从前'})
    await c.abort('wake_word_detected')
    assert c.mute_audio
    await c._on_json({'type': 'tts', 'state': 'sentence_start', 'text': '从前有座山'})   # late, before the confirmation
    assert c.mute_audio and c.threads[None][-1]['text'] == '从前'
    await c._on_json({'type': 'tts', 'state': 'stop', 'interrupted': True})
    await c._on_json({'type': 'tts', 'state': 'sentence_start', 'text': '孙悟空做完了：故事讲完了'})
    assert not c.mute_audio and c.state == 'speaking'
    assert c.threads[None][-1]['text'] == '孙悟空做完了：故事讲完了'


@pytest.mark.asyncio
async def test_mic_switched_off_during_reply_ends_in_idle():
    c = make_client()
    await test_barge_in.ask(c)
    c._on_audio(test_barge_in.PACKET)
    c.keys.mic_on = False                   # privacy switch flipped during the reply
    c._on_mic(False)
    await c._on_json({'type': 'tts', 'state': 'stop'})
    test_barge_in.play(c, 0.1)
    await asyncio.sleep(0.1)
    assert not c.uploading and c.state == 'idle' and not c.listen_after_reply


@pytest.mark.asyncio
async def test_mic_switched_off_and_on_again_during_reply_still_listens():
    c = make_client()
    await test_barge_in.ask(c)
    c._on_audio(test_barge_in.PACKET)
    c.keys.mic_on = False
    c._on_mic(False)
    c.keys.mic_on = True
    c._on_mic(True)
    await c._on_json({'type': 'tts', 'state': 'stop'})
    test_barge_in.play(c, 0.1)
    await asyncio.sleep(0.1)
    assert c.uploading and c.state == 'listening'
