"""Weather broadcasts (设置页「天气播报」, 设计画布第 6 块): at the times set, the device connects to the
hub on its own and asks the model to read the weather of the home address (and, before noon, today's
alarms and reminders and a few headlines if chosen).

The request is sent as text that starts with a tag in 【】 ("【天气播报 07:30】…"): the page shows that
as a small label instead of something the user said, also when the conversation is loaded back from
the hub. The model answers it like any question, so "那明天呢" right after works.

Busy at that minute (a conversation, a ring, an update): wait until idle, at most WAIT_MAX; past that
the broadcast is skipped and noted as missed on the home page. A time missed while the device was off
is not caught up.

The setting lives in the user config (config.save), like the sleep hours."""
import datetime
import re

TIME = re.compile(r'^([01]\d|2[0-3]):([0-5]\d)$')
DAYS = {'daily': '每天', 'workdays': '工作日', 'weekend': '周末'}
EXTRAS = ('reminders', 'news')
MAX_TIMES = 4
STEP_MINUTES = 15
WAIT_MAX = 30 * 60      # seconds a broadcast waits for the device to be free
TOMORROW_FROM = 14      # broadcasts from 14:00 on are about tomorrow
MORNING_UNTIL = 12      # broadcasts before noon may add today's reminders and headlines
# the hub's echo of the text (stt) drops the leading 【, the stored conversation keeps it
TAG = re.compile(r'^【?([^【】]{1,20})】')

DEFAULT = {'enabled': False, 'times': ['07:30'], 'days': 'daily', 'reminders': True, 'news': False}


def _time(value):
    value = str(value).strip().replace('：', ':')
    if re.match(r'^\d:', value):
        value = '0' + value
    m = TIME.match(value)
    if not m:
        raise ValueError(f'时间要是 24 小时制的 HH:MM，比如 07:30，收到的是 {value!r}')
    return value


def normalize(setting, changes=None):
    """A complete, valid setting. changes may also hold add_time / remove_time. ValueError when invalid."""
    s = dict(DEFAULT)
    s.update({k: v for k, v in (setting or {}).items() if k in DEFAULT})
    changes = dict(changes or {})
    s.update({k: v for k, v in changes.items() if k in DEFAULT and v is not None})
    s['enabled'] = bool(s['enabled'])
    s['reminders'] = bool(s['reminders'])
    s['news'] = bool(s['news'])
    if s['days'] not in DAYS:
        raise ValueError(f"哪几天只能是 {'、'.join(DAYS)}")
    times = [_time(t) for t in (s['times'] or [])]
    if changes.get('remove_time'):
        gone = _time(changes['remove_time'])
        if gone not in times:
            raise ValueError(f'没有 {gone} 这个播报时间')
        times.remove(gone)
    if changes.get('add_time'):
        new = _time(changes['add_time'])
        if new not in times:
            if len(times) >= MAX_TIMES:
                raise ValueError(f'最多 {MAX_TIMES} 个播报时间，先删掉一个')
            times.append(new)
    s['times'] = sorted(set(times))
    return s


def shift(hhmm, minutes):
    """HH:MM moved by minutes, wrapping round midnight."""
    h, m = map(int, _time(hhmm).split(':'))
    total = (h * 60 + m + minutes) % (24 * 60)
    return f'{total // 60:02d}:{total % 60:02d}'


def on_day(days, date):
    weekday = date.isoweekday()
    return days == 'daily' or (days == 'workdays' and weekday <= 5) or (days == 'weekend' and weekday >= 6)


def about(hhmm):
    """'today' or 'tomorrow': which day a broadcast at hhmm talks about."""
    return 'tomorrow' if int(hhmm[:2]) >= TOMORROW_FROM else 'today'


def due(setting, now, fired):
    """The broadcast times due now: HH:MM keys whose minute has come today and that haven't fired
    today (fired: {'YYYY-MM-DD HH:MM', ...}). Only within WAIT_MAX of the time (a time passed while the
    device was off isn't caught up)."""
    if not setting['enabled'] or not on_day(setting['days'], now.date()):
        return []
    out = []
    for t in setting['times']:
        h, m = map(int, t.split(':'))
        at = now.replace(hour=h, minute=m, second=0, microsecond=0)
        key = f'{now.date().isoformat()} {t}'
        if key not in fired and at <= now < at + datetime.timedelta(seconds=WAIT_MAX):
            out.append((t, key, at))
    return out


FRESH = '先调用查天气工具查最新的天气（不要用之前对话里的天气，可能是旧的），再'


def request_text(hhmm, setting, home_name, reminders_today=None):
    """What is sent to the model (as if typed): the tag the page shows, then the request. It asks for
    a fresh lookup: with yesterday's broadcast still in the conversation the model answered from that."""
    place = home_name or '这里'
    if about(hhmm) == 'tomorrow':
        ask = (f'{FRESH}播报{place}明天的天气：天气怎么样、气温范围、会不会下雨，需要带伞、加衣服的话提醒一句。'
               '两三句话说完，不要问问题。')
        return f'【天气播报 {hhmm}】{ask}'
    morning = int(hhmm[:2]) < MORNING_UNTIL
    parts = [f'{FRESH}播报{place}今天的天气：现在的天气和气温、今天的气温范围、会不会下雨，需要带伞、加衣服的话提醒一句']
    if morning and setting.get('reminders'):
        if reminders_today:
            parts.append('再说一下今天的闹钟和提醒：' + '；'.join(reminders_today))
        else:
            parts.append('今天没有闹钟和提醒，不用提')
    if morning and setting.get('news'):
        parts.append('最后用一两句话说三条今天的新闻头条')
    greeting = '早上好，' if morning else ''
    return f"【{'早安播报' if morning else '天气播报'} {hhmm}】{greeting}{'。'.join(parts)}。说得简短自然，不要问问题。"


def tag_of(text):
    """The label of a request the device sent on its own ('早安播报 07:30', '拍照问 09:41'), or None for
    anything the user said."""
    m = TAG.match(text or '')
    return m.group(1) if m and ('播报' in m.group(1) or m.group(1).startswith(('拍照问', '定时任务'))) else None


def describe(setting):
    """The setting in words (for the model and the card's header)."""
    if not setting['enabled']:
        return '天气播报关着'
    times = '、'.join(f"{t}（{'明天' if about(t) == 'tomorrow' else '今天'}的天气）" for t in setting['times'])
    extra = [n for k, n in (('reminders', '今天的闹钟和提醒'), ('news', '新闻三条')) if setting.get(k)]
    text = f"{DAYS[setting['days']]} {times or '（还没有时间）'} 播报天气"
    return text + (f"；中午前的那次顺便播{'和'.join(extra)}" if extra else '')
