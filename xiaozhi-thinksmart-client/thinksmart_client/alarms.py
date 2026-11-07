"""Countdowns, alarms and reminders, all kept and rung on the device (the user's plan, 2026-11-03).

The model only understands what was said and calls a device tool (mcp.py) with the intent: a
duration, or a clock time with a date, a repeat rule, what it is about. The time point is worked out
here and given back for the model to repeat. (No local parsing of the words: rules miss phrasings and
set wrong alarms.)

One kind of entry for all three; the kind only decides what is shown and what happens when it is due.
Each keeps its next time ("at"), an optional snooze time hung on it, what it is about and a repeat
rule. One scheduler (app.py) rings whatever is due first. A one-off entry stays (without a next time)
for SNOOZE_WINDOW after it rang, so "再睡 10 分钟" can still find it, then goes; a repeating one gets
its next time written back.

The device has no clock battery: until the system clock is synchronised nothing is judged due
(clock_synced). Due longer ago than LATE when first seen means the device was off (or the client
down): under CATCH_UP it rings once, saying which one was missed; older is only noted as missed.

Saved on every change (create, delete, snooze, rung) with a temporary file, fsync and rename: the
device is often unplugged without shutting down, and a file written in place has come back as zeros.
"""
import calendar
import datetime
import json
import logging
import os
import pathlib
import re
import time

import numpy as np

from . import opus
from .audio import OUT_RATE

log = logging.getLogger(__name__)

DIR = pathlib.Path.home() / '.local' / 'share' / 'thinksmart-client' / 'alarms'
TIMESYNC = pathlib.Path('/run/systemd/timesync/synchronized')   # systemd-timesyncd, removed at every boot

KINDS = {'timer': '倒计时', 'alarm': '闹钟', 'reminder': '提醒'}
REPEATS = ('daily', 'workdays', 'weekly', 'monthly')
MAX_ITEMS = 100
MAX_MISSED = 20
MISSED_DAYS = 7
LATE = 60                 # seconds: due longer ago than this when seen means it was missed
CATCH_UP = 3600           # missed by less than this (device off): ring once on start, saying so
RING_SECONDS = 600        # an alarm or countdown nobody stops: give up after 10 minutes, noted as missed
REMINDER_SECONDS = 180    # a reminder: chime + its words over and over for 3 minutes, then noted as missed
REMINDER_PAUSE = 5        # seconds between those rounds
SNOOZE_WINDOW = 900       # "再睡" finds an entry ringing or rung this recently
SNOOZE_MINUTES = 10
MAX_AHEAD = 366 * 86400   # nothing further ahead than a year
TIME = re.compile(r'^([01]?\d|2[0-3]):([0-5]\d)$')
WEEKDAY = '一二三四五六日'


def clock_synced():
    """The system clock has been set from the network since this boot. Only read, never set."""
    if TIMESYNC.parent.exists():
        return TIMESYNC.exists()
    return True   # no timesyncd (a desktop under test, another time daemon): trust the clock


