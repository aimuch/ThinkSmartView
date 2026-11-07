"""Wake word while an alarm rings: does "小麦小麦" stop it, also at full volume, and does the ring
tone wake it by itself?

Like echo.py (a user talking over a ring is the ring's echo plus their voice): the ring is recorded on
the device, the wake words are mixed in here, then the client's own linear echo canceller (speexdsp,
what the wake word hears while something plays) and wake word model run here.

  1. On the device, while a countdown rings at the alarm volume to test (devlock first):
       pw-record --target thinksmart_mic --rate 16000 --channels 1 --format s16 v70.mic.wav &
       pw-record --target <speaker sink>.monitor --rate 48000 --channels 2 --format s16 v70.far.wav &
     copy both to DATA/ring/
  2. uv run --no-project --with sherpa-onnx==2.0.1 --with numpy --with soxr python eval/wake/ring.py v70 v100
"""
import sys

import numpy as np

import echo
from common import DATA, RATE, read_wav

RING = DATA / 'ring'


def linear(near, far):
    from thinksmart_client.speex_aec import LinearAec
    a = echo.SPEEX['speex'][0]
    c = LinearAec(RATE, 320, a)
    y = np.zeros_like(near)
    for i in range(0, len(near) - 320 + 1, 320):
        y[i:i + 320] = c.process(near[i:i + 320], far[i:i + 320])
    return y


def ringing_part(far):
    """Where the ring plays in the recording (its reference is above -60 dBFS in 1 s windows)."""
    w = far[:len(far) // RATE * RATE].reshape(-1, RATE)
    loud = np.flatnonzero(np.sqrt((w ** 2).mean(1)) > 1e-3)
    return (loud[0] + 2, loud[-1]) if len(loud) > 4 else (0, len(w))   # 2 s for the canceller to settle


def run(name):
    import soxr
    mic = echo.read_any(RING / f'{name}.mic.wav')[0][:, 0]
    farx, far_rate = echo.read_any(RING / f'{name}.far.wav')
    far = soxr.resample(farx.mean(1), far_rate, RATE).astype(np.float32)
    n = min(len(mic), len(far))
    mic, far = mic[:n], far[:n]
    a, b = ringing_part(far)
    print(f'{name}: ring {a}-{b} s of {n / RATE:.0f} s; mic {echo.db(mic[a * RATE:b * RATE]):.1f} dBFS')
    lin = linear(mic, far)
    for label, x in (('raw mic', mic), ('linear AEC', lin)):
        hits = echo.detect(x[a * RATE:b * RATE])
        print(f'  ring alone, {label:10s}: {echo.db(x[a * RATE:b * RATE]):6.1f} dBFS, wake word hits {len(hits)}'
              + (f' at {[round(h + a, 1) for h in hits]} s' if hits else ''))
    near_echo, far_echo = mic[a * RATE:b * RATE], far[a * RATE:b * RATE]
    files = echo.clips()
    for level, dbfs in echo.LEVELS.items():
        voice, marks, t = [np.zeros(RATE)], [], 1.0
        for f in files:
            x = read_wav(f)
            x = x * (10 ** (dbfs / 20) / echo.active_rms(x))
            marks.append({'start': t, 'end': t + len(x) / RATE})
            voice += [x, np.zeros(int(echo.GAP * RATE))]
            t += len(x) / RATE + echo.GAP
        voice = np.concatenate(voice)
        reps = len(voice) // len(near_echo) + 1
        near = np.tile(near_echo, reps)[:len(voice)] + voice
        y = linear(near, np.tile(far_echo, reps)[:len(voice)])
        hits = echo.detect(y)
        found = sum(any(m['start'] < h <= m['end'] + 1.5 for h in hits) for m in marks)
        other = sum(not any(m['start'] < h <= m['end'] + 1.5 for m in marks) for h in hits)
        print(f'  wake words at {dbfs} dBFS ({level}): woke {found}/{len(marks)} '
              f'({100 * (1 - found / len(marks)):.0f}% missed), other hits {other}')


if __name__ == '__main__':
    for name in sys.argv[1:]:
        run(name)
