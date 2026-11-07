"""Wake word while the device is talking (interrupting a reply): how much does the echo, and the echo
cancellation in front of the wake word model, cost?

Sound is linear, so a user talking over a reply is the reply's echo at the microphone plus their voice:
the echo is recorded once on the device (echo_record.sh), the wake words are mixed in here, the echo
canceller runs on the device (its library is linux-arm64 only) and the wake word model here.

  1. eval/wake/echo_record.sh                       # device plays a reply: mic + speaker -> DATA/echo/
  2. uv run --no-project --with numpy --with soxr python eval/wake/echo.py mix
  3. eval/wake/echo.py aec  (runs on the device via ts.sh, see main() for the variants)
  4. uv run --no-project --with sherpa-onnx==2.0.1 --with numpy --with soxr python eval/wake/echo.py score
"""
import argparse
import json
import pathlib
import subprocess
import sys
import time
import wave

import numpy as np

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent))
from common import CLIENT, DATA, RATE, read_wav, sentences, write_wav  # noqa: E402

ECHO = DATA / 'echo'
REPLY = (12, 62)          # seconds of echo-only reply in the recording (see echo_record.sh)
GAP = 2.4                 # silence after each wake word (> the detector's 2 s refractory time)
LEVELS = {'near': -30, 'mid': -40}
ECHO_LESS = (10, 20)      # dB   # voice level at the mic (run.py: 1 m is about -30 dBFS)
WORDS = ('p1', 'p2', 'p3')
TS = CLIENT.parent.parent.parent / 'thinksmart-ubuntu-image' / 'ts.sh'
if not TS.exists():
    TS = CLIENT.parent / 'thinksmart-ubuntu-image' / 'ts.sh'


def read_any(path):
    with wave.open(str(path)) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
        return x.reshape(-1, w.getnchannels()), w.getframerate()


