"""Wake word evaluation: miss rate per condition and false wakes per hour, using the client's own code.

Every set is played as one continuous stream through thinksmart_client.kws.Kws (same batching,
quiet gate and refractory period as on the device, with a clock that follows the audio), so the
numbers are what the device would do with this audio. With --verify, every first-stage hit also
goes through the second stage (thinksmart_client.wake_verify) before it counts.

Conditions (speech level = RMS of the voiced part; the device's own room noise, -57 dBFS, is
always underneath):
  loud / mid / low   -20 / -40 / -48 dBFS, no reverb (how much the level alone matters)
  near   -30 dBFS, about an arm's length
  far    -42 dBFS with room reverb, across the room
  soft   -48 dBFS with reverb, far and quiet: just above the quiet gate (-50 dBFS)
  tv     -30 dBFS while a TV/radio talks at -36 dBFS (other sentences, other voices, reverb)
  music  -30 dBFS over music at -36 dBFS (only when $WAKE_EVAL_DATA/bg/music*.wav exist)
Negatives: conf (sentences that sound like a wake word) and talk (everyday speech, TV lines),
all voices, played back to back as TV/room speech at -33 dBFS with reverb -> false wakes per hour.
Real recordings from the device's microphone ($WAKE_EVAL_DATA/real, record_real.sh), played as
they are: pos-<what>-n<count>.wav (the wake word said <count> times) -> wakes / count;
neg-<what>.wav (TV, talking, music) -> false wakes per hour.

    uv run --no-project --with sherpa-onnx==2.0.1 --with numpy python eval/wake/run.py [--verify asr]
"""
import argparse
import collections
import json
import multiprocessing
import pathlib
import re
import sys
import time

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from common import CLIENT, DATA, RATE, read_wav, sentences  # noqa: E402

sys.path.insert(0, str(CLIENT))

NOISE_DB = -57
POS_CONDITIONS = {'loud': (-20, None, None), 'mid': (-40, None, None), 'low': (-48, None, None),
                  'near': (-30, None, None), 'far': (-42, 'room', None), 'soft': (-48, 'room', None),
                  'tv': (-30, None, ('talk', -36)), 'music': (-30, None, ('music', -36)),
                  'program': (-30, None, ('program', -36))}


def program_files(kind):
    """Downloaded TV / podcast ('program') or music audio in $WAKE_EVAL_DATA/bg (see README)."""
    files = [f for f in sorted((DATA / 'bg').glob('*.wav')) if f.name != 'device-ambient.wav']
    return [f for f in files if f.name.startswith('music') == (kind == 'music')]


def at_rms(x, db):
    """Long recordings: whole-file RMS (at_level looks at the loudest parts, fine for one sentence)."""
    return x * (10 ** (db / 20) / max(float(np.sqrt(np.mean(x ** 2))), 1e-9))


# ---- audio helpers ----

