"""Wake word spotting with sherpa-onnx (open vocabulary: keywords are written as pinyin tokens)."""
import collections
import logging
import tempfile
import time

import numpy as np
import sherpa_onnx

log = logging.getLogger(__name__)

REFRACTORY = 2.0  # seconds after a hit during which the same speech can't wake again
# Feed the model 40 ms at a time with one thread (onnxruntime's worker threads spin while waiting:
# two threads + a decode every 20 ms cost ~1.7 cores idle on this device). It decodes only once it
# has a whole chunk, so smaller batches cost little; with 100 ms the wake word was reported ~40 ms
# later on average (eval: 398 -> 361 ms after the end of the word)
BATCH = 640
# Decoding costs ~37% of a core; in a quiet room skip it. Speech starts the decoder again
# together with the quiet audio just before it (PREROLL batches, ~0.1 s), so the start of the wake
# word isn't lost.
QUIET_RMS = 10 ** (-50 / 20)
PREROLL = 3
# Batches (1 s) to keep decoding after the sound stops. The model needs ~0.5 s after a keyword
# to report it; with 0.5 s a quietly spoken wake word (-48 dBFS) was often reported only when the
# next sound started the decoder again (21 of 250 hits; 1 with 1 s; eval/wake)
HANGOVER = 25
RECENT = 3.0  # seconds of audio kept for a second look at a hit (wake_verify.py)


class Kws:
    def __init__(self, model_dir, keywords, score=2.0, threshold=0.25, paths=8, gain_db=0.0):
        """keywords: lines like 'x iǎo m ài x iǎo m ài @小麦小麦'. gain_db: applied to what the model
        hears (not to the quiet gate): the model misses more at low levels."""
        # sherpa-onnx only takes keywords from a file, and reads it here once
        with tempfile.NamedTemporaryFile('w', suffix='.txt') as kw_file:
            kw_file.write('\n'.join(keywords) + '\n')
            kw_file.flush()
            self._spotter = sherpa_onnx.KeywordSpotter(
                tokens=f'{model_dir}/tokens.txt',
                encoder=f'{model_dir}/encoder.onnx',
                decoder=f'{model_dir}/decoder.onnx',
                joiner=f'{model_dir}/joiner.onnx',
                keywords_file=kw_file.name,
                num_threads=1,
                max_active_paths=paths,
                keywords_score=score,
                keywords_threshold=threshold,
                num_trailing_blanks=1,
                provider='cpu',
            )
        self._stream = self._spotter.create_stream()
        self._gain = 10 ** (gain_db / 20)
        self._last_hit = 0.0
        self._pending = []
        self._pending_n = 0
        self._hang = 0
        self._preroll = collections.deque(maxlen=PREROLL)
        self._recent = collections.deque()
        self._recent_n = 0

    def recent(self):
        """The last RECENT seconds of audio fed in (ends where the last hit was found)."""
        return np.concatenate(self._recent) if self._recent else np.zeros(0, np.float32)

    def feed(self, samples, rate=16000):
        """Returns the keyword's label when it was just spoken, else None."""
        self._recent.append(samples)
        self._recent_n += len(samples)
        while self._recent_n - len(self._recent[0]) >= RECENT * rate:
            self._recent_n -= len(self._recent.popleft())
        self._pending.append(samples)
        self._pending_n += len(samples)
        if self._pending_n < BATCH:
            return None
        batch = np.concatenate(self._pending)
        self._pending, self._pending_n = [], 0
        if float(np.sqrt(np.mean(batch ** 2))) >= QUIET_RMS:
            if self._hang == 0 and self._preroll:
                self._stream.accept_waveform(rate, np.concatenate(self._preroll) * self._gain)
                self._preroll.clear()
            self._hang = HANGOVER
        elif self._hang == 0:
            self._preroll.append(batch)
            return None
        else:
            self._hang -= 1
        self._stream.accept_waveform(rate, batch * self._gain)
        while self._spotter.is_ready(self._stream):
            self._spotter.decode_stream(self._stream)
            hit = self._spotter.get_result(self._stream)
            if hit:
                self._spotter.reset_stream(self._stream)
                now = time.monotonic()
                if now - self._last_hit >= REFRACTORY:
                    self._last_hit = now
                    return hit
        return None
