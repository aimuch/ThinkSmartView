"""X-ASR decode time on the device under different settings (run with the hub stopped: memory).

  ~/xiaozhi-hub-server/main/xiaozhi-server/.venv/bin/python asrbench.py <config> files...
config: beam2 | greedy2 | beam4 | greedy4 | beam1 ...
"""
import sys, time, wave, glob, os
import numpy as np
import sherpa_onnx

D = '/home/ubuntu/xiaozhi-hub-server/main/xiaozhi-server/models/sherpa-onnx-x-asr-zipformer-transducer-zh-en-punct-int8-2026-06-03'
HOT = sorted(glob.glob('/home/ubuntu/xiaozhi-hub-server/main/xiaozhi-server/tmp/hotwords-a09e88f559.txt'))[0]
cfg = sys.argv[1]
threads = int(cfg[-1])
beam = cfg.startswith('beam')
kw = dict(encoder=f'{D}/encoder-epoch-99-avg-1.int8.onnx', decoder=f'{D}/decoder-epoch-99-avg-1.onnx',
          joiner=f'{D}/joiner-epoch-99-avg-1.int8.onnx', tokens=f'{D}/tokens.txt', num_threads=threads)
if beam:
    kw.update(decoding_method='modified_beam_search', hotwords_file=HOT, hotwords_score=1.5,
              modeling_unit='bpe', bpe_vocab=f'{D}/bpe.vocab')
t = time.time()
rec = sherpa_onnx.OfflineRecognizer.from_transducer(**kw)
print(f'# {cfg} load {time.time() - t:.1f}s', flush=True)


def load(path):
    with wave.open(path) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype='<i2').astype(np.float32) / 32768
    loud = np.nonzero(np.abs(x) > 0.02)[0]
    # like the hub gets it: ~0.6 s before (its 10-frame pre-roll) and ~1 s of silence after
    return np.concatenate([np.zeros(9600, np.float32), x[loud[0]:loud[-1] + 1], np.zeros(int(16000 * float(os.environ.get("POST", "1"))), np.float32)])


def dec(x):
    s = rec.create_stream()
    s.accept_waveform(16000, x)
    t = time.time()
    rec.decode_stream(s)
    return time.time() - t, s.result.text


dec(load(sys.argv[2]))   # warm up
for f in sys.argv[2:]:
    x = load(f)
    times = []
    for _ in range(3):
        dt, text = dec(x)
        times.append(dt)
    print(f'{cfg}\t{os.path.basename(f)}\t{len(x) / 16000:.2f}s\t{min(times) * 1000:.0f}\t{np.mean(times) * 1000:.0f}\t{text}', flush=True)