def write_atomic(path, data):
    """Replace path with data so that a power cut leaves either the old file or the new one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    with open(tmp, 'wb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    try:
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)   # the rename itself
        finally:
            os.close(fd)
    except OSError:
        pass


# ---- repeat rules and time points ----

def normalize_repeat(rule):
    """None, or {'type': 'daily'|'workdays'} / {'type': 'weekly', 'days': [1-7]} / {'type': 'monthly', 'day': 1-31}.
    Raises ValueError."""
    if not rule:
        return None
    kind = rule.get('type')
    if kind in ('daily', 'workdays'):
        return {'type': kind}
    if kind == 'weekly':
        days = sorted({int(d) for d in rule.get('days') or []})
        if not days or any(d < 1 or d > 7 for d in days):
            raise ValueError('每周循环要说是周几（1 是周一，7 是周日）')
        return {'type': 'weekly', 'days': days}
    if kind == 'monthly':
        day = int(rule.get('day') or 0)
        if not 1 <= day <= 31:
            raise ValueError('每月循环要说是几号（1 到 31）')
        return {'type': 'monthly', 'day': day}
    raise ValueError(f'不认识的循环方式：{kind}')


def parse_time(hhmm):
    m = TIME.match(str(hhmm or '').strip())
    if not m:
        raise ValueError('时间要是 24 小时制的 HH:MM，比如 07:00、19:30')
    return int(m.group(1)), int(m.group(2))


def _on_day(rule, date):
    kind = rule['type']
    if kind == 'daily':
        return True
    if kind == 'workdays':
        return date.isoweekday() <= 5
    if kind == 'weekly':
        return date.isoweekday() in rule['days']
    return False


def next_time(rule, hhmm, after):
    """The first time after `after` (a datetime) that the rule rings at hhmm. A monthly day the month
    doesn't have (31st, 29th of February) rings on that month's last day."""
    h, m = parse_time(hhmm)
    if rule['type'] == 'monthly':
        year, month = after.year, after.month
        for _ in range(14):
            day = min(rule['day'], calendar.monthrange(year, month)[1])
            t = datetime.datetime(year, month, day, h, m)
            if t > after:
                return t
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    else:
        for i in range(9):
            date = after.date() + datetime.timedelta(days=i)
            t = datetime.datetime.combine(date, datetime.time(h, m))
            if t > after and _on_day(rule, date):
                return t
    raise ValueError('算不出下一次的时间')


def plan(now, hours=None, minutes=None, seconds=None, time=None, date=None, days_from_today=None, repeat=None):
    """The first time point (epoch seconds) for what the model passed. now: epoch seconds.
    Raises ValueError with a sentence for the model."""
    now_dt = datetime.datetime.fromtimestamp(now)
    relative = [v for v in (hours, minutes, seconds) if v is not None]
    if relative:
        if time is not None or repeat:
            raise ValueError('相对时长（多久以后）和钟点、循环不能同时用')
        total = float(hours or 0) * 3600 + float(minutes or 0) * 60 + float(seconds or 0)
        if total < 1:
            raise ValueError('时长要大于 0')
        at = now + total
    elif time is not None:
        h, m = parse_time(time)
        if repeat:
            if date is not None or days_from_today is not None:
                raise ValueError('循环的闹钟不用传日期')
            return next_time(repeat, time, now_dt).timestamp()
        if date is not None:
            try:
                day = datetime.date.fromisoformat(str(date))
            except ValueError:
                raise ValueError('日期要是 YYYY-MM-DD') from None
        elif days_from_today is not None:
            day = now_dt.date() + datetime.timedelta(days=int(days_from_today))
        else:
            day = now_dt.date()
            if datetime.datetime.combine(day, datetime.time(h, m)) <= now_dt:
                day += datetime.timedelta(days=1)   # "7 点叫我" said after 7: tomorrow's
        at = datetime.datetime.combine(day, datetime.time(h, m)).timestamp()
        if at <= now:
            raise ValueError(f'{day.month}月{day.day}日 {h:02d}:{m:02d} 已经过去了')
    else:
        raise ValueError('要么传多久以后（hours/minutes/seconds），要么传钟点 time')
    if at - now > MAX_AHEAD:
        raise ValueError('最多只能设一年以内的')
    return at


# ---- words for the page and the model ----

def describe_repeat(rule):
    if not rule:
        return ''
    kind = rule['type']
    if kind == 'daily':
        return '每天'
    if kind == 'workdays':
        return '每个工作日（周一到周五）'
    if kind == 'weekly':
        return '每周' + '、'.join(WEEKDAY[d - 1] for d in rule['days'])
    return f"每月 {rule['day']} 号" + ('（没有这一天的月份在最后一天）' if rule['day'] > 28 else '')


def describe_when(at, now):
    t, today = datetime.datetime.fromtimestamp(at), datetime.datetime.fromtimestamp(now).date()
    days = (t.date() - today).days
    day = {0: '今天', 1: '明天', 2: '后天'}.get(days)
    full = f'{t.month}月{t.day}日 星期{WEEKDAY[t.weekday()]}'
    return f'{day}（{full}）{t:%H:%M}' if day else f'{full} {t:%H:%M}'


def describe_in(at, now):
    s = max(0, round(at - now))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    parts = [f'{d} 天'] * bool(d) + [f'{h} 小时'] * bool(h) + [f'{m} 分钟'] * bool(m)
    if not parts or (not d and not h and m < 5 and s):
        parts.append(f'{s} 秒')
    return ''.join(parts) + '后'


def due(item):
    times = [t for t in (item.get('at'), item.get('snooze')) if t is not None]
    return min(times) if times else None


# ---- the entries ----

class Alarms:
    def __init__(self, directory=None, clock=time.time):
        self.dir = pathlib.Path(directory or DIR)
        self.path = self.dir / 'alarms.json'
        self.clock = clock
        self.items = []
        self.missed = []
        self.next_id = 1
        self.load()

    def load(self):
        try:
            data = json.loads(self.path.read_text())
            self.items = [i for i in data.get('items') or [] if isinstance(i, dict) and i.get('kind') in KINDS]
            self.missed = list(data.get('missed') or [])
            self.next_id = int(data.get('next_id') or 1)
        except FileNotFoundError:
            pass
        except (ValueError, TypeError, AttributeError, UnicodeDecodeError):
            # can't happen with write_atomic, unless the storage itself broke: keep it for a look
            log.error('%s is unreadable, starting with no alarms (kept as .bad)', self.path)
            try:
                os.replace(self.path, self.path.with_name('alarms.json.bad'))
            except OSError:
                pass
        self.next_id = max([self.next_id] + [int(i.get('id', 0)) + 1 for i in self.items])

    def save(self):
        data = {'version': 1, 'next_id': self.next_id, 'items': self.items, 'missed': self.missed}
        write_atomic(self.path, json.dumps(data, ensure_ascii=False, indent=1).encode())

    def clip_path(self, item):
        return self.dir / 'clips' / f"{item['id']}.wav"

    def get(self, item_id):
        return next((i for i in self.items if i['id'] == item_id), None)

    def active(self):
        """Entries that will ring, soonest first."""
        return sorted((i for i in self.items if due(i) is not None), key=due)

    def create(self, kind, label='', say='', repeat=None, **when):
        if kind not in KINDS:
            raise ValueError('kind 只能是 timer、alarm 或 reminder')
        if len(self.active()) >= MAX_ITEMS:
            raise ValueError(f'最多同时 {MAX_ITEMS} 条，先删掉一些')
        now = self.clock()
        repeat = normalize_repeat(repeat)
        at = plan(now, repeat=repeat, **when)
        item = {'id': self.next_id, 'kind': kind, 'label': str(label or '').strip()[:60],
                'say': str(say or '').strip()[:200], 'repeat': repeat,
                'time': datetime.datetime.fromtimestamp(at).strftime('%H:%M') if repeat else None,
                'at': at, 'snooze': None, 'rang': None, 'created': now, 'clip': None}
        self.next_id += 1
        self.items.append(item)
        self.save()
        log.info('alarm %d set: %s %s at %s %s', item['id'], kind, item['label'],
                 datetime.datetime.fromtimestamp(at), describe_repeat(repeat))
        return item

    def remove(self, item_id):
        item = self.get(item_id)
        if item is None:
            return None
        self.items.remove(item)
        self._drop_clip(item)
        self.save()
        log.info('alarm %d deleted', item_id)
        return item

    def _drop_clip(self, item):
        try:
            self.clip_path(item).unlink()
        except FileNotFoundError:
            pass
        except OSError as e:
            log.warning('cannot delete %s: %s', self.clip_path(item), e)

    def set_clip(self, item_id, wav):
        """The spoken reminder, synthesised by the hub when it was set (None: that failed)."""
        item = self.get(item_id)
        if item is None:
            return False   # deleted meanwhile
        if wav:
            write_atomic(self.clip_path(item), wav)
        item['clip'] = bool(wav)
        self.save()
        return True

    def take_due(self, now, ringing=()):
        """What to ring now (besides what is `ringing` already): (entries, notes); notes are the
        "错过了 7:00 的闹钟" ones (missed while off by under CATCH_UP). Missed by more: noted, and
        moved on to their next time."""
        ring, notes, changed = [], [], False
        for item in list(self.items):
            t = due(item)
            if t is None or t > now or item in ringing:
                continue
            late = now - t
            if late <= LATE:
                ring.append(item)
            elif late <= CATCH_UP:
                ring.append(item)
                notes.append(f"错过了 {datetime.datetime.fromtimestamp(t):%H:%M} 的{KINDS[item['kind']]}")
            else:
                self._note_missed(item, t, 'off', now)
                self._advance(item, now, now)
                changed = True
        if changed:
            self._purge(now)
            self.save()
        return ring, notes

    def _advance(self, item, fired_before, now):
        """After ringing (or being missed): clear what fired; a repeating entry gets its next time."""
        if item.get('snooze') is not None and item['snooze'] <= fired_before:
            item['snooze'] = None
        if item.get('at') is not None and item['at'] <= fired_before:
            item['at'] = (next_time(item['repeat'], item['time'], datetime.datetime.fromtimestamp(now)).timestamp()
                          if item['repeat'] else None)

    def finish(self, items, started, answered, now=None, fired=None):
        """A ring is over: stopped by the user (answered) or given up on after RING_SECONDS. fired:
        item id -> when it began ringing, for those that joined the ring after `started` (what came
        due up to that moment is what rang)."""
        now = self.clock() if now is None else now
        fired = fired or {}
        for item in items:
            if item not in self.items:
                continue   # deleted while it rang
            if not answered:
                self._note_missed(item, due(item) or started, 'unanswered', now)
            self._advance(item, max(started, fired.get(item['id'], started)), now)
            item['rang'] = now
        self._purge(now)
        self.save()

    def snooze(self, minutes, now=None, ringing=()):
        """Hang a new ring time on the entry ringing now or rung within SNOOZE_WINDOW (the latest);
        None when there is none."""
        now = self.clock() if now is None else now
        found = [i for i in ringing if i in self.items] or sorted(
            (i for i in self.items if i.get('rang') and now - i['rang'] <= SNOOZE_WINDOW), key=lambda i: i['rang'])[-1:]
        for item in found:
            item['snooze'] = now + minutes * 60
        if found:
            self.save()
            log.info('snoozed %s for %d min', [i['id'] for i in found], minutes)
        return found

    def _note_missed(self, item, at, why, now):
        self.missed.append({'kind': item['kind'], 'label': item['label'], 'at': at, 'why': why, 'noted': now})

    def _purge(self, now):
        for item in list(self.items):
            if due(item) is None and not (item.get('rang') and now - item['rang'] <= SNOOZE_WINDOW):
                self.items.remove(item)
                self._drop_clip(item)
        self.missed = [m for m in self.missed if now - m['noted'] <= MISSED_DAYS * 86400][-MAX_MISSED:]

    def tidy(self, now=None):
        """Drop what has run out (rung one-offs past the snooze window, old missed notes)."""
        now = self.clock() if now is None else now
        before = (len(self.items), len(self.missed))
        self._purge(now)
        if (len(self.items), len(self.missed)) != before:
            self.save()

    def clear_missed(self):
        self.missed = []
        self.save()

    def view(self, item, now=None):
        """An entry as the page and the model see it."""
        now = self.clock() if now is None else now
        t = due(item)
        return {'id': item['id'], 'kind': item['kind'], 'type': KINDS[item['kind']], 'label': item['label'],
                'at': t, 'when': describe_when(t, now) if t else '', 'in': describe_in(t, now) if t else '',
                'repeat': describe_repeat(item['repeat']), 'snoozed': item.get('snooze') is not None,
                'spoken': item.get('clip')}


# ---- ring tones ----
# Four short piano pieces (originals) rendered from the Salamander Grand Piano samples (CC BY 3.0,
# Alexander Holm) by tools/ringtone/, which the user picked from 14 on 2026-11-04, and the marimba
# made here like the wake chime. Reminders ring with a short piano chord instead (reminder_chime).

SOUNDS = pathlib.Path(__file__).resolve().parent.parent / 'web' / 'sounds'
TONES = {'coffee': '咖啡时光', 'morning': '晨曦', 'bright': '元气早晨', 'hop': '阳光小跳跃', 'marimba': '清晨木琴'}
FILE_TONES = {'coffee': 'alarm-coffee.ogg', 'morning': 'alarm-morning-light.ogg', 'bright': 'alarm-bright-start.ogg',
              'hop': 'alarm-sunny-hop.ogg'}
DEFAULT_TONE = 'coffee'
CHIME_SPEECH_AT = 1.4   # seconds into the reminder chime its words begin, over the chord's ring
TONE_GAP = 1.2      # silence after each phrase: room for the wake word to be heard


def _env(n, attack=0.004):
    t = np.arange(n) / OUT_RATE
    return t, np.minimum(1, t / attack)


def _bar(f, dur=0.6):
    t, a = _env(int(OUT_RATE * dur), 0.0015)
    return a * (np.sin(2 * np.pi * f * t) * np.exp(-6 * t) + 0.35 * np.sin(2 * np.pi * 4 * f * t) * np.exp(-26 * t)
                + 0.1 * np.sin(2 * np.pi * 9.9 * f * t) * np.exp(-40 * t))


def _place(parts, total):
    out = np.zeros(int(OUT_RATE * total))
    for at, x in parts:
        start = int(OUT_RATE * at)
        out[start:start + len(x)] += x[:len(out) - start]
    return out


def tone(name, peak=0.6):
    """One phrase of the ring tone, then TONE_GAP of silence, as 48 kHz stereo float32. Not cached: the
    piano piece is ~10 MB as float32, held only while it rings."""
    if name in FILE_TONES:
        try:
            x = load_sound(SOUNDS / FILE_TONES[name])
            return np.concatenate([x, np.zeros((int(OUT_RATE * TONE_GAP), 2), np.float32)])
        except (OSError, EOFError, ValueError, KeyError, IndexError, RuntimeError) as e:   # missing or broken: still ring
            log.error('ring tone %s unreadable (%s): the marimba instead', name, e)
    C6, E6, G6, C7 = 1046.5, 1318.5, 1568.0, 2093.0
    notes = [C6, E6, G6, C7, G6, E6, C6, G6]
    x = _place([(0.13 * i, _bar(f)) for i, f in enumerate(notes)], 1.6)
    x = np.concatenate([x * peak / np.max(np.abs(x)), np.zeros(int(OUT_RATE * TONE_GAP))]).astype(np.float32)
    return np.repeat(x[:, None], 2, axis=1)


def load_sound(path):
    """A tone file: Ogg Opus at 64 kbit/s (a tenth of the WAV it was made from, so each client update
    downloads ~0.7 MB of tones instead of ~3.2), as 48 kHz stereo float32."""
    x = opus.read_ogg(path)
    return np.repeat(x[:, None], 2, axis=1)


def reminder_chime():
    """A reminder's chime: a rolled piano chord (~3 s with its ring), its words start CHIME_SPEECH_AT in."""
    try:
        return load_sound(SOUNDS / 'alarm-chime.ogg')
    except (OSError, EOFError, ValueError, KeyError, IndexError, RuntimeError) as e:
        log.error('reminder chime unreadable (%s): the marimba instead', e)
        return tone('marimba')
