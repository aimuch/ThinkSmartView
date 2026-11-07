"""Whole-utterance vs segmented-at-pauses decoding with X-ASR: accuracy and how much is left after the end.

  python seg.py <model dir> <hotwords> <wav dir> <sents.txt> <pause ms>
"""
import sys, glob, os, re, time, wave
import numpy as np
import sherpa_onnx

M, HOT, WAVS, SENTS, PAUSE = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5])
rec = sherpa_onnx.OfflineRecognizer.from_transducer(
    encoder=f'{M}/encoder-epoch-99-avg-1.int8.onnx', decoder=f'{M}/decoder-epoch-99-avg-1.onnx',
    joiner=f'{M}/joiner-epoch-99-avg-1.int8.onnx', tokens=f'{M}/tokens.txt', num_threads=4,
    decoding_method='modified_beam_search', hotwords_file=HOT, hotwords_score=1.5, modeling_unit='bpe',
    bpe_vocab=f'{M}/bpe.vocab')
refs = [l.strip() for l in open(SENTS)]
PUNCT = re.compile(r'[\s，。？！、,.?!；;：:“”"]')


def norm(t):
    return PUNCT.sub('', t).lower()


def cer(ref, hyp):
    r, h = norm(ref), norm(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
    return d[len(h)], len(r)


def dec(x):
    s = rec.create_stream()
    s.accept_waveform(16000, x)
    t = time.time()
    rec.decode_stream(s)
    return s.result.text, time.time() - t


def segments(x):
    """Cut where a pause reaches PAUSE ms (20 ms frames under -45 dBFS), like a VAD would during speech."""
    fr = x[:len(x) // 320 * 320].reshape(-1, 320)
    quiet = 20 * np.log10(np.sqrt((fr ** 2).mean(1)) + 1e-9) < -45
    cuts, run = [], 0
    for i, q in enumerate(quiet):
        run = run + 1 if q else 0
        if run * 20 >= PAUSE > (run - 1) * 20:
            cuts.append((i + 1) * 320)
    bounds = [0] + cuts + [len(x)]
    return [x[a:b] for a, b in zip(bounds, bounds[1:]) if b - a > 320 * 10]


def join(parts):
    out = ''
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if out:
            out = out.rstrip('。') + ('' if out[-1:] in '，？！,?!' else '，')
        out += p
    return out


pad = lambda x, a, b: np.concatenate([np.zeros(int(a * 16000), np.float32), x, np.zeros(int(b * 16000), np.float32)])
tot = {'whole': [0, 0], 'seg': [0, 0]}
left = []
for f in sorted(glob.glob(f'{WAVS}/*.wav'), key=lambda p: (int(os.path.basename(p).split('-')[0]), p)):
    with wave.open(f) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), '<i2').astype(np.float32) / 32768
    loud = np.nonzero(np.abs(x) > 0.02)[0]
    if not len(loud):
        continue
    x = x[loud[0]:loud[-1] + 1]
    ref = refs[int(os.path.basename(f).split('-')[0]) - 1]
    whole, tw = dec(pad(x, 0.2, 0.3))
    segs = segments(x)
    texts = [dec(pad(s, 0.2 if i == 0 else 0, 0.3 if i == len(segs) - 1 else 0))[0] for i, s in enumerate(segs)]
    seg = join(texts)
    for k, h in (('whole', whole), ('seg', seg)):
        e, n = cer(ref, h)
        tot[k][0] += e
        tot[k][1] += n
    left.append(len(segs[-1]) / len(x))
    flag = '' if norm(whole) == norm(seg) else '  <-- differs'
    print(f'{os.path.basename(f)}\t{len(x) / 16000:.1f}s\t{len(segs)} segs, last {len(segs[-1]) / 16000:.1f}s\n  whole: {whole}\n  seg:   {seg}{flag}')
print(f"\npause {PAUSE} ms: CER whole {tot['whole'][0]}/{tot['whole'][1]} = {tot['whole'][0] / tot['whole'][1]:.2%}, "
      f"segmented {tot['seg'][0]}/{tot['seg'][1]} = {tot['seg'][0] / tot['seg'][1]:.2%}; "
      f"last segment is {np.mean(left):.0%} of the audio on average")
