"""Second look at a wake word hit: recognise the last few seconds and check the wake word is really there.

The wake word model (kws.py) is small and tuned to catch the wake word easily, so it also fires on
things that sound close (小妹小妹, 小白同学, 小美同学). On a hit, the audio it heard goes to a full
speech recogniser and the text is compared with the wake words by sound (toneless pinyin). The last
syllable of the name (麦 of 小麦, the part all wake words share) must sound exactly right: 小卖 and
笑麦 count, 小美, 小妹 and 小白 don't. Other syllables may be off by one, or by two half-wrong ones
(same initial or same final): the recogniser writes 哈喽小麦 as 还有小麦 or Hello 小麦, and 小麦小麦
as 洗了麦小麦. In order:
1. a wake word, or just the name 小麦, is in the text -> wake (from far away or over a TV the
   recogniser often gets only the second 小麦 of 小麦小麦);
2. something shaped like a wake word with the wrong name (小妹小妹, 小白同学) -> ignore;
3. hardly anything recognised (3 syllables or less) -> wake: the recogniser is unsure, not the
   wake word model wrong, and a missed wake is worse than a false one;
4. other speech -> ignore.
Measured with eval/wake (synthetic voices): false wakes on confusable sentences 29 -> 6, true
wakes lost: 1% near, 5% across the room, 11% with a TV talking.
The audio is brought to a normal speaking level first: like the wake word model, the recogniser
does worse on quiet audio.

Checkers (config 'wake_verify'):
  'hub'        the hub's own recogniser (POST /kiosk/asr, patch-hub.py): no extra memory here
  'asr:<dir>'  a sherpa-onnx offline model in this process (evaluation)
  ''           off
"""
import json
import logging
import pathlib
import re
import time
import urllib.request

import numpy as np

log = logging.getLogger(__name__)

TAIL = 8000   # samples of silence after the hit (0.5 s)
LEVEL = 10 ** (-20 / 20)   # loudest 100 ms brought to this RMS, with at most MAX_GAIN
MAX_GAIN = 10 ** (30 / 20)
LATIN = {'hello': ['ha', 'lou'], 'hallo': ['ha', 'lou'], 'halo': ['ha', 'lou']}

INITIALS = ('zh', 'ch', 'sh', 'b', 'p', 'm', 'f', 'd', 't', 'n', 'l', 'g', 'k', 'h', 'j', 'q', 'x', 'r',
            'z', 'c', 's', 'y', 'w')
_PINYIN = None


def pinyin(text):
    """Toneless syllable per Chinese character (None for unknown ones); other characters are skipped."""
    global _PINYIN
    if _PINYIN is None:
        _PINYIN = {}
        for line in (pathlib.Path(__file__).parent / 'pinyin.txt').read_text().splitlines():
            if line and not line.startswith('#'):
                syllable, chars = line.split('\t')
                for ch in chars:
                    _PINYIN.setdefault(ch, syllable)
    out = []
    for word in re.findall(r'[a-zA-Z]+|[\u4e00-\u9fff]', text):
        if word.isascii():
            out += LATIN.get(word.lower(), [None])
        else:
            out.append(_PINYIN.get(word))
    return out


def _split(s):
    for i in INITIALS:
        if s.startswith(i):
            return i, s[len(i):]
    return '', s


def _distance(a, b):
    if a == b:
        return 0.0
    if a is None or b is None:
        return 1.0
    (ia, fa), (ib, fb) = _split(a), _split(b)
    return 0.5 if ia == ib or fa == fb else 1.0


def _common(labels):
    """Longest substring shared by all wake words: the device's name."""
    first = min(labels, key=len)
    for n in range(len(first), 0, -1):
        for i in range(len(first) - n + 1):
            part = first[i:i + n]
            if all(part in w for w in labels):
                return part
    return ''


class Matcher:
    def __init__(self, wake_words, slack=1.0):
        self.labels = [w.split('@')[-1].strip() for w in wake_words]
        self.name = _common(self.labels)
        self.slack = slack

    def _cost(self, heard, label, wrong_name):
        """Lowest mismatch of label against any stretch of heard; wrong_name: cost of a wrong 麦."""
        want = pinyin(label)
        strict = [bool(self.name) and ch == self.name[-1] for ch in label]
        best = 99.0
        for start in range(len(heard) - len(want) + 1):
            cost = 0.0
            for k, w in enumerate(want):
                d = _distance(heard[start + k], w)
                cost += wrong_name if strict[k] and d else d
            best = min(best, cost)
        return best

    def judge(self, text):
        """(wake?, why) for recognised text; see the module docstring for the rules."""
        heard = pinyin(text or '')
        for label in self.labels:
            if self._cost(heard, label, 99) <= self.slack:
                return True, label
        if self.name and self._cost(heard, self.name, 99) <= self.slack / 2:
            return True, self.name
        for label in self.labels:
            if self._cost(heard, label, 1) <= self.slack + 1:
                return False, f'not {label}'
        if len(heard) <= 3:
            return True, 'unclear'
        return False, 'other speech'

