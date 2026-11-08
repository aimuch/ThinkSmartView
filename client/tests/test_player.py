"""The playback buffer fades where sound is cut off mid-wave instead of jumping to zero (a click).

  uv run --no-project --with numpy --with soxr --with websockets --with aiohttp --with pytest --with pytest-asyncio \\
      python -m pytest -q tests
"""
import sys
import types

import numpy as np

sys.modules.setdefault('sounddevice', types.ModuleType('sounddevice'))
sys.modules.setdefault('thinksmart_client.webrtc_apm', types.ModuleType('thinksmart_client.webrtc_apm'))

from thinksmart_client.audio import CUT_LEVEL, FADE, OUT_RATE, Player  # noqa: E402

BLOCK = 960


def tone(seconds, amp=0.5, phase=0.0):
    t = np.arange(int(OUT_RATE * seconds)) / OUT_RATE + phase
    x = (amp * np.sin(2 * np.pi * 300 * t)).astype(np.float32)
    return np.repeat(x[:, None], 2, axis=1)


def play(p, blocks):
    return np.concatenate([p.pull(BLOCK) for _ in range(blocks)])[:, 0]


def max_step(x):
    return float(np.abs(np.diff(x)).max())


def test_running_dry_mid_word_fades_out_and_fades_back_in():
    p = Player()
    p.push(tone(0.05))                 # 2400 samples: runs out in the middle of the third block
    first = play(p, 3)
    steady = 0.5 * 2 * np.pi * 300 / OUT_RATE    # the tone's own largest step
    assert max_step(first) <= steady * 1.05      # no jump to zero
    assert len(p.ran_out) == 1 and p.ran_out[0] > CUT_LEVEL
    p.push(tone(0.05, phase=0.05))               # the rest of the word arrives late
    second = play(p, 3)
    assert abs(second[0]) < 0.01                 # starts from (near) silence
    assert max_step(second) <= steady * 1.05


def test_ending_in_silence_is_left_alone():
    p = Player()
    x = tone(0.05)
    x[-FADE * 2:] = 0                            # the word ended; then the buffer is empty
    p.push(x)
    out = play(p, 3)
    np.testing.assert_array_equal(out[:len(x)], x[:, 0])
    assert p.ran_out == [0.0]
    p.push(tone(0.02))
    np.testing.assert_allclose(play(p, 1)[:960], tone(0.02)[:, 0])   # no fade-in after a normal end


def test_trim_fades_out_instead_of_cutting():
    p = Player()
    p.push(tone(1.0))
    play(p, 10)
    p.trim(0)
    assert abs(p.buffered() - FADE / OUT_RATE) < 1e-9
    out = play(p, 2)
    assert max_step(out) <= 0.5 * 2 * np.pi * 300 / OUT_RATE * 1.05
    assert np.all(out[FADE:] == 0)


def test_trim_keeps_what_was_asked_and_fades_its_end():
    p = Player()
    for _ in range(10):
        p.push(tone(0.1))
    p.trim(0.25)
    assert abs(p.buffered() - 0.25) < 1e-9
    out = play(p, 15)
    assert abs(out[int(0.25 * OUT_RATE) - 1]) < 0.01


def test_trim_with_less_queued_than_kept_changes_nothing():
    p = Player()
    x = tone(0.1)
    p.push(x)
    p.trim(1.0)
    np.testing.assert_array_equal(play(p, 5)[:len(x)], x[:, 0])


def test_a_second_trim_right_after_new_sound_drops_it():
    """trim(0) leaves the last FADE samples fading out; sound pushed after them must go on the next trim."""
    p = Player()
    p.push(np.full((OUT_RATE, 2), 0.5, np.float32))
    p.pull(480)
    p.trim(0)
    p.push(np.full((OUT_RATE, 2), 0.5, np.float32))
    p.trim(0)
    assert p.buffered() * OUT_RATE <= FADE
