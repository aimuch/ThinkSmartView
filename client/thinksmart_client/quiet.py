"""静音和勿扰（设计画布第 81 块）。

静音：现在就不出声。回答只在屏幕上显示文字，唤醒的「在」、音乐、电台、隔空播放都不响；
锁住的静音叫它也取消不了，只能在屏幕上点。
勿扰：在指定的时间里不主动打扰。定时播报推后（最多等 30 分钟，过了算错过），提醒、告警、待汇报只记在首页、
不亮屏不出声，视频对讲来电只亮屏不响、也不自动接通；你叫它照常回答。
两种都可以让闹钟照响（alarms，默认照响）。

关不关声音最后在混音那一处统一把关（audio.Audio.muted，app._silenced 算），哪条路放出来的声音都一样。
设置存在 config.json 的 quiet 里，重启、断电后接着生效。dnd_until 是墙上时间（秒），-1 = 一直，None = 没开。
"""
import datetime
import time

DEFAULT = {'mute': False, 'lock': False, 'dnd_until': None, 'alarms': True}
FOREVER = -1
MORNING = '07:00'          # 「到明早」：下一个早上七点（睡眠时段开着时用它的结束时间）
MAX_MINUTES = 24 * 60


def normalize(value):
    v = value if isinstance(value, dict) else {}
    until = v.get('dnd_until')
    if not (until == FOREVER or (isinstance(until, (int, float)) and not isinstance(until, bool) and until > 0)):
        until = None
    mute = v.get('mute') is True
    return {'mute': mute, 'lock': mute and v.get('lock') is True, 'dnd_until': until, 'alarms': v.get('alarms') is not False}


def next_time(hhmm, now=None):
    """下一个 hh:mm（墙上时间，秒）。"""
    now = datetime.datetime.fromtimestamp(now if now is not None else time.time())
    h, m = (int(x) for x in str(hhmm or MORNING).split(':'))
    at = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if at <= now:
        at += datetime.timedelta(days=1)
    return at.timestamp()


def apply(q, changes, now=None, morning=MORNING, by_voice=False):
    """改设置。changes：mute（bool）、lock（bool）、alarms（bool）、dnd（分钟数 | 'morning' | 'forever' | 'off'）。
    锁住的静音用语音取消时报 ValueError。"""
    q = dict(normalize(q))
    now = now if now is not None else time.time()
    if 'mute' in changes:
        if not changes['mute'] and q['mute'] and q['lock'] and by_voice:
            raise ValueError('静音锁住了：要在屏幕上点「取消静音」')
        q['mute'] = bool(changes['mute'])
        if not q['mute']:
            q['lock'] = False
    if 'lock' in changes:
        q['lock'] = bool(changes['lock']) and q['mute']
    if 'alarms' in changes:
        q['alarms'] = bool(changes['alarms'])
    if 'dnd' in changes:
        d = changes['dnd']
        if d in (None, False, 'off', 0):
            q['dnd_until'] = None
        elif d == 'forever':
            q['dnd_until'] = FOREVER
        elif d == 'morning':
            q['dnd_until'] = next_time(morning, now)
        elif isinstance(d, (int, float)) and not isinstance(d, bool) and 0 < d <= MAX_MINUTES:
            q['dnd_until'] = now + d * 60
        else:
            raise ValueError('勿扰要说多久：几分钟（最多一天）、到明早，或者一直')
    return normalize(q)


def dnd_active(q, now=None):
    until = q.get('dnd_until')
    return until == FOREVER or (until is not None and (now if now is not None else time.time()) < until)


def view(q, now=None):
    now = now if now is not None else time.time()
    on = dnd_active(q, now)
    until = q.get('dnd_until')
    text = ''
    if on and until == FOREVER:
        text = '一直'
    elif on:
        at = datetime.datetime.fromtimestamp(until)
        day = '' if at.date() == datetime.datetime.fromtimestamp(now).date() else '明天 '
        text = f'到 {day}{at:%H:%M}'
    return {'mute': q['mute'], 'lock': q['lock'], 'alarms': q['alarms'], 'dnd': on,
            'dnd_until': until if on else None, 'dnd_text': text}


def describe(q, now=None):
    """给语音回答的一句话。"""
    v = view(q, now)
    parts = ['静音中' + ('（锁住了）' if v['lock'] else '') if v['mute'] else '没静音',
             f"勿扰{v['dnd_text']}" if v['dnd'] else '没开勿扰']
    if v['mute'] or v['dnd']:
        parts.append('闹钟照响' if v['alarms'] else '闹钟也不响')
    return '，'.join(parts)
