"""儿童模式 (设置页「儿童模式」, 设计画布第 10 块). The hub makes the answers child-friendly (its system prompt,
a child's voice: core/utils/device_prefs.py); the device keeps the limits: talk at most daily_minutes a
day in kids mode, and none from bedtime until MORNING. Past a limit a wake only says why (a clip the hub
synthesised) and doesn't connect. Alarms, reminders and weather broadcasts are not limited.

Turning it off by voice isn't offered (a child could), only on the settings page."""
import datetime
import json
import re
from pathlib import Path

DEFAULT = {'enabled': False, 'age': 3, 'daily_minutes': 30, 'bedtime': '20:30', 'voice': 'child'}
MORNING = '06:30'
USAGE = Path.home() / '.local' / 'share' / 'thinksmart-client' / 'kids-usage.json'
TIME = re.compile(r'^([01]\d|2[0-3]):[0-5]\d$')
SAY_LIMIT = '今天聊得够多啦，明天再来找我玩吧。'
SAY_BEDTIME = '该睡觉啦，明天早上再来找我玩吧，晚安。'


def normalize(setting, changes=None):
    s = dict(DEFAULT)
    s.update({k: v for k, v in (setting or {}).items() if k in DEFAULT})
    s.update({k: v for k, v in (changes or {}).items() if k in DEFAULT and v is not None})
    s['enabled'] = bool(s['enabled'])
    s['age'] = int(s['age'])
    if not 1 <= s['age'] <= 14:
        raise ValueError('年龄要在 1 到 14 岁之间')
    s['daily_minutes'] = int(s['daily_minutes'])
    if not 0 <= s['daily_minutes'] <= 600:
        raise ValueError('每天最多聊的时间要在 0 到 600 分钟之间（0 是不限）')
    b = str(s['bedtime'] or '').strip()
    if b and not TIME.match(b):
        raise ValueError('睡觉时间要是 HH:MM，比如 20:30；不限就留空')
    s['bedtime'] = b
    if s['voice'] not in ('child', 'adult'):
        raise ValueError('声音只能是童声或大人的声音')
    return s


def load_usage(today):
    try:
        data = json.loads(USAGE.read_text())
        if data.get('date') == today:
            return float(data.get('seconds') or 0)
    except (OSError, ValueError):
        pass
    return 0.0


def save_usage(today, seconds):
    try:
        USAGE.parent.mkdir(parents=True, exist_ok=True)
        tmp = USAGE.with_suffix('.tmp')
        tmp.write_text(json.dumps({'date': today, 'seconds': round(seconds, 1)}))
        tmp.replace(USAGE)
    except OSError:
        pass


def bedtime_now(setting, now):
    """From bedtime until MORNING (across midnight)."""
    b = setting.get('bedtime')
    if not b:
        return False
    t = now.strftime('%H:%M')
    return t >= b or t < MORNING if b > MORNING else b <= t < MORNING


def blocked(setting, seconds_today, now=None):
    """Why a wake is refused now ('limit' / 'bedtime'), or None."""
    if not setting['enabled']:
        return None
    now = now or datetime.datetime.now()
    if bedtime_now(setting, now):
        return 'bedtime'
    if setting['daily_minutes'] and seconds_today >= setting['daily_minutes'] * 60:
        return 'limit'
    return None


def shift_time(hhmm, minutes):
    h, m = map(int, hhmm.split(':'))
    total = (h * 60 + m + minutes) % (24 * 60)
    return f'{total // 60:02d}:{total % 60:02d}'
