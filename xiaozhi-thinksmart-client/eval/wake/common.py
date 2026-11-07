"""Shared by the wake word evaluation scripts: where the audio lives, the sentence list, wav io."""
import collections
import os
import pathlib
import wave

import numpy as np

HERE = pathlib.Path(__file__).parent
CLIENT = HERE.parent.parent          # the thinksmart-client checkout
# audio is too big for git: kept outside the repository (see README.md for how to rebuild it)
DATA = pathlib.Path(os.environ.get('WAKE_EVAL_DATA', pathlib.Path.home() / 'thinksmart-wake-eval'))
RATE = 16000

Sentence = collections.namedtuple('Sentence', 'kind id text label')


def sentences():
    out = []
    for line in (HERE / 'sentences.tsv').read_text().splitlines():
        if line and not line.startswith('#'):
            parts = line.split('\t')
            out.append(Sentence(parts[0], parts[1], parts[2], parts[3] if len(parts) > 3 else None))
    return out


def read_wav(path):
    with wave.open(str(path)) as w:
        assert w.getframerate() == RATE and w.getnchannels() == 1, path
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768


def write_wav(path, x):
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(np.clip(x * 32768, -32768, 32767).astype(np.int16).tobytes())
