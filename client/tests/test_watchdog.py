"""Dead audio after a PipeWire restart: the client exits for systemd to restart it, and the playback
buffer doesn't grow without end meanwhile.

Run like test_barge_in.py (it stubs the audio stack; importing it first does that here too).
"""
import asyncio

import numpy as np
import pytest

from test_barge_in import make_client
from thinksmart_client import app, audio
from thinksmart_client.audio import MAX_BUFFER, OUT_RATE, STALL, Audio, Player


def sound(seconds):
    return np.zeros((int(OUT_RATE * seconds), 2), np.float32)


def test_push_drops_new_sound_once_the_buffer_is_full():
    p = Player()
    for _ in range(MAX_BUFFER // 10):
        p.push(sound(10))
    assert p.buffered() == MAX_BUFFER
    p.push(sound(10))                       # the speaker stopped pulling: refused
    assert p.buffered() == MAX_BUFFER and p.dropped == 10 * OUT_RATE
    p.pull(OUT_RATE)                        # playing again: room for more
    p.push(sound(0.5))
    assert p.buffered() == MAX_BUFFER - 0.5 and p.dropped == 0


def test_buffered_follows_pull_and_trim():
    p = Player()
    p.push(sound(1))
    p.push(sound(2))
    p.pull(OUT_RATE // 2)
    assert p.buffered() == 2.5
    p.pull(OUT_RATE)
    assert p.buffered() == 1.5
    p.trim(1)
    assert p.buffered() == 1
    p.trim(0)
    assert p.buffered() < 0.01


class FakeStream:
    active = True


def started_audio(monkeypatch, now):
    a = Audio.__new__(Audio)
    a._streams = [FakeStream(), FakeStream()]
    a.last_in = a.last_out = now
    monkeypatch.setattr(audio.time, 'monotonic', lambda: clock[0])
    return a


clock = [0.0]


def test_stalled_when_mic_or_speaker_stop_or_a_stream_dies(monkeypatch):
    clock[0] = 100.0
    a = started_audio(monkeypatch, 100.0)
    assert a.stalled() is None
    clock[0] = 100 + STALL - 0.5            # quiet room, mic switch off: blocks still come, all fine
    assert a.stalled() is None
    clock[0] = 100 + STALL + 1
    a.last_out = clock[0]
    assert 'microphone' in a.stalled()
    a.last_in = clock[0]
    assert a.stalled() is None
    a.last_out = 100.0
    assert 'speaker' in a.stalled()
    a.last_out = clock[0]
    a._streams[0].active = False
    assert a.stalled() == 'stream stopped'


def test_not_stalled_before_the_streams_are_open():
    a = Audio.__new__(Audio)
    a._streams = []
    a.last_in = a.last_out = 0.0
    assert a.stalled() is None


@pytest.mark.asyncio
async def test_ticker_exits_when_the_audio_is_dead(monkeypatch):
    c = make_client()
    exits = []

    class Exited(Exception):
        pass

    def fake_exit(code):
        exits.append(code)
        raise Exited
    monkeypatch.setattr(app.os, '_exit', fake_exit)
    dead = []
    c.audio.stalled = lambda: dead[0] if dead else None
    t = asyncio.create_task(c._ticker())
    await asyncio.sleep(0.25)
    assert not exits and not t.done()
    dead.append('no microphone block for 6 s')
    with pytest.raises(Exited):
        await asyncio.wait_for(t, 1)
    assert exits == [1]