def active_rms(x):
    frames = x[:len(x) // 400 * 400].reshape(-1, 400)
    e = (frames ** 2).mean(1)
    voiced = e[e > e.max() * 0.01]
    return float(np.sqrt(voiced.mean())) if len(voiced) else 1e-9


def clips():
    want = {s.id for s in sentences() if s.id in WORDS}
    return sorted(f for f in (DATA / 'tts').glob('*~norm.wav') if f.name.split('~')[0] in want)


def mix():
    """near/far pairs: the wake words over the reply's echo ('echo') and over the quiet room ('quiet')."""
    import soxr
    mic, _ = read_any(ECHO / 'mic.wav')
    far, far_rate = read_any(ECHO / 'far.wav')
    mic = mic[:, 0]
    # the canceller works at 16 kHz anyway (the client resamples its 48 kHz reference): 16 kHz mono
    # keeps what is copied to the device small
    far = soxr.resample(far.mean(1), far_rate, RATE).astype(np.float32)
    a, b = REPLY
    echo_near = mic[a * RATE:b * RATE]
    echo_far = far[a * RATE:b * RATE]
    quiet = mic[:int(8 * RATE)]                 # room before the reply started
    files = clips()
    out = ECHO / 'mix'
    out.mkdir(parents=True, exist_ok=True)
    for level_name, db in LEVELS.items():
        voice, marks, t = [], [], 1.0
        voice.append(np.zeros(RATE))
        for f in files:
            x = read_wav(f)
            x = x * (10 ** (db / 20) / active_rms(x))
            marks.append({'start': t, 'end': t + len(x) / RATE, 'file': f.name})
            voice += [x, np.zeros(int(GAP * RATE))]
            t += len(x) / RATE + GAP
        voice = np.concatenate(voice)
        n = len(voice)
        reps = n // len(echo_near) + 1
        near_echo = np.tile(echo_near, reps)[:n]
        far_echo = np.tile(echo_far, reps)[:n]
        near_quiet = np.tile(quiet, n // len(quiet) + 1)[:n]
        conds = [('echo', near_echo, far_echo), ('quiet', near_quiet, np.zeros_like(far_echo))]
        # less echo at the mic for the same reference: what two microphones nulling the speaker
        # (about -10 dB, see the second-mic notes) or a quieter speaker could gain at most
        conds += [(f'echo{d}', near_quiet + near_echo * 10 ** (-d / 20), far_echo)
                  for d in ECHO_LESS]
        for cond, near, farx in conds:
            tag = f'{cond}-{level_name}'
            write_wav(out / f'{tag}.wav', near + voice)
            write_wav(out / f'{tag}.far.wav', farx)
            (out / f'{tag}.json').write_text(json.dumps(marks))
        print(level_name, f'{n / RATE:.0f} s, {len(marks)} wake words')
    # the echo alone, for how much the canceller removes
    write_wav(out / 'echoonly.wav', echo_near)
    write_wav(out / 'echoonly.far.wav', echo_far)


# ---- on the device ----

VARIANTS = {
    # name: (noise suppression, far reference shift in ms: >0 feeds it earlier, like a longer output latency)
    'client': (True, 0),
    'nons': (False, 0),
    'late150': (True, 150),
    'late300': (True, 300),
    'mobile': (True, 0),      # AECM instead of AEC3
}


def aec_run(near_path, far_path, out_path, ns, shift_ms, mobile=False):
    """The client's Aec, fed like the client does: one 20 ms reference block, then one mic block."""
    from thinksmart_client import aec as aec_mod, webrtc_apm
    a = aec_mod.Aec(RATE, RATE)
    if not ns or mobile:
        c = webrtc_apm.create_default_config()
        c.echo.enabled = True
        c.echo.mobile_mode = mobile
        c.high_pass.enabled = True
        c.noise_suppress.enabled = ns
        assert a._apm.apply_config(c) == 0
    near, far = read_wav(near_path), read_wav(far_path)
    shift = int(shift_ms * RATE / 1000)
    far = np.concatenate([far[shift:], np.zeros(shift, np.float32)])
    out = np.zeros_like(near)
    for i in range(0, len(near) - 320 + 1, 320):
        a.feed_far(far[i:i + 320])
        out[i:i + 320] = a.process_near(near[i:i + 320])
    write_wav(out_path, out)


def aec_device(variants, only=None):
    """Copy the mixes to the device, run the client's own echo canceller there, copy the results back."""
    mixdir = ECHO / 'mix'
    names = sorted(p.stem for p in mixdir.glob('*.wav') if not p.stem.endswith('.far') and (mixdir / f'{p.stem}.far.wav').exists()
                   and (not only or any(p.stem.startswith(o) for o in only)))
    tar = subprocess.run(['tar', 'cf', '-', '-C', str(mixdir), *[f'{n}{e}' for n in names for e in ('.wav', '.far.wav')],
                          '-C', str(HERE), 'echo.py', 'common.py', 'sentences.tsv'], capture_output=True, check=True).stdout
    jobs = ' '.join(f'{n}:{v}' for n in names for v in variants)
    cmd = (f'rm -rf /tmp/echo && mkdir -p /tmp/echo/out && cd /tmp/echo && tar xf - 2>/dev/null && '
           f'for j in {jobs}; do n=${{j%%:*}}; v=${{j##*:}}; '
           f'PYTHONPATH=$HOME/thinksmart-client nice $HOME/thinksmart-client/.venv/bin/python echo.py aec-one $n $v || exit 1; done; '
           f'tar cf - -C out .')
    res = subprocess.run([str(TS), cmd], input=tar, capture_output=True, check=True)
    out = ECHO / 'out'
    out.mkdir(exist_ok=True)
    subprocess.run(['tar', 'xf', '-', '-C', str(out)], input=res.stdout, check=True)
    subprocess.run([str(TS), 'rm -rf /tmp/echo'], check=True)


# ---- linear-only cancellation (speexdsp, runs here too) ----

SPEEX = {'speex': (500, 0), 'speex300': (300, 0), 'speex-late150': (500, 150)}   # name: (tail ms, shift ms)


def speex_all(only=None):
    from thinksmart_client.speex_aec import LinearAec
    mixdir, out = ECHO / 'mix', ECHO / 'out'
    out.mkdir(exist_ok=True)
    for p in sorted(mixdir.glob('*.far.wav')):
        name = p.name[:-len('.far.wav')]
        if only and not any(name.startswith(o) for o in only):
            continue
        near, far = read_wav(mixdir / f'{name}.wav'), read_wav(p)
        for v, (tail, shift_ms) in SPEEX.items():
            shift = int(shift_ms * RATE / 1000)
            f = np.concatenate([far[shift:], np.zeros(shift, np.float32)])
            a = LinearAec(RATE, 320, tail)
            y = np.zeros_like(near)
            for i in range(0, len(near) - 320 + 1, 320):
                y[i:i + 320] = a.process(near[i:i + 320], f[i:i + 320])
            write_wav(out / f'{name}-{v}.wav', y)
        print(name, 'done')


# ---- scoring (Mac) ----

class Clock:
    t = 0.0

    def monotonic(self):
        return self.t


TUNE = {}   # overrides of the client's kws_* settings (--threshold, --score, --gain)


def detect(x):
    from thinksmart_client import config, kws as kws_mod
    clock = Clock()
    kws_mod.time = clock
    d = {**config.DEFAULTS, **TUNE}
    k = kws_mod.Kws(str(CLIENT / 'models' / 'kws-zh'), d['wake_words'], d['kws_score'], d['kws_threshold'],
                    d['kws_paths'], d['kws_gain_db'])
    hits = []
    for i in range(0, len(x) - 320 + 1, 320):
        clock.t = (i + 320) / RATE
        if k.feed(x[i:i + 320]):
            hits.append(clock.t)
    return hits


def score_one(path, marks):
    hits = detect(read_wav(path))
    found = sum(any(m['start'] < h <= m['end'] + 1.5 for h in hits) for m in marks)
    false = sum(not any(m['start'] < h <= m['end'] + 1.5 for m in marks) for h in hits)
    return found, false


def db(x):
    return 20 * np.log10(max(float(np.sqrt(np.mean(x ** 2))), 1e-9))


def score():
    out, mixdir = ECHO / 'out', ECHO / 'mix'
    rows = []
    for level in LEVELS:
        for cond in ('quiet', 'echo', *[f'echo{d}' for d in ECHO_LESS]):
            tag = f'{cond}-{level}'
            if not (mixdir / f'{tag}.json').exists():
                continue
            marks = json.loads((mixdir / f'{tag}.json').read_text())
            variants = [('raw (no AEC)', mixdir / f'{tag}.wav')] + \
                [(v, out / f'{tag}-{v}.wav') for v in [*VARIANTS, *SPEEX] if (out / f'{tag}-{v}.wav').exists()]
            for v, p in variants:
                found, false = score_one(p, marks)
                rows.append((tag, v, found, len(marks), false))
                print(f'{tag:12s} {v:14s} woke {found:3d}/{len(marks)}  ({100 * (1 - found / len(marks)):4.0f}% missed)  other hits {false}')
    eo = mixdir / 'echoonly.wav'
    if eo.exists():
        print(f'echo alone: mic {db(read_wav(eo)[5 * RATE:]):.1f} dBFS', end='')
        for v in [*VARIANTS, *SPEEX]:
            p = out / f'echoonly-{v}.wav'
            if p.exists():
                hits = detect(read_wav(p))
                print(f', {v} {db(read_wav(p)[5 * RATE:]):.1f} ({len(hits)} wakes)', end='')
        print(' (after the canceller has settled)')
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('step', choices=['mix', 'aec', 'aec-one', 'speex', 'score'])
    ap.add_argument('rest', nargs='*')
    ap.add_argument('--variants', default=','.join(VARIANTS))
    ap.add_argument('--only', help='mixes whose names start with these (comma-separated), for aec')
    ap.add_argument('--threshold', type=float)
    ap.add_argument('--score', type=float)
    ap.add_argument('--gain', type=float)
    a = ap.parse_args()
    for key, v in (('kws_threshold', a.threshold), ('kws_score', a.score), ('kws_gain_db', a.gain)):
        if v is not None:
            TUNE[key] = v
    if a.step == 'mix':
        mix()
    elif a.step == 'aec':
        t0 = time.time()
        aec_device(a.variants.split(','), a.only.split(',') if a.only else None)
        print(f'echo cancellation on the device: {time.time() - t0:.0f} s')
    elif a.step == 'speex':
        speex_all(a.only.split(',') if a.only else None)
    elif a.step == 'aec-one':                      # on the device, called by aec
        name, variant = a.rest
        ns, shift = VARIANTS[variant]
        aec_run(f'{name}.wav', f'{name}.far.wav', f'out/{name}-{variant}.wav', ns, shift, variant == 'mobile')
    else:
        score()


if __name__ == '__main__':
    main()
