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


HISTORY_DAYS = 14        # 「最近 7 天」用到 7 天，多留一周


def _usage():
    try:
        data = json.loads(USAGE.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load_usage(today):
    data = _usage()
    if data.get('date') == today:
        return float(data.get('seconds') or 0)
    return 0.0


def save_usage(today, seconds):
    """今天的秒数；以前的天数留在 days 里（{日期: 秒}），设置页「最近 7 天」用。"""
    data = _usage()
    days = {k: v for k, v in (data.get('days') or {}).items() if isinstance(v, (int, float))}
    if data.get('date') and data.get('date') != today:
        days[data['date']] = float(data.get('seconds') or 0)
    days[today] = round(seconds, 1)
    days = dict(sorted(days.items())[-HISTORY_DAYS:])
    try:
        USAGE.parent.mkdir(parents=True, exist_ok=True)
        tmp = USAGE.with_suffix('.tmp')
        tmp.write_text(json.dumps({'date': today, 'seconds': round(seconds, 1), 'days': days}))
        tmp.replace(USAGE)
    except OSError:
        pass


def week(today):
    """最近 7 天（到 today）每天聊了几分钟：[{date, minutes}]，没记录的天是 0。"""
    data = _usage()
    days = dict(data.get('days') or {})
    if data.get('date'):
        days[data['date']] = float(data.get('seconds') or 0)
    d = datetime.date.fromisoformat(today)
    out = []
    for i in range(6, -1, -1):
        k = (d - datetime.timedelta(days=i)).isoformat()
        out.append({'date': k, 'minutes': int(float(days.get(k) or 0) // 60)})
    return out


# 学一学（设计画布第 34 块）：点一个，发一句带标签的文字给模型，模型照着带孩子学。{age} 换成孩子几岁
LEARN = {
    'chars': ('认字卡', '和孩子玩认字卡：每次出一个适合{age}岁孩子的常用汉字，调用工具 self_screen_show_card 在屏幕上显示这个大字'
                        '（标题就是这个字，正文写拼音和一个组词），然后问孩子认不认识、怎么读；孩子说完告诉他对不对，'
                        '再组一个词，接着出下一个字。一次只出一个字，等孩子回答。'),
    'poem': ('背古诗', '带孩子背一首适合{age}岁孩子的短古诗：先说诗名和作者，再一句一句念，每念一句让孩子跟着念一遍，'
                       '等他念完再念下一句；背完夸一句，问他要不要再来一首。'),
    'math': ('口算', '和孩子玩口算：按{age}岁孩子的水平出一道加减法（小一点的孩子用 10 以内），一次只出一道，等他回答；'
                     '答对了夸一句再出下一道，答错了用数手指的办法讲一下，再出一道差不多的。'),
    'why': ('十万个为什么', '给{age}岁的孩子讲一个有意思的科学小知识（比如天为什么是蓝的），三四句讲完，'
                          '最后问孩子一个简单的问题，等他回答。'),
}


def learn_prompt(kind, age):
    if kind not in LEARN:
        raise ValueError('没有这个学习项目')
    name, text = LEARN[kind]
    return f'【学习·{name}】' + text.format(age=int(age or 6))


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
