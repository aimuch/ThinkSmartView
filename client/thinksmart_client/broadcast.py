"""定时播报（设置 › 闹钟和作息，设计画布第 6、82 块）：到点设备自己连网关，请模型播报。

可以有好几条（items），每条有自己的时间、哪几天、播什么：天气、闹钟和提醒、待办和清单、新闻三条、一句自己写的话。
下午两点以后的播报讲明天（天气、闹钟和提醒都是明天的），之前的讲今天。

The request is sent as text that starts with a tag in 【】 ("【早安播报 07:30】…"): the page shows that
as a small label instead of something the user said, also when the conversation is loaded back from
the hub. The model answers it like any question, so "那明天呢" right after works.

Busy at that minute (a conversation, a ring, an update, 勿扰): wait until idle, at most WAIT_MAX; past
that the broadcast is skipped and noted as missed on the home page. A time missed while the device was
off is not caught up.

以前只有「天气播报」一种（几个时间共用一样的天数和内容）：normalize 把它变成一样的几条。
The setting lives in the user config (config.save), like the sleep hours."""
import datetime
import re
import secrets

TIME = re.compile(r'^([01]\d|2[0-3]):([0-5]\d)$')
DAYS = {'daily': '每天', 'workdays': '工作日', 'weekend': '周末'}
WEEKDAYS = '一二三四五六日'           # isoweekday 1..7
WHAT = {'weather': '天气', 'reminders': '闹钟和提醒', 'lists': '待办和清单', 'news': '新闻三条', 'say': '一句话'}
MAX_ITEMS = 8
MAX_SAY = 80
STEP_MINUTES = 15
WAIT_MAX = 30 * 60      # seconds a broadcast waits for the device to be free
TOMORROW_FROM = 14      # broadcasts from 14:00 on are about tomorrow
MORNING_UNTIL = 12
EVENING_FROM = 17
# the hub's echo of the text (stt) drops the leading 【, the stored conversation keeps it
TAG = re.compile(r'^【?([^【】]{1,20})】')

DEFAULT = {'items': []}
NEW_ITEM = {'time': '07:30', 'days': 'daily', 'on': True, 'what': ['weather', 'reminders'], 'say': ''}


def _time(value):
    value = str(value).strip().replace('：', ':')
    if re.match(r'^\d:', value):
        value = '0' + value
    m = TIME.match(value)
    if not m:
        raise ValueError(f'时间要是 24 小时制的 HH:MM，比如 07:30，收到的是 {value!r}')
    return value


def _days(value):
    if isinstance(value, str) and value in DAYS:
        return value
    if isinstance(value, (list, tuple)):
        days = sorted({int(d) for d in value if isinstance(d, int) and not isinstance(d, bool) and 1 <= d <= 7})
        if days:
            return 'daily' if days == list(range(1, 8)) else 'workdays' if days == [1, 2, 3, 4, 5] else 'weekend' if days == [6, 7] else days
    raise ValueError('哪几天：每天、工作日、周末，或者星期几（1–7）')


def _item(v, old=None):
    """一条完整、有效的播报；ValueError 说哪里不对。"""
    it = {**NEW_ITEM, **(old or {})}
    it.update({k: val for k, val in (v or {}).items() if k in NEW_ITEM and val is not None})
    it['id'] = (old or {}).get('id') or (v or {}).get('id') or secrets.token_hex(3)
    it['time'] = _time(it['time'])
    it['days'] = _days(it['days'])
    it['on'] = bool(it['on'])
    what = [w for w in WHAT if w in (it['what'] or [])]
    if not what:
        raise ValueError('至少要播一样：天气、闹钟和提醒、待办和清单、新闻或者一句话')
    it['what'] = what
    it['say'] = str(it.get('say') or '').strip()[:MAX_SAY]
    if 'say' in it['what'] and not it['say']:
        raise ValueError('「一句话」要写上念什么')
    return it


def normalize(setting):
    """A complete, valid setting {'items': [...]}，按时间排；以前的天气播报设置变成一样的几条。"""
    s = setting if isinstance(setting, dict) else {}
    if 'items' not in s and 'times' in s:   # 以前的「天气播报」
        what = ['weather'] + [k for k in ('reminders', 'news') if s.get(k)]
        days = s.get('days') if isinstance(s.get('days'), str) and s.get('days') in DAYS else 'daily'
        s = {'items': [{'time': t, 'days': days, 'on': bool(s.get('enabled')), 'what': what} for t in s.get('times') or []]}
    items = []
    for v in s.get('items') or []:
        try:
            items.append(_item(v, v if isinstance(v, dict) and v.get('id') else None))
        except (ValueError, TypeError):
            continue
    return {'items': sorted(items, key=lambda i: (i['time'], i['id']))[:MAX_ITEMS]}


