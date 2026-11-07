"""On the device: wake word model CPU and the second look's latency, without touching the running client.

Copy the branch's thinksmart_client/, models/ and this file to a scratch directory on the device
and run it with the client's python, giving 16 kHz mono wav files (played back to back with 2 s
of silence in between, as fast as the CPU allows):

    ~/thinksmart-client/.venv/bin/python device_bench.py [--verify hub | --verify asr:<dir> --threads 2] a.wav ...

Prints the model's CPU time per second of audio (one thread) and, per hit, how long the second
look took end to end (HTTP to the hub, recognition, answer) and what it decided.
"""
import argparse
import json
import pathlib
import resource
import sys
import time
import wave

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from thinksmart_client import kws as kws_mod, wake_verify  # noqa: E402
from thinksmart_client.config import DEFAULTS  # noqa: E402


class Clock:
    t = 0.0

    def monotonic(self):
        return self.t


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--verify', default='')
    p.add_argument('--threads', type=int, default=1, help='for --verify asr:<model dir>')
    p.add_argument('--gain', type=float, default=DEFAULTS['kws_gain_db'])
    p.add_argument('wavs', nargs='+')
    args = p.parse_args()
    parts = []
    for f in args.wavs:
        with wave.open(f) as w:
            parts += [np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768,
                      np.zeros(32000, np.float32)]
    x = np.concatenate(parts)
    clock = Clock()
    kws_mod.time = clock
    model = str(pathlib.Path(__file__).resolve().parent / 'models' / 'kws-zh')
    k = kws_mod.Kws(model, DEFAULTS['wake_words'], DEFAULTS['kws_score'], DEFAULTS['kws_threshold'],
                    DEFAULTS['kws_paths'], args.gain)
    if args.verify.startswith('asr:'):
        verifier = wake_verify.Verifier(wake_verify.LocalChecker(args.verify[4:], args.threads), DEFAULTS['wake_words'])
    else:
        verifier = wake_verify.make(args.verify, DEFAULTS['wake_words'], DEFAULTS['hub_http'])
    cpu = 0.0
    checks = []
    for i in range(0, len(x) - 319, 320):
        clock.t = (i + 320) / 16000
        t0 = time.process_time()
        hit = k.feed(x[i:i + 320])
        cpu += time.process_time() - t0
        if hit and verifier:
            t0 = time.perf_counter()
            r = verifier.check(k.recent(), hit)
            r['ms'] = round((time.perf_counter() - t0) * 1000)
            r['label'] = hit
            checks.append(r)
            print(json.dumps(r, ensure_ascii=False), flush=True)
        elif hit:
            print('hit', hit, flush=True)
    print(f'wake word model: {cpu:.1f} CPU s for {len(x) / 16000:.0f} s of audio '
          f'({100 * cpu / (len(x) / 16000):.0f}% of a core while there is sound)')
    if checks:
        ms = [c['ms'] for c in checks]
        print(f'second look: {len(ms)} checks, median {np.median(ms):.0f} ms, max {max(ms)} ms')
    print(f'peak memory of this process: {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024:.0f} MB')


if __name__ == '__main__':
    main()