def active_rms(x):
    frames = x[:len(x) // 400 * 400].reshape(-1, 400)
    e = (frames ** 2).mean(1)
    voiced = e[e > e.max() * 0.01]       # within 20 dB of the loudest 25 ms
    return float(np.sqrt(voiced.mean())) if len(voiced) else 1e-9


def at_level(x, db):
    return x * (10 ** (db / 20) / active_rms(x))


def room_ir(seed, rt60=0.5, drr_db=-3):
    """Direct sound plus an exponentially decaying diffuse tail (drr_db: direct-to-reverb ratio)."""
    rng = np.random.default_rng(seed)
    n = int(rt60 * RATE)
    t = np.arange(n) / RATE
    tail = rng.normal(size=n) * np.exp(-6.9 * t / rt60)
    tail[:int(0.004 * RATE)] = 0          # first reflections arrive a few ms later
    tail *= np.sqrt(10 ** (-drr_db / 10) / (tail ** 2).sum())
    tail[0] = 1.0
    return tail


def reverb(x, seed):
    return np.convolve(x, room_ir(seed))[:len(x) + RATE // 4]


def noise_floor(n, seed):
    amb = DATA / 'bg' / 'device-ambient.wav'
    rng = np.random.default_rng(seed)
    if amb.exists():
        a = read_wav(amb)[RATE:]           # skip pw-record's start-up
        a = a - a.mean()
        start = rng.integers(0, len(a))
        a = np.tile(np.roll(a, -start), n // len(a) + 1)[:n]
        return a * (10 ** (NOISE_DB / 20) / np.sqrt((a ** 2).mean()))
    return rng.normal(0, 10 ** (NOISE_DB / 20), n)


def clips(kind):
    """{sentence: [paths]} for one kind."""
    out = collections.defaultdict(list)
    meta = {s.id: s for s in sentences() if s.kind == kind}
    for f in sorted((DATA / 'tts').glob('*.wav')):
        sid = f.name.split('~')[0]
        if sid in meta:
            out[meta[sid]].append(f)
    return out


def background(kind, n, db, seed):
    rng = np.random.default_rng(seed)
    if kind in ('music', 'program'):
        parts = [at_rms(read_wav(f), db) for f in program_files(kind)]
    else:
        files = [f for fs in clips('talk').values() for f in fs]
        rng.shuffle(files)
        parts = []
        for f in files[:400]:
            parts += [at_level(reverb(read_wav(f), rng.integers(1 << 30)), db), np.zeros(int(RATE * 0.3))]
    b = np.concatenate(parts)
    b = np.tile(b, n // len(b) + 1)
    start = rng.integers(0, len(b) - n)
    return b[start:start + n]


# ---- running the client's detector over a stream ----

class Clock:
    t = 0.0

    def monotonic(self):
        return self.t


def run_stream(x, args):
    """Feed x through Kws (and the verifier) in 20 ms blocks; returns [(time, label, verdict)]."""
    from thinksmart_client import kws as kws_mod
    clock = Clock()
    kws_mod.time = clock
    model = str(CLIENT / 'models' / 'kws-zh')
    if args.hangover:
        kws_mod.HANGOVER = args.hangover
    k = kws_mod.Kws(model, json.loads(args.words), args.score, args.threshold, args.paths, args.gain)
    verifier = make_verifier(args)
    hits = []
    block = 320
    for i in range(0, len(x) - block + 1, block):
        clock.t = (i + block) / RATE
        hit = k.feed(x[i:i + block].astype(np.float32))
        if hit:
            verdict = None
            if verifier:
                t0 = time.perf_counter()
                window = k.recent()
                verdict = verifier.check(window, hit)
                verdict['ms'] = (time.perf_counter() - t0) * 1000
                if args.dump and not verdict['ok']:
                    from common import write_wav
                    pathlib.Path(args.dump).mkdir(parents=True, exist_ok=True)
                    write_wav(f'{args.dump}/{args.tag}-{clock.t:.2f}.wav', window)
            hits.append((clock.t, hit, verdict))
    return hits


def make_verifier(args):
    if not args.verify:
        return None
    from thinksmart_client import wake_verify
    return wake_verify.make(args.verify, json.loads(args.words))


def accepted(h):
    return h[2] is None or h[2]['ok']


# ---- sets ----

def pos_job(job):
    cond, args = job
    level, room, bg = POS_CONDITIONS[cond]
    args.tag = cond
    rng = np.random.default_rng(hash(cond) % (1 << 30))
    parts, spans, pos = [np.zeros(RATE)], [], RATE
    for s, files in clips('pos').items():
        for f in files:
            x = read_wav(f)
            if room:
                x = reverb(x, rng.integers(1 << 30))
            x = at_level(x, level)
            spans.append((s, f.name, pos / RATE, (pos + len(x)) / RATE))
            gap = np.zeros(int(RATE * 2.5))     # longer than the refractory period
            parts += [x, gap]
            pos += len(x) + len(gap)
    x = np.concatenate(parts)
    x = x + noise_floor(len(x), 1)
    if bg:
        x = x + background(bg[0], len(x), bg[1], 2)
    hits = run_stream(x, args)
    rows = []
    for s, name, a, b in spans:
        hs = [h for h in hits if a <= h[0] <= b + 1.0]
        rows.append({'cond': cond, 'id': s.id, 'label': s.label, 'clip': name,
                     'stage1': bool(hs), 'woke': any(accepted(h) for h in hs),
                     'verdicts': [h[2] for h in hs if h[2]]})
    return rows


def neg_job(job):
    """One slice of the negatives: everyday speech heard from a TV / across the room."""
    files, seed, args = job
    args.tag = f'neg{seed}'
    rng = np.random.default_rng(seed)
    parts, spans, pos = [], [], 0
    for f in files:
        x = at_level(reverb(read_wav(f), rng.integers(1 << 30)), -33)
        gap = np.zeros(int(RATE * rng.uniform(0.2, 1.5)))
        spans.append((f.name, pos / RATE, (pos + len(x)) / RATE))
        parts += [x, gap]
        pos += len(x) + len(gap)
    x = np.concatenate(parts)
    x = x + noise_floor(len(x), seed)
    hits = run_stream(x, args)
    out = []
    clip_list = [{'name': n, 'seconds': b - a} for n, a, b in spans]
    for t, label, verdict in hits:
        clip = next((n for n, a, b in spans if a <= t <= b + 1.0), '?')
        out.append({'t': t, 'label': label, 'clip': clip, 'woke': accepted((t, label, verdict)),
                    'verdict': verdict})
    return {'seconds': len(x) / RATE, 'clips': clip_list, 'hits': out}


def program_job(job):
    """A whole TV programme / podcast / music recording as heard across the room: false wakes."""
    path, args = job
    args.tag = path.stem
    x = at_rms(read_wav(path), -33)
    x = x + noise_floor(len(x), 3)
    hits = run_stream(x, args)
    return {'file': path.name, 'seconds': len(x) / RATE, 'stage1': len(hits),
            'woke': sum(accepted(h) for h in hits),
            'hits': [{'t': round(h[0], 1), 'label': h[1], 'woke': accepted(h), 'verdict': h[2]} for h in hits]}


def real_job(job):
    path, args = job
    args.tag = path.stem
    x = read_wav(path)
    hits = run_stream(x, args)
    return {'file': path.name, 'seconds': len(x) / RATE, 'stage1': len(hits),
            'woke': sum(accepted(h) for h in hits), 'verdicts': [h[2] for h in hits if h[2]],
            'times': [round(h[0], 1) for h in hits if accepted(h)]}


def main():
    p = argparse.ArgumentParser()
    from thinksmart_client.config import DEFAULTS
    # defaults: the client's own config, so a bare run measures what ships
    p.add_argument('--score', type=float, default=DEFAULTS['kws_score'])
    p.add_argument('--threshold', type=float, default=DEFAULTS['kws_threshold'])
    p.add_argument('--paths', type=int, default=DEFAULTS['kws_paths'])
    p.add_argument('--words', default=json.dumps(DEFAULTS['wake_words'], ensure_ascii=False), help='JSON list')
    p.add_argument('--verify', default='', help="second stage, 'asr:<model dir>' (the client's 'hub' needs the device)")
    p.add_argument('--gain', type=float, default=DEFAULTS['kws_gain_db'], help='dB in front of the wake word model')
    p.add_argument('--hangover', type=int, default=0, help='override kws.HANGOVER (batches)')
    p.add_argument('--only', default='', help='comma list of conditions / neg / real / programs')
    p.add_argument('--out', default='', help='write all rows as JSON here')
    p.add_argument('--dump', default='', help='save the audio of hits the second stage rejected here')
    args = p.parse_args()
    conds = [c for c in POS_CONDITIONS if c not in ('music', 'program') or program_files(c)]
    want = set(args.only.split(',')) if args.only else set(conds) | {'neg'}
    negs = [f for kind in ('conf', 'talk') for fs in clips(kind).values() for f in fs]
    rng = np.random.default_rng(0)
    rng.shuffle(negs)
    slices = [(list(negs[i::8]), i, args) for i in range(8)] if 'neg' in want else []
    reals = sorted((DATA / 'real').glob('*.wav')) if 'real' in want or not args.only else []
    programs = program_files('program') + program_files('music') if 'programs' in want or not args.only else []
    t0 = time.time()
    with multiprocessing.Pool(8) as pool:
        pos_res = pool.map_async(pos_job, [(c, args) for c in conds if c in want])
        neg_res = pool.map_async(neg_job, slices)
        real_res = pool.map_async(real_job, [(f, args) for f in reals])
        prog_res = pool.map_async(program_job, [(f, args) for f in programs])
        pos_rows = [r for rows in pos_res.get() for r in rows]
        neg_parts = neg_res.get()
        real_rows = real_res.get()
        prog_rows = prog_res.get()
    report(args, pos_rows, neg_parts, time.time() - t0)
    if prog_rows:
        hours = sum(r['seconds'] for r in prog_rows) / 3600
        stage1 = sum(r['stage1'] for r in prog_rows)
        woke = sum(r['woke'] for r in prog_rows)
        print(f'downloaded programmes: {hours:.2f} h; first stage {stage1} hits, false wakes {woke} '
              f'= {woke / hours:.1f} per hour')
        for r in prog_rows:
            print(f'   {r["file"]:<14} {r["seconds"] / 60:5.0f} min  first stage {r["stage1"]}, woke {r["woke"]}')
            for h in r['hits']:
                text = (h['verdict'] or {}).get('text')
                print(f'      {"WOKE   " if h["woke"] else "blocked"} {h["t"]:7.1f} s {h["label"]:<6} {text or ""}')
    for r in real_rows:
        n = re.search(r'-n(\d+)$', r['file'][:-4])
        what = f'{r["woke"]}/{n.group(1)} woke' if n else f'{r["woke"] / (r["seconds"] / 3600):.1f} false wakes per hour'
        print(f'real {r["file"]:<40} {r["seconds"]:7.0f} s  first stage {r["stage1"]}, {what}  at {r["times"]}')
        for v in r['verdicts']:
            print(f'      {v}')
    if args.out:
        pathlib.Path(args.out).write_text(json.dumps({'args': vars(args), 'pos': pos_rows, 'neg': neg_parts,
                                                      'real': real_rows, 'programs': prog_rows},
                                                     ensure_ascii=False, indent=1, default=str))


def report(args, pos_rows, neg_parts, took):
    print(f'score {args.score} threshold {args.threshold} paths {args.paths} gain {args.gain} '
          f'hangover {args.hangover or "default"} '
          f'verify {args.verify or "-"}'
          f'  ({took:.0f} s)')
    if pos_rows:
        ids = sorted({r['id'] for r in pos_rows})
        conds = list(dict.fromkeys(r['cond'] for r in pos_rows))
        print('miss rate (missed/clips)   ' + '  '.join(f'{c:>12}' for c in conds))
        for sid in ids + ['all']:
            cells = []
            for c in conds:
                rs = [r for r in pos_rows if r['cond'] == c and (sid == 'all' or r['id'] == sid)]
                miss = sum(not r['woke'] for r in rs)
                cells.append(f'{miss:>4}/{len(rs):<3} {100 * miss / len(rs):3.0f}%')
            name = sid if sid == 'all' else f'{sid} {next(r["label"] for r in pos_rows if r["id"] == sid)}'
            print(f'{name:<26} ' + '  '.join(f'{c:>12}' for c in cells))
        if args.verify:
            rejected = sum(r['stage1'] and not r['woke'] for r in pos_rows)
            print(f'second stage rejected {rejected} true wakes the first stage had caught')
    if neg_parts:
        for kind, what in (('c', 'confusable sentences'), ('t', 'everyday speech / TV')):
            seconds = sum(c['seconds'] for p in neg_parts for c in p['clips'] if c['name'][0] == kind)
            n = sum(1 for p in neg_parts for c in p['clips'] if c['name'][0] == kind)
            hits = [h for p in neg_parts for h in p['hits'] if h['clip'][0] == kind]
            woke = [h for h in hits if h['woke']]
            print(f'{what}: {n} sentences, {seconds / 3600:.2f} h; first stage {len(hits)} hits, '
                  f'false wakes {len(woke)} = {100 * len(woke) / n:.1f}% of sentences, '
                  f'{len(woke) / (seconds / 3600):.1f} per hour')
        hits = [h for p in neg_parts for h in p['hits']]
        for h in sorted(hits, key=lambda h: h['clip']):
            text = (h['verdict'] or {}).get('text')
            print(f'   {"WOKE   " if h["woke"] else "blocked"} {h["label"]:<6} {h["clip"]:<42} {text or ""}')
    ms = [v['ms'] for r in pos_rows for v in r['verdicts']] + \
         [h['verdict']['ms'] for p in neg_parts for h in p['hits'] if h['verdict']]
    if ms:
        print(f'second stage time on this Mac: median {np.median(ms):.0f} ms, max {max(ms):.0f} ms')


if __name__ == '__main__':
    main()
