"""Sleep hours (requirements section eight): reports from the hub wait on the screen instead of
being read out. Since 2026-11-10 (设计画布第 29 块) the screen goes to the sleep brightness and the
speaker to the sleep volume when they start, and both come back when they end; the avatar sleeps.
Waking the device works as usual (at the sleep volume).
The setting lives in the user config (config.save), so a client update keeps it."""
import datetime
import re

TIME = re.compile(r'^([01]\d|2[0-3]):[0-5]\d$')
BRIGHTNESS = 10   # the screen's 0-100 in the sleep hours
VOLUME = 15       # the speaker's 0-100 in the sleep hours (30 was louder than many daytime levels: 2026-11-10)


def normalize(setting, changes=None):
    """A complete, valid setting: {'enabled': bool, 'start': 'HH:MM', 'end': 'HH:MM', 'brightness': 0-100,
    'volume': 0-100}. Raises ValueError for a time that isn't HH:MM."""
    s = {'enabled': False, 'start': '22:00', 'end': '07:00', 'brightness': BRIGHTNESS, 'volume': VOLUME}
    s.update({k: v for k, v in (setting or {}).items() if k in s})
    s.update({k: v for k, v in (changes or {}).items() if k in s and v is not None})
    s['enabled'] = bool(s['enabled'])
    for key in ('start', 'end'):
        value = str(s[key]).strip()
        if re.match(r'^\d:', value):
            value = '0' + value
        if not TIME.match(value):
            raise ValueError(f'{key} must be HH:MM, got {s[key]!r}')
        s[key] = value
    for key, default in (('brightness', BRIGHTNESS), ('volume', VOLUME)):
        try:
            s[key] = max(0, min(100, int(s[key])))
        except (TypeError, ValueError):
            s[key] = default
    return s


def active(setting, now=None):
    """Whether now falls in the sleep hours; the range may cross midnight (22:00-07:00)."""
    if not setting['enabled'] or setting['start'] == setting['end']:
        return False
    t = (now or datetime.datetime.now()).strftime('%H:%M')
    start, end = setting['start'], setting['end']
    return start <= t < end if start < end else (t >= start or t < end)