def prepare(audio):
    """Bring the loudest 100 ms to a normal speaking level and add 0.5 s of silence: the hit comes
    right as the wake word ends; without some silence after it the recogniser lacks right context
    for the last syllable (小麦小麦 came out as 洗澡麦洗脚麦)."""
    loudest = max(np.sqrt(np.mean(audio[i:i + 1600] ** 2)) for i in range(0, max(len(audio) - 1599, 1), 800))
    return np.concatenate([audio * min(LEVEL / max(loudest, 1e-6), MAX_GAIN), np.zeros(TAIL)]).astype(np.float32)


class HubChecker:
    """Recognise with the hub's own speech recogniser (it is already loaded there)."""

    def __init__(self, url, timeout=4.0):
        self.url = url.rstrip('/') + '/kiosk/asr'
        self.timeout = timeout

    def text(self, audio):
        pcm = np.clip(audio * 32768, -32768, 32767).astype('<i2').tobytes()
        req = urllib.request.Request(self.url, data=pcm, headers={'Content-Type': 'audio/L16; rate=16000'})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            answer = json.loads(r.read())
        log.info('hub recognised %.1f s of audio in %s ms', len(audio) / 16000, answer.get('ms'))
        return answer['text']


class LocalChecker:
    """A sherpa-onnx offline recogniser in this process (used by the evaluation to compare models)."""

    def __init__(self, model_dir, threads=1):
        import sherpa_onnx
        d = pathlib.Path(model_dir)

        def one(pattern):
            found = sorted(d.glob(pattern), key=lambda p: ('int8' not in p.name, p.name))
            return str(found[0]) if found else None

        tokens = str(d / 'tokens.txt')
        if one('joiner*.onnx'):
            self.rec = sherpa_onnx.OfflineRecognizer.from_transducer(
                encoder=one('encoder*.onnx'), decoder=one('decoder*.onnx'), joiner=one('joiner*.onnx'),
                tokens=tokens, num_threads=threads)
        elif 'paraformer' in d.name:
            self.rec = sherpa_onnx.OfflineRecognizer.from_paraformer(paraformer=one('model*.onnx'),
                                                                     tokens=tokens, num_threads=threads)
        elif 'streaming' in d.name:   # a streaming CTC model, fed the whole clip at once
            self.online = sherpa_onnx.OnlineRecognizer.from_zipformer2_ctc(model=one('*.onnx'), tokens=tokens,
                                                                           num_threads=threads)
            self.rec = None
        else:
            self.rec = sherpa_onnx.OfflineRecognizer.from_zipformer_ctc(model=one('model*.onnx'), tokens=tokens,
                                                                        num_threads=threads)

    def text(self, audio):
        if self.rec is None:
            s = self.online.create_stream()
            s.accept_waveform(16000, audio)
            s.accept_waveform(16000, np.zeros(8000, np.float32))
            s.input_finished()
            while self.online.is_ready(s):
                self.online.decode_stream(s)
            return self.online.get_result(s)
        s = self.rec.create_stream()
        s.accept_waveform(16000, audio)
        self.rec.decode_stream(s)
        return s.result.text


class Verifier:
    def __init__(self, checker, wake_words):
        self.checker = checker
        self.matcher = Matcher(wake_words)

    def check(self, audio, label):
        """{'ok': is the wake word really there, 'text': what was recognised}. Fails open: if the
        recogniser can't be asked, the wake counts (a missed wake is worse than a false one)."""
        t0 = time.monotonic()
        audio = prepare(audio)
        try:
            text = self.checker.text(audio)
        except Exception as e:
            log.warning('wake check failed, accepting the wake: %s', e)
            return {'ok': True, 'text': None, 'error': str(e)}
        ok, why = self.matcher.judge(text)
        log.info('wake check %s: heard %r in %.0f ms -> %s (%s)', label, text, (time.monotonic() - t0) * 1000,
                 'wake' if ok else 'ignored', why)
        return {'ok': ok, 'text': text, 'why': why}


def make(spec, wake_words, hub_http='http://127.0.0.1:16688'):
    if not spec:
        return None
    if spec == 'hub':
        return Verifier(HubChecker(hub_http), wake_words)
    if spec.startswith('asr:'):
        return Verifier(LocalChecker(spec[4:]), wake_words)
    raise ValueError(f'unknown wake_verify {spec!r}')
