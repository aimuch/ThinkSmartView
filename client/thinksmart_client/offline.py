"""断网兜底 (设计画布第 13 块): when the hub can't be reached after a wake, a few commands still work for
WINDOW seconds — added to the wake word model, recognised and answered here. The answers are clips the
hub synthesised earlier, while it could (CLIPS: synthesised once in the background, kept on disk)."""
from pathlib import Path

WORDS = ['音量大一点', '音量小一点', '现在几点', '关掉所有灯', '打开所有灯']
WINDOW = 10
CLIPS = Path.home() / '.local' / 'share' / 'thinksmart-client' / 'offline'
DIGITS = '零一二三四五六七八九'


def cn(n):
    """0-59 in Chinese words (二十三, 十, 零)."""
    if n < 10:
        return DIGITS[n]
    tens, ones = divmod(n, 10)
    return ('' if tens == 1 else DIGITS[tens]) + '十' + (DIGITS[ones] if ones else '')


def phrases():
    """{clip name: words} for every clip the offline answers use."""
    out = {'ok': '好的。', 'no_home': '米家还没登录，开关不了灯。', 'hint': '连不上小麦网关，可以直接说音量大一点、现在几点、关掉所有灯。',
           'now': '现在是'}
    for h in range(24):
        out[f'h{h}'] = ('两' if h == 2 else cn(h)) + '点'
    out['m0'] = '整。'
    for m in range(1, 60):
        out[f'm{m}'] = ('零' + cn(m) if m < 10 else cn(m)) + '分。'
    return out


def time_clips(hour, minute):
    return ['now', f'h{hour}', f'm{minute}']
