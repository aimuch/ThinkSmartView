"""Custom wake words: said on the settings page (or asked for by voice), turned into wake word keywords.

The on-screen keyboard has only letters, so a new wake word is spoken: the settings card records it
while the button is held, the hub's recogniser writes it down (POST /kiosk/asr, as wake_verify.py
does) and each character becomes the wake word model's tokens from kws_pinyin.txt (made by
eval/wake/make_pinyin.py: initial + final with tone, all readings, most common first; no pypinyin
on the device). The model goes by sound, so a homophone the recogniser picks is fine; a character
with several readings starts with the common one and the card lets the user tap through the others.

Checks (analyse): two characters or fewer are refused (said in passing all the time: 小天 woke on
nearly every sentence with it, like 小麦 alone did), three get a warning, a word that sounds the same
as one already set and a sound the model has no token for are refused with the reason.

The list lives in config.json 'wake_words' only once changed (reset removes it, back to
config.DEFAULTS). Changes apply at once: the wake word model and wake_verify's matcher are rebuilt
off the event loop and swapped in (Client.set_wake_words)."""
import pathlib
import re

from . import wake_verify

TABLE = pathlib.Path(__file__).parent / 'kws_pinyin.txt'
MAX_CHARS = 8
MAX_WORDS = 6
RECORD_SECONDS = 4.0
INITIALS = {'b', 'p', 'm', 'f', 'd', 't', 'n', 'l', 'g', 'k', 'h', 'j', 'q', 'x', 'zh', 'ch', 'sh', 'r',
            'z', 'c', 's', 'y', 'w'}
_TABLE = None
CJK = re.compile(r'[一-鿿]')


def readings(ch):
    """The character's readings as model tokens ('x iǎo'), most common first; [] if it has none."""
    global _TABLE
    if _TABLE is None:
        _TABLE = {}
        for line in TABLE.read_text().splitlines():
            if line and not line.startswith('#'):
                c, rs = line.split('\t')
                _TABLE[c] = rs.split('|')
    return _TABLE.get(ch, [])


def syllables(tokens):
    """['x', 'iǎo', 'm', 'ài'] -> ['xiǎo', 'mài'] (an initial joins the final after it)."""
    out, ini = [], ''
    for t in tokens:
        if t in INITIALS and not ini:
            ini = t
        else:
            out.append(ini + t)
            ini = ''
    if ini:
        out.append(ini)
    return out


def parse(line):
    """A keyword line 'x iǎo m ài x iǎo m ài @小麦小麦' -> {'label', 'pinyin'}."""
    tokens, _, label = line.partition('@')
    return {'label': label.strip(), 'pinyin': ' '.join(syllables(tokens.split()))}


def keyword(label, picks=None):
    """The keyword line for label, with picks[i] choosing the reading of character i."""
    picks = list(picks or [])
    tokens = []
    for i, ch in enumerate(label):
        rs = readings(ch)
        if not rs:
            raise ValueError(f'「{ch}」的读音唤醒词模型念不了')
        pick = picks[i] if i < len(picks) else 0
        if not isinstance(pick, int) or not 0 <= pick < len(rs):
            raise ValueError(f'「{ch}」没有第 {pick} 种读音')
        tokens.append(rs[pick])
    return f"{' '.join(tokens)} @{label}"


def _same_sound(a, b):
    """The two labels sound alike (toneless, at most half a syllable off), as wake_verify compares."""
    pa, pb = wake_verify.pinyin(a), wake_verify.pinyin(b)
    if len(pa) != len(pb):
        return False
    return sum(wake_verify._distance(x, y) for x, y in zip(pa, pb)) <= 0.5


def analyse(text, existing=()):
    """What the card shows for recognised text: {'label', 'chars': [{'ch', 'pinyin': [...], 'pick'}],
    'error' (can't be added, why) or 'warning' (can, but...)}. existing: the labels already set."""
    text = (text or '').strip()
    label = ''.join(CJK.findall(text))
    chars = [{'ch': ch, 'pinyin': [''.join(syllables(r.split())) for r in readings(ch)], 'pick': 0} for ch in label]
    out = {'text': text, 'label': label, 'chars': chars, 'error': None, 'warning': None}
    if not label:
        out['error'] = '没听清，按住再说一次' if not text else f'听成了「{text}」，唤醒词只能用中文，再说一次'
    elif re.search(r'[A-Za-z0-9]', text):
        out['error'] = f'听成了「{text}」，唤醒词只能用中文，再说一次'
    elif len(label) == 1:
        out['error'] = '只有一个字，至少要三个字，四个字最好'
    elif len(label) == 2:
        out['error'] = f'只有两个字，说话里经常出现，会频繁误唤醒。建议说成「{label * 2}」或「你好{label}」'
    elif len(label) > MAX_CHARS:
        out['error'] = f'太长了，最多 {MAX_CHARS} 个字'
    elif any(not c['pinyin'] for c in chars):
        bad = next(c['ch'] for c in chars if not c['pinyin'])
        out['error'] = f'「{bad}」的读音唤醒词模型念不了，换个说法'
    else:
        twin = next((e for e in existing if e == label or _same_sound(e, label)), None)
        if twin == label:
            out['error'] = f'已经有「{label}」了'
        elif twin:
            out['error'] = f'和「{twin}」读音几乎一样，换一个'
        elif len(label) == 3:
            out['warning'] = '只有三个字，比四个字容易误唤醒'
    return out


def validate(lines):
    if not lines:
        raise ValueError('至少要留一个唤醒词')
    if len(lines) > MAX_WORDS:
        raise ValueError(f'最多 {MAX_WORDS} 个唤醒词')
    labels = [parse(x)['label'] for x in lines]
    if len(set(labels)) != len(labels):
        raise ValueError('唤醒词重复了')
    return lines
