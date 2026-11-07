"""A small SFZ sampler: renders scores.py with the Salamander Grand Piano (CC-BY 3.0, Alexander Holm).
For each note: the region whose key and velocity range cover it, pitched from its keycenter by
resampling, a release fade at the note's end (a damper, with a little ring), a touch of room.

  uv run --no-project --with numpy --with scipy --with soxr python render.py <sfz> <out dir>
"""
import pathlib
import re
import sys
import wave
from functools import lru_cache

import numpy as np
import soxr
from scipy.signal import butter, lfilter

import importlib
import os

scores = importlib.import_module(os.environ.get('SCORES', 'scores'))

SR = 48000
GAP = 1.2          # silence after the piece: the wake word gets a clear moment each round


def parse_sfz(path):
    text = re.sub(r'//[^\n]*', '', path.read_text(errors='replace'))
    regions, group, current, defines = [], {}, None, {}
    for m in re.finditer(r'#define\s+(\$\w+)\s+(\S+)', text):
        defines[m.group(1)] = m.group(2)
    for k, v in defines.items():
        text = text.replace(k, v)
    tokens = re.split(r'(<\w+>)', text)
    for tok in tokens:
        tok = tok.strip()
        if not tok:
            continue
        if tok.startswith('<'):
            if current is not None:
                regions.append(current)
            current = None
            if tok in ('<group>', '<global>', '<master>'):
                group = {} if tok == '<group>' else group
                target = 'group'
            elif tok == '<region>':
                current = dict(group)
                target = 'region'
            else:
                target = None
            continue
        # opcode=value pairs; sample paths may contain spaces, so split before each "word="
        for m in re.finditer(r'(\w+)=(.*?)(?=\s+\w+=|$)', tok, re.S):
            key, val = m.group(1), m.group(2).strip()
            if target == 'group':
                group[key] = val
            elif target == 'region':
                current[key] = val
    if current is not None:
        regions.append(current)
    keep = []
    for r in regions:
        if r.get('trigger', 'attack') != 'attack' or 'sample' not in r:
            continue
        lo = int(r.get('lokey', r.get('key', 0)))
        hi = int(r.get('hikey', r.get('key', 127)))
        keep.append({'sample': path.parent / r['sample'].replace('\\', '/'), 'lokey': lo, 'hikey': hi,
                     'center': int(r.get('pitch_keycenter', r.get('key', lo))),
                     'lovel': int(r.get('lovel', 1)), 'hivel': int(r.get('hivel', 127)),
                     'tune': float(r.get('tune', 0)), 'volume': float(r.get('volume', 0))})
    return keep


@lru_cache(maxsize=None)
def load(path):
    with wave.open(str(path)) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
        x = x.reshape(-1, w.getnchannels())
        rate = w.getframerate()
    return soxr.resample(x, rate, SR) if rate != SR else x


class Piano:
    def __init__(self, sfz):
        self.regions = parse_sfz(pathlib.Path(sfz))
        assert self.regions, 'no regions'

    def note(self, key, vel, seconds):
        r = next((r for r in self.regions if r['lokey'] <= key <= r['hikey'] and r['lovel'] <= vel <= r['hivel']), None)
        if r is None:
            r = min(self.regions, key=lambda r: (abs(r['center'] - key), abs((r['lovel'] + r['hivel']) / 2 - vel)))
        x = load(r['sample'])
        ratio = 2 ** ((key - r['center'] + r['tune'] / 100) / 12)
        ring = 0.35 if key < 88 else 0.15                 # damper: the strings fade out after release
        need = int(SR * (seconds + ring) * ratio) + 64
        x = x[:need]
        if abs(ratio - 1) > 1e-6:
            x = soxr.resample(x, SR * ratio, SR)
        end = int(SR * seconds)
        rel = int(SR * ring)
        x = x[:end + rel].copy()
        if len(x) > end:
            x[end:] *= np.linspace(1, 0, len(x) - end)[:, None] ** 2
        gain = 0.27 + 0.73 * (vel / 127) ** 2          # the bank's amp_veltrack=73
        return x * gain * 10 ** (r['volume'] / 20)


def reverb(x, wet=0.12):
    y = np.zeros_like(x)
    for ch in range(x.shape[1]):
        acc = np.zeros(len(x))
        for d, g in ((1557, 0.82), (1617, 0.81), (1491, 0.83), (1422, 0.80)):
            d += 23 * ch                                    # a little different per side: width
            a = np.zeros(d + 1); a[0], a[d] = 1, -g
            acc += lfilter([1], a, x[:, ch])
        for d, g in ((225, 0.7), (556, 0.7)):
            b = np.zeros(d + 1); b[0], b[d] = -g, 1
            a = np.zeros(d + 1); a[0], a[d] = 1, -g
            acc = lfilter(b, a, acc)
        y[:, ch] = acc
    b, a = butter(2, 6000 / (SR / 2))
    return x + wet * lfilter(b, a, y, axis=0) / 4


def render(piano, piece):
    bpm, events, total_beats = piece()
    beat = 60 / bpm
    out = np.zeros((int(SR * (total_beats * beat + 1.5)), 2), np.float32)
    for b, key, beats, vel in events:
        x = piano.note(key, vel, beats * beat)
        s = int(SR * b * beat)
        out[s:s + len(x)] += x[:len(out) - s]
    out = reverb(out)
    # the device's small speaker: nothing below ~120 Hz, gently
    bb, aa = butter(2, 120 / (SR / 2), 'high')
    out = lfilter(bb, aa, out, axis=0)
    loud = np.abs(out).max(axis=1) > 0.02 * np.abs(out).max()
    out *= 10 ** (-18 / 20) / np.sqrt(np.mean(out[loud] ** 2))
    out = 0.8 * np.tanh(out / 0.8)
    f = int(SR * 0.3)
    out[-f:] *= np.linspace(1, 0, f)[:, None]
    return out


def write(path, x):
    pcm = (np.clip(x, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(2), w.setsampwidth(2), w.setframerate(SR), w.writeframes(pcm.tobytes())


if __name__ == '__main__':
    piano = Piano(sys.argv[1])
    out = pathlib.Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    only = sys.argv[3:]
    for name, fn in scores.PIECES:
        if only and not any(name.startswith(o) for o in only):
            continue
        x = render(piano, fn)
        cycle = np.concatenate([x, np.zeros((int(SR * GAP), 2), np.float32)])
        write(out / f'{name}.wav', np.concatenate([cycle, cycle]))
        print(f'{name}: {len(x) / SR:.1f} s')
