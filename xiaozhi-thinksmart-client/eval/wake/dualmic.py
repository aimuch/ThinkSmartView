"""Count wake word hits in the three stereo recordings: DMIC2 alone (what the client uses now),
DMIC1 alone, the two averaged, and averaged after aligning them by cross-correlation."""
import sys, glob, os
import numpy as np, soxr
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from thinksmart_client import kws as kws_mod
from thinksmart_client.config import DEFAULTS as C

class Clock:
    t = 0.0
    def monotonic(self): return self.t

def hits(x16):
    clock = Clock(); kws_mod.time = clock
    k = kws_mod.Kws(os.path.join(ROOT, 'models', 'kws-zh'),
                    C['wake_words'], C['kws_score'], C['kws_threshold'], C['kws_paths'], C['kws_gain_db'])
    n = 0
    for i in range(0, len(x16) - 320 + 1, 320):
        clock.t = (i + 320) / 16000
        if k.feed(x16[i:i + 320].astype(np.float32)): n += 1
    return n

def load(p):
    b = open(p, 'rb').read(); i = b.find(b'data')
    x = np.frombuffer(b[i + 8:], np.int16); x = x[:len(x) // 2 * 2].reshape(-1, 2).astype(np.float32) / 32768
    return x

def db(v): return 20 * np.log10(max(float(np.sqrt(np.mean(v ** 2))), 1e-9))

for p in sorted(glob.glob(sys.argv[1] + '/[123]-*.wav')):
    x = load(p); l, r = x[:, 0], x[:, 1]
    # lag of the user's voice between the mics: cross-correlate the loudest 10 s
    e = np.convolve(r ** 2, np.ones(4800), 'same'); c = int(np.argmax(e)); seg = slice(max(0, c - 240000), c + 240000)
    xc = np.correlate(l[seg][::1], r[seg][::1][:len(l[seg])], 'full') if False else None
    best, lag = -1, 0
    for d in range(-24, 25):
        a = l[seg][24 + d: len(l[seg]) - 24 + d]; b = r[seg][24: len(r[seg]) - 24]
        v = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12))
        if v > best: best, lag = v, d
    aligned = (np.roll(l, -lag) + r) / 2
    variants = {'DMIC2 (now)': r, 'DMIC1': l, 'average': (l + r) / 2, f'aligned avg (lag {lag})': aligned}
    print(f'== {os.path.basename(p)}  corr {best:.2f}  noise floor L {db(l[:48000])} R {db(r[:48000])}')
    for name, v in variants.items():
        v16 = soxr.resample(v, 48000, 16000)
        print(f'   {name:24s} hits {hits(v16):2d}')