def apply(setting, op):
    """改设置：{'add': 条}、{'update': id, 改的字段…}、{'delete': id}、{'all': True/False}（全开 / 全关）。"""
    s = normalize(setting)
    items = s['items']
    if 'add' in op:
        if len(items) >= MAX_ITEMS:
            raise ValueError(f'最多 {MAX_ITEMS} 条播报，先删掉一条')
        items.append(_item(op['add']))
    elif 'update' in op:
        i = next((n for n, it in enumerate(items) if it['id'] == op['update']), None)
        if i is None:
            raise ValueError('没有这条播报')
        items[i] = _item({k: v for k, v in op.items() if k != 'update'}, items[i])
    elif 'delete' in op:
        if not any(it['id'] == op['delete'] for it in items):
            raise ValueError('没有这条播报')
        items = [it for it in items if it['id'] != op['delete']]
    elif 'all' in op:
        items = [{**it, 'on': bool(op['all'])} for it in items]
    return normalize({'items': items})


def find(setting, ident):
    """按 id 或时间找一条（语音说的是时间）。"""
    ident = str(ident or '').strip()
    for it in setting['items']:
        if it['id'] == ident:
            return it
    try:
        t = _time(ident)
    except ValueError:
        return None
    return next((it for it in setting['items'] if it['time'] == t), None)


def shift(hhmm, minutes):
    """HH:MM moved by minutes, wrapping round midnight."""
    h, m = map(int, _time(hhmm).split(':'))
    total = (h * 60 + m + minutes) % (24 * 60)
    return f'{total // 60:02d}:{total % 60:02d}'


def on_day(days, date):
    weekday = date.isoweekday()
    if isinstance(days, list):
        return weekday in days
    return days == 'daily' or (days == 'workdays' and weekday <= 5) or (days == 'weekend' and weekday >= 6)


def about(hhmm):
    """'today' or 'tomorrow': which day a broadcast at hhmm talks about."""
    return 'tomorrow' if int(hhmm[:2]) >= TOMORROW_FROM else 'today'


def due(setting, now, fired):
    """The broadcasts due now: (item, key, at) whose minute has come today and that haven't fired today
    (fired: {'YYYY-MM-DD HH:MM id', ...}). Only within WAIT_MAX of the time (a time passed while the
    device was off isn't caught up)."""
    out = []
    for it in setting['items']:
        if not it['on'] or not on_day(it['days'], now.date()):
            continue
        h, m = map(int, it['time'].split(':'))
        at = now.replace(hour=h, minute=m, second=0, microsecond=0)
        key = f"{now.date().isoformat()} {it['time']} {it['id']}"
        if key not in fired and at <= now < at + datetime.timedelta(seconds=WAIT_MAX):
            out.append((it, key, at))
    return out


FRESH = '先调用查天气工具查最新的天气（不要用之前对话里的天气，可能是旧的），再'


def label(hhmm):
    h = int(hhmm[:2])
    return '早安播报' if h < MORNING_UNTIL else '晚间播报' if h >= EVENING_FROM else '定时播报'


def request_text(item, home_name, reminders=None, lists=None, hhmm=None):
    """What is sent to the model (as if typed): the tag the page shows, then the request. It asks for
    a fresh lookup: with yesterday's broadcast still in the conversation the model answered from that.
    reminders：要讲的那天的闹钟和提醒（几条字）；lists：待办和清单（几条字）。"""
    hhmm = hhmm or item['time']
    place = home_name or '这里'
    day = '明天' if about(hhmm) == 'tomorrow' else '今天'
    parts = []
    what = item['what']
    if 'weather' in what:
        now = '现在的天气和气温、' if day == '今天' else ''
        parts.append(f'{FRESH}播报{place}{day}的天气：{now}气温范围、会不会下雨，需要带伞、加衣服的话提醒一句')
    if 'reminders' in what:
        parts.append(f'说一下{day}的闹钟和提醒：' + '；'.join(reminders) if reminders else f'{day}没有闹钟和提醒，不用提')
    if 'lists' in what:
        parts.append('说一下待办和清单里还没做的：' + '；'.join(lists) if lists else '待办和清单都是空的，不用提')
    if 'news' in what:
        parts.append('用一两句话说三条今天的新闻头条')
    if 'say' in what and item.get('say'):
        parts.append(f'最后原样念这一句：「{item["say"]}」')
    greeting = '早上好，' if int(hhmm[:2]) < MORNING_UNTIL else '晚上好，' if int(hhmm[:2]) >= EVENING_FROM else ''
    return f"【{label(hhmm)} {hhmm}】{greeting}{'。'.join(parts)}。说得简短自然，不要问问题。"


def tag_of(text):
    """The label of a request the device sent on its own ('早安播报 07:30', '拍照问 09:41'), or None for
    anything the user said."""
    m = TAG.match(text or '')
    return m.group(1) if m and ('播报' in m.group(1) or m.group(1).startswith(('拍照问', '定时任务'))) else None


def days_text(days):
    return DAYS[days] if isinstance(days, str) else '每周' + '、'.join(WEEKDAYS[d - 1] for d in days)


def item_text(it):
    """「07:30 工作日：天气、闹钟和提醒」。"""
    what = '、'.join(WHAT[w] if w != 'say' else f'「{it["say"]}」' for w in it['what'])
    return f"{it['time']} {days_text(it['days'])}：{what}" + ('' if it['on'] else '（关着）')


def describe(setting):
    """The setting in words (for the model and the card's header)."""
    items = setting['items']
    if not items:
        return '还没有定时播报'
    if not any(it['on'] for it in items):
        return '定时播报都关着'
    return '；'.join(item_text(it) for it in items)
