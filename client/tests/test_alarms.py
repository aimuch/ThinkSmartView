"""Countdowns, alarms and reminders: time points, repeats, the store on disk, missed ones, snooze, and the
client's ringing and volume handling, all with a fake clock.

Run like test_barge_in.py (same stubs).
"""
import asyncio
import datetime
import json
import os
import types

import numpy as np
import pytest

import test_barge_in
from thinksmart_client import alarms, app, config, mcp
from thinksmart_client.audio import OUT_RATE

# test_barge_in stubs thinksmart_client.opus (no libopus needed); the tone files need the real reader
try:
    import importlib.util
    _spec = importlib.util.spec_from_file_location('real_opus', app.ROOT / 'thinksmart_client' / 'opus.py')
    real_opus = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(real_opus)
    alarms.opus.read_ogg = real_opus.read_ogg
except OSError:   # no libopus here (on a Mac: DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib): a stand-in piece
    real_opus = None

    def _stand_in(path):
        open(path).close()   # a missing file still fails like the real reader
        return np.full(OUT_RATE * 3, 0.5, np.float32)
    alarms.opus.read_ogg = _stand_in


def ts(*args):
    return datetime.datetime(*args).timestamp()


def at(t):
    return datetime.datetime.fromtimestamp(t)


class Clock:
    def __init__(self, *start):
        self.t = ts(*start)

    def __call__(self):
        return self.t

    def go(self, seconds):
        self.t += seconds


# ---- time points ----

def test_relative_and_clock_times():
    now = ts(2026, 11, 3, 23, 50)
    assert alarms.plan(now, minutes=5) == now + 300
    assert alarms.plan(now, hours=1, minutes=30) == now + 5400
    assert alarms.plan(now, seconds=30) == now + 30
    # crossing midnight: 7:00 said at 23:50 is tomorrow's
    assert at(alarms.plan(now, time='07:00')) == datetime.datetime(2026, 11, 4, 7, 0)
    assert at(alarms.plan(now, time='23:55')) == datetime.datetime(2026, 11, 3, 23, 55)
    assert at(alarms.plan(now, time='7:05', days_from_today=2)) == datetime.datetime(2026, 11, 5, 7, 5)
    assert at(alarms.plan(now, time='08:00', date='2026-12-31')) == datetime.datetime(2026, 12, 31, 8, 0)
    assert at(alarms.plan(now, minutes=20)) == datetime.datetime(2026, 11, 4, 0, 10)


@pytest.mark.parametrize('kwargs', [
    {}, {'minutes': 0}, {'time': '25:00'}, {'time': '7点'}, {'time': '07:00', 'days_from_today': 0},
    {'time': '07:00', 'date': '2026-11-03'}, {'time': '07:00', 'date': '11/04'}, {'hours': 24 * 400},
    {'minutes': 5, 'time': '07:00'}, {'time': '07:00', 'date': '2027-12-01'},
])
def test_bad_times_are_refused_with_a_reason(kwargs):
    with pytest.raises(ValueError) as e:
        alarms.plan(ts(2026, 11, 3, 23, 50), **kwargs)
    assert str(e.value)


def test_repeats():
    R = alarms.normalize_repeat
    sun_night = datetime.datetime(2026, 11, 8, 22, 0)        # a Sunday
    assert alarms.next_time(R({'type': 'daily'}), '07:00', sun_night) == datetime.datetime(2026, 11, 9, 7, 0)
    fri = datetime.datetime(2026, 11, 6, 7, 0)               # Friday 7:00 just rang
    assert alarms.next_time(R({'type': 'workdays'}), '07:00', fri) == datetime.datetime(2026, 11, 9, 7, 0)
    assert alarms.next_time(R({'type': 'workdays'}), '07:00', fri - datetime.timedelta(seconds=1)) == fri
    weekly = R({'type': 'weekly', 'days': [5, 1, 3, 3]})
    assert weekly == {'type': 'weekly', 'days': [1, 3, 5]}
    assert alarms.next_time(weekly, '09:30', datetime.datetime(2026, 11, 4, 9, 30)) == datetime.datetime(2026, 11, 6, 9, 30)
    assert alarms.next_time(weekly, '09:30', datetime.datetime(2026, 11, 6, 10, 0)) == datetime.datetime(2026, 11, 9, 9, 30)
    sunday = R({'type': 'weekly', 'days': [7]})
    assert alarms.next_time(sunday, '10:00', datetime.datetime(2026, 11, 8, 10, 0)) == datetime.datetime(2026, 11, 15, 10, 0)


def test_monthly_on_a_day_the_month_lacks_rings_on_its_last_day():
    m31 = alarms.normalize_repeat({'type': 'monthly', 'day': 31})
    t = datetime.datetime(2026, 10, 31, 8, 0)
    seen = []
    for _ in range(5):
        t = alarms.next_time(m31, '08:00', t)
        seen.append(t.date())
    assert seen == [datetime.date(2026, 11, 30), datetime.date(2026, 12, 31), datetime.date(2027, 1, 31),
                    datetime.date(2027, 2, 28), datetime.date(2027, 3, 31)]
    m29 = alarms.normalize_repeat({'type': 'monthly', 'day': 29})
    assert alarms.next_time(m29, '08:00', datetime.datetime(2028, 2, 1)).date() == datetime.date(2028, 2, 29)   # leap
    # crossing the year
    m1 = alarms.normalize_repeat({'type': 'monthly', 'day': 1})
    assert alarms.next_time(m1, '00:00', datetime.datetime(2026, 12, 15)) == datetime.datetime(2027, 1, 1)


@pytest.mark.parametrize('rule', [{'type': 'weekly'}, {'type': 'weekly', 'days': [0]}, {'type': 'monthly', 'day': 32},
                                  {'type': 'yearly'}])
def test_bad_repeats(rule):
    with pytest.raises(ValueError):
        alarms.normalize_repeat(rule)


def test_words():
    now = ts(2026, 11, 3, 23, 50)
    assert alarms.describe_when(ts(2026, 11, 4, 7, 0), now) == '明天（11月4日 星期三）07:00'
    assert alarms.describe_when(ts(2026, 11, 20, 7, 0), now) == '11月20日 星期五 07:00'
    assert alarms.describe_in(now + 300, now) == '5 分钟后'
    assert alarms.describe_in(now + 90, now) == '1 分钟30 秒后'
    assert alarms.describe_in(now + 7 * 3600 + 10 * 60, now) == '7 小时10 分钟后'
    assert alarms.describe_repeat({'type': 'weekly', 'days': [1, 3, 5]}) == '每周一、三、五'
    assert '最后一天' in alarms.describe_repeat({'type': 'monthly', 'day': 31})
    assert alarms.describe_repeat({'type': 'workdays'}).startswith('每个工作日')


# ---- the store ----

def test_store_survives_a_restart(tmp_path):
    clock = Clock(2026, 11, 3, 22, 0)
    a = alarms.Alarms(tmp_path, clock)
    one = a.create('alarm', '起床', time='07:00')
    rep = a.create('alarm', '上班', time='07:30', repeat={'type': 'workdays'})
    b = alarms.Alarms(tmp_path, clock)
    assert [i['id'] for i in b.active()] == [one['id'], rep['id']]
    assert b.get(rep['id'])['repeat'] == {'type': 'workdays'} and b.get(rep['id'])['time'] == '07:30'
    assert b.create('timer', minutes=3)['id'] == rep['id'] + 1   # ids keep counting


def test_power_cut_while_writing_keeps_the_old_file(tmp_path):
    clock = Clock(2026, 11, 3, 22, 0)
    a = alarms.Alarms(tmp_path, clock)
    a.create('alarm', '起床', time='07:00')
    # cut while writing the next version: only its temporary file is half written
    (tmp_path / 'alarms.json.tmp').write_bytes(b'{"items": [{"id": 9')
    b = alarms.Alarms(tmp_path, clock)
    assert [i['label'] for i in b.items] == ['起床']


def test_a_zeroed_file_is_set_aside_not_fatal(tmp_path):
    (tmp_path / 'alarms.json').write_bytes(b'\0' * 512)
    a = alarms.Alarms(tmp_path, Clock(2026, 11, 3, 22, 0))
    assert a.items == [] and (tmp_path / 'alarms.json.bad').exists()
    a.create('timer', minutes=1)
    assert json.loads((tmp_path / 'alarms.json').read_text())['items'][0]['kind'] == 'timer'


def test_write_atomic_syncs_before_renaming(tmp_path, monkeypatch):
    calls = []
    real_fsync, real_replace = os.fsync, os.replace
    monkeypatch.setattr(os, 'fsync', lambda fd: (calls.append('fsync'), real_fsync(fd)))
    monkeypatch.setattr(os, 'replace', lambda a, b: (calls.append('rename'), real_replace(a, b)))
    alarms.write_atomic(tmp_path / 'x.json', b'{}')
    assert calls == ['fsync', 'rename', 'fsync'] and (tmp_path / 'x.json').read_bytes() == b'{}'


def test_at_most_100(tmp_path):
    a = alarms.Alarms(tmp_path, Clock(2026, 11, 3, 22, 0))
    for i in range(alarms.MAX_ITEMS):
        a.items.append({'id': i, 'kind': 'timer', 'label': '', 'repeat': None, 'at': a.clock() + 60 + i, 'snooze': None})
    with pytest.raises(ValueError, match='100'):
        a.create('timer', minutes=1)


def test_deleting_removes_its_clip(tmp_path):
    a = alarms.Alarms(tmp_path, Clock(2026, 11, 3, 22, 0))
    item = a.create('reminder', '晾衣服', '该晾衣服了', minutes=10)
    assert a.set_clip(item['id'], b'RIFF....')
    assert a.clip_path(item).exists() and a.get(item['id'])['clip']
    a.remove(item['id'])
    assert not a.clip_path(item).exists() and a.items == []
    assert not a.set_clip(item['id'], b'RIFF')   # deleted while the hub was synthesising: dropped


# ---- due, missed, snooze ----

def test_one_off_rings_then_goes_after_the_snooze_window(tmp_path):
    clock = Clock(2026, 11, 3, 22, 0)
    a = alarms.Alarms(tmp_path, clock)
    item = a.create('timer', minutes=5)
    assert a.take_due(clock()) == ([], [])
    clock.go(300)
    ring, notes = a.take_due(clock())
    assert ring == [item] and notes == []
    assert a.take_due(clock(), ringing=ring) == ([], [])    # already ringing
    started = clock()
    clock.go(20)
    a.finish(ring, started, answered=True)
    assert a.active() == [] and a.items == [item]           # kept for "再睡"
    clock.go(alarms.SNOOZE_WINDOW + 1)
    a.tidy()
    assert a.items == [] and a.missed == []


def test_repeating_alarm_gets_its_next_time(tmp_path):
    clock = Clock(2026, 11, 6, 6, 0)    # a Friday
    a = alarms.Alarms(tmp_path, clock)
    item = a.create('alarm', '上班', time='07:00', repeat={'type': 'workdays'})
    clock.t = ts(2026, 11, 6, 7, 0)
    ring, _ = a.take_due(clock())
    a.finish(ring, clock(), answered=True, now=clock() + 30)
    assert at(item['at']) == datetime.datetime(2026, 11, 9, 7, 0)   # Monday
    assert alarms.Alarms(tmp_path, clock).get(item['id'])['at'] == item['at']


def test_unanswered_ring_is_noted_as_missed(tmp_path):
    clock = Clock(2026, 11, 3, 6, 59)
    a = alarms.Alarms(tmp_path, clock)
    a.create('alarm', '起床', time='07:00')
    clock.go(60)
    ring, _ = a.take_due(clock())
    a.finish(ring, clock(), answered=False, now=clock() + alarms.RING_SECONDS)
    assert [(m['label'], m['why']) for m in a.missed] == [('起床', 'unanswered')]


def test_missed_while_off_under_an_hour_rings_once_saying_so(tmp_path):
    clock = Clock(2026, 11, 3, 6, 0)
    a = alarms.Alarms(tmp_path, clock)
    a.create('alarm', '起床', time='07:00')
    rep = a.create('alarm', '吃药', time='07:10', repeat={'type': 'daily'})
    clock.t = ts(2026, 11, 3, 7, 40)    # the device was off from 6:30 to 7:39
    b = alarms.Alarms(tmp_path, clock)
    ring, notes = b.take_due(clock())
    assert sorted(i['label'] for i in ring) == ['吃药', '起床']
    assert notes == ['错过了 07:00 的闹钟', '错过了 07:10 的闹钟']
    b.finish(ring, clock(), answered=True)
    assert at(b.get(rep['id'])['at']) == datetime.datetime(2026, 11, 4, 7, 10)   # the next one as usual
    assert b.missed == []


def test_missed_while_off_longer_is_only_noted(tmp_path):
    clock = Clock(2026, 11, 3, 6, 0)
    a = alarms.Alarms(tmp_path, clock)
    one = a.create('reminder', '开会', time='07:00')
    rep = a.create('alarm', '上班', time='07:00', repeat={'type': 'daily'})
    a.set_clip(one['id'], b'RIFF')
    clock.t = ts(2026, 11, 3, 9, 0)
    b = alarms.Alarms(tmp_path, clock)
    assert b.take_due(clock()) == ([], [])
    assert [(m['label'], m['why'], at(m['at']).hour) for m in b.missed] == [('开会', 'off', 7), ('上班', 'off', 7)]
    assert b.get(one['id']) is None and not b.clip_path(one).exists()
    assert at(b.get(rep['id'])['at']) == datetime.datetime(2026, 11, 4, 7, 0)
    assert alarms.Alarms(tmp_path, clock).missed == b.missed     # saved


def test_missed_notes_keep_20_for_7_days(tmp_path):
    clock = Clock(2026, 11, 3, 6, 0)
    a = alarms.Alarms(tmp_path, clock)
    for i in range(25):
        a.missed.append({'kind': 'alarm', 'label': str(i), 'at': clock() - 10, 'why': 'off', 'noted': clock() - 10})
    a.missed.insert(0, {'kind': 'alarm', 'label': 'old', 'at': 0, 'why': 'off', 'noted': clock() - 8 * 86400})
    a.tidy()
    assert len(a.missed) == 20 and a.missed[-1]['label'] == '24' and 'old' not in [m['label'] for m in a.missed]


def test_snooze_ringing_or_within_15_minutes(tmp_path):
    clock = Clock(2026, 11, 3, 6, 59)
    a = alarms.Alarms(tmp_path, clock)
    item = a.create('alarm', '起床', time='07:00')
    assert a.snooze(10) == []                         # nothing has rung
    clock.go(60)
    ring, _ = a.take_due(clock())
    started = clock()
    clock.go(30)
    a.finish(ring, started, answered=True)            # stopped with the wake word
    clock.go(14 * 60)                                  # "再睡 10 分钟", 14 minutes later
    assert a.snooze(10) == [item]
    assert at(alarms.due(item)) == at(clock() + 600) and item['at'] is None and item['label'] == '起床'
    assert alarms.Alarms(tmp_path, clock).get(item['id'])['snooze'] == item['snooze']
    clock.go(600)
    ring, _ = a.take_due(clock())
    assert ring == [item]
    a.finish(ring, clock(), answered=True)
    assert alarms.due(item) is None
    clock.go(alarms.SNOOZE_WINDOW + 1)
    assert a.snooze(10) == []                          # too long ago
    a.tidy()
    assert a.items == []


def test_snooze_on_a_repeating_alarm_keeps_its_rule(tmp_path):
    clock = Clock(2026, 11, 3, 6, 59)
    a = alarms.Alarms(tmp_path, clock)
    item = a.create('alarm', '上班', time='07:00', repeat={'type': 'daily'})
    clock.go(60)
    ring, _ = a.take_due(clock())
    assert a.snooze(10, ringing=ring) == [item]
    a.finish(ring, clock(), answered=True)
    assert at(item['at']) == datetime.datetime(2026, 11, 4, 7, 0) and at(item['snooze']) == datetime.datetime(2026, 11, 3, 7, 10)
    clock.t = item['snooze']
    ring, _ = a.take_due(clock())
    a.finish(ring, clock(), answered=True)
    assert item['snooze'] is None and at(item['at']) == datetime.datetime(2026, 11, 4, 7, 0)
    a.remove(item['id'])                               # deleting it takes the snooze with it
    assert a.active() == []


def test_snooze_outside_the_window_after_a_snooze_is_gone(tmp_path):
    clock = Clock(2026, 11, 3, 6, 59)
    a = alarms.Alarms(tmp_path, clock)
    a.create('alarm', time='07:00')
    clock.go(60)
    ring, _ = a.take_due(clock())
    a.finish(ring, clock(), answered=True)
    clock.go(16 * 60)
    assert a.snooze(10) == []


def test_tones():
    assert alarms.DEFAULT_TONE in alarms.TONES
    for name in alarms.TONES:
        x = alarms.tone(name)
        assert x.dtype == np.float32 and x.shape[1] == 2
        assert 0.3 < np.abs(x).max() <= 0.8 + 1e-3
        gap = x[-int(OUT_RATE * alarms.TONE_GAP):]
        assert not gap.any()   # silence for the wake word after each phrase
        assert 1.5 < len(x) / OUT_RATE < (35 if name in alarms.FILE_TONES else 4)


# ---- the client: ringing, volume ----

class Speaker:
    """wpctl: the sink volume."""

    def __init__(self, monkeypatch, volume=40):
        self.volume = volume
        self.history = []

        async def get_volume():
            return self.volume

        async def set_volume(v):
            self.volume = max(0, min(100, int(v)))
            self.history.append(self.volume)
        monkeypatch.setattr(mcp, 'get_volume', get_volume)
        monkeypatch.setattr(mcp, 'set_volume', set_volume)


def make_client(tmp_path, monkeypatch, clock=None):
    monkeypatch.setattr(config, 'save', lambda **kw: None)
    c = test_barge_in.make_client()
    c.play = lambda name: None
    c.cfg['hub_http'] = 'http://127.0.0.1:9'
    c.alarms = alarms.Alarms(tmp_path, clock or Clock(2026, 11, 3, 6, 59))
    c.clock_ok = True
    c.volume_lock = asyncio.Lock()
    c.held_volume = None
    c.preview = None
    c.alarm_volume, c.alarm_tone = 70, 'marimba'
    c.screen = types.SimpleNamespace(hold=lambda: None, release=lambda: None, poke=lambda: None, ringing=False,
                                     level=100, dimmed=False)
    c.screen.alarm = lambda on: setattr(c.screen, 'ringing', on)
    monkeypatch.setattr(alarms, 'clock_synced', lambda: True)
    monkeypatch.setattr(app.time, 'time', lambda: c.alarms.clock())
    return c


def events(c):
    seen = []
    orig = c.events.publish
    c.events.publish = lambda e, d: (seen.append((e, d)), orig(e, d))
    return seen


async def ring_now(c):
    c.alarms.clock.go(60)
    await c._alarm_tick(c.alarms.clock())
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_ring_switches_to_the_alarm_volume_and_back(tmp_path, monkeypatch):
    sp = Speaker(monkeypatch, 40)
    c = make_client(tmp_path, monkeypatch)
    c.alarms.create('alarm', '起床', time='07:00')
    await ring_now(c)
    assert c.ring and c.screen.ringing and sp.volume == 70
    assert json.loads((tmp_path / 'ring.json').read_text()) == {'volume': 40}   # on disk before switching
    assert c.audio.player.buffered() > 1                                        # the tone is playing
    assert await c.stop_alarm()
    assert c.ring is None and not c.screen.ringing and sp.volume == 40 and not (tmp_path / 'ring.json').exists()
    assert c.audio.player.buffered() < 0.01
    assert c.alarms.active() == [] and c.alarms.missed == []


@pytest.mark.asyncio
async def test_volume_keys_during_a_ring_change_only_that_ring(tmp_path, monkeypatch):
    sp = Speaker(monkeypatch, 40)
    c = make_client(tmp_path, monkeypatch)
    seen = events(c)
    c.alarms.create('alarm', time='07:00')
    await ring_now(c)
    await c.volume_step(5)
    await c.volume_step(5)
    assert sp.volume == 80 and c.alarm_volume == 70
    assert ('volume', {'volume': 80, 'source': 'key', 'ring': True}) in seen
    await c.stop_alarm()
    assert sp.volume == 40 and c.volume == 40 and c.alarm_volume == 70
    await c.volume_step(-5)                                   # after the ring: the device volume again
    assert sp.volume == 35 and ('volume', {'volume': 35, 'source': 'key'}) in seen


@pytest.mark.asyncio
async def test_device_volume_set_during_a_ring_is_what_comes_back(tmp_path, monkeypatch):
    sp = Speaker(monkeypatch, 40)
    c = make_client(tmp_path, monkeypatch)
    c.alarms.create('alarm', time='07:00')
    await ring_now(c)
    assert await c.set_volume(55, 'voice') == 55
    assert sp.volume == 70 and await c.device_volume() == 55
    assert json.loads((tmp_path / 'ring.json').read_text()) == {'volume': 55}
    await c.stop_alarm()
    assert sp.volume == 55


@pytest.mark.asyncio
async def test_volume_put_back_after_a_crash_mid_ring(tmp_path, monkeypatch):
    sp = Speaker(monkeypatch, 70)                 # left at the alarm volume
    (tmp_path / 'ring.json').write_text('{"volume": 35}')
    c = make_client(tmp_path, monkeypatch)
    await c._restore_volume()
    assert sp.volume == 35 and c.volume == 35 and not (tmp_path / 'ring.json').exists()
    sp.history.clear()
    await c._restore_volume()                     # a normal start: nothing to put back
    assert sp.history == [] and c.volume == 35


@pytest.mark.asyncio
async def test_ring_gives_up_after_10_minutes_and_notes_it(tmp_path, monkeypatch):
    sp = Speaker(monkeypatch, 40)
    monkeypatch.setattr(alarms, 'RING_SECONDS', 0.3)
    c = make_client(tmp_path, monkeypatch)
    c.alarms.create('alarm', '起床', time='07:00')
    await ring_now(c)
    await asyncio.sleep(0.5)
    assert c.ring is None and sp.volume == 40
    assert [(m['label'], m['why']) for m in c.alarms.missed] == [('起床', 'unanswered')]


@pytest.mark.asyncio
async def test_wake_word_stops_the_ring_then_snooze_by_voice(tmp_path, monkeypatch):
    Speaker(monkeypatch, 40)
    c = make_client(tmp_path, monkeypatch)
    item = c.alarms.create('alarm', '起床', time='07:00')
    await ring_now(c)
    await c.wake('voice')
    assert c.ring is None and c.state == 'listening'
    reply = await mcp.handle({'id': 1, 'method': 'tools/call',
                              'params': {'name': 'self.alarm.snooze', 'arguments': {'minutes': 10}}}, client=c)
    out = json.loads(reply['result']['content'][0]['text'])
    assert out['success'] and out['snoozed'][0]['label'] == '起床'
    assert alarms.due(c.alarms.get(item['id'])) == c.alarms.clock() + 600


@pytest.mark.asyncio
async def test_snooze_button_and_no_snooze_without_a_recent_ring(tmp_path, monkeypatch):
    Speaker(monkeypatch, 40)
    c = make_client(tmp_path, monkeypatch)
    reply = await mcp.handle({'id': 1, 'method': 'tools/call', 'params': {'name': 'self.alarm.snooze', 'arguments': {}}},
                             client=c)
    out = json.loads(reply['result']['content'][0]['text'])
    assert not out['success'] and '10 分钟后' in out['suggest']
    item = c.alarms.create('timer', minutes=1)
    await ring_now(c)
    assert [v['id'] for v in await c.snooze_alarm()] == [item['id']]
    assert c.ring is None and alarms.due(c.alarms.get(item['id'])) == c.alarms.clock() + 600


@pytest.mark.asyncio
async def test_a_ring_stops_a_reply(tmp_path, monkeypatch):
    Speaker(monkeypatch, 40)
    c = make_client(tmp_path, monkeypatch)
    c.alarms.create('alarm', time='07:00')
    await test_barge_in.ask(c)
    assert c.state == 'speaking'
    await ring_now(c)
    assert c.state == 'idle' and ('abort', None) in c.hub.kinds() and not c.uploading
    await c.stop_alarm()


@pytest.mark.asyncio
async def test_nothing_rings_before_the_clock_is_synchronised(tmp_path, monkeypatch):
    Speaker(monkeypatch, 40)
    c = make_client(tmp_path, monkeypatch)
    c.alarms.create('alarm', time='07:00')
    synced = [False]
    monkeypatch.setattr(alarms, 'clock_synced', lambda: synced[0])
    seen = events(c)
    await ring_now(c)
    assert c.ring is None and not c.clock_ok and seen[-1][1]['synced'] is False
    with pytest.raises(ValueError, match='同步|对上'):
        await c.create_alarm('timer', minutes=5)
    synced[0] = True
    await c._alarm_tick(c.alarms.clock())
    assert c.ring and c.clock_ok
    await c.stop_alarm()


@pytest.mark.asyncio
async def test_reminder_words_are_synthesised_once_and_played_each_cycle(tmp_path, monkeypatch):
    Speaker(monkeypatch, 40)
    c = make_client(tmp_path, monkeypatch)
    said = []

    async def speech(text):
        said.append(text)
        x = (np.sin(np.arange(24000) / 10) * 8000).astype(np.int16)
        import io
        import wave
        out = io.BytesIO()
        with wave.open(out, 'wb') as w:
            w.setnchannels(1), w.setsampwidth(2), w.setframerate(24000), w.writeframes(x.tobytes())
        return out.getvalue()
    c._speech = speech
    view = await c.create_alarm('reminder', '晾衣服', '该晾衣服了', minutes=1)
    await asyncio.sleep(0.05)
    assert said == ['该晾衣服了'] and c.alarms.get(view['id'])['clip']
    await ring_now(c)
    chime = len(alarms.reminder_chime())
    # a short piano chord with the words over its ring, then a pause (no long ring tone first)
    assert c.audio.player.buffered() * OUT_RATE >= chime + alarms.REMINDER_PAUSE * OUT_RATE
    assert c.audio.player.buffered() < 12
    await c.stop_alarm()
    assert said == ['该晾衣服了']                                  # not synthesised again


@pytest.mark.asyncio
async def test_reminder_without_words_still_rings(tmp_path, monkeypatch):
    Speaker(monkeypatch, 40)
    c = make_client(tmp_path, monkeypatch)

    async def speech(text):
        return None
    c._speech = speech
    view = await c.create_alarm('reminder', '开会', minutes=1)
    await asyncio.sleep(0.05)
    assert c.alarms.get(view['id'])['clip'] is False
    await ring_now(c)
    assert c.ring and c.ring['items'][0]['label'] == '开会'
    await c.stop_alarm()


@pytest.mark.asyncio
async def test_preview_plays_at_the_alarm_volume_and_puts_it_back(tmp_path, monkeypatch):
    sp = Speaker(monkeypatch, 40)
    c = make_client(tmp_path, monkeypatch)
    monkeypatch.setattr(alarms, 'TONE_GAP', 1.2)
    await c.preview_tone('coffee')
    await asyncio.sleep(0.05)
    assert sp.volume == 70 and c.held_volume == 40
    await c.set_volume(45)                       # the slider moved meanwhile
    await c.preview_tone('marimba')              # a second tap: the first one stops
    await asyncio.sleep(0.05)
    assert sp.volume == 70
    await c._stop_preview()
    assert sp.volume == 45 and c.held_volume is None
    with pytest.raises(ValueError):
        await c.preview_tone('siren')


# ---- the model's tools ----

async def call(c, name, **args):
    reply = await mcp.handle({'id': 1, 'method': 'tools/call', 'params': {'name': name, 'arguments': args}}, client=c)
    return reply['result']['isError'], reply['result']['content'][0]['text']


@pytest.mark.asyncio
async def test_tools_create_list_cancel(tmp_path, monkeypatch):
    Speaker(monkeypatch, 40)
    c = make_client(tmp_path, monkeypatch, Clock(2026, 11, 3, 23, 50))
    err, text = await call(c, 'self.alarm.create', kind='alarm', time='07:00', days_from_today=1, label='起床')
    out = json.loads(text)
    assert not err and out['when'] == '明天（11月4日 星期三）07:00' and out['in'] == '7 小时10 分钟后'
    err, text = await call(c, 'self.alarm.create', kind='alarm', time='07:30', repeat='weekly', weekdays=[1, 3, 5])
    assert json.loads(text)['repeat'] == '每周一、三、五'
    err, text = await call(c, 'self.alarm.create', kind='alarm', time='08:00', repeat='monthly', month_day=31)
    assert '每月 31 号' in json.loads(text)['repeat']
    err, text = await call(c, 'self.alarm.create', kind='timer', minutes=5)
    timer = json.loads(text)['id']
    err, text = await call(c, 'self.alarm.create', kind='alarm', time='07:00', days_from_today=0)
    assert err and '已经过去了' in text
    err, text = await call(c, 'self.alarm.list')
    listed = json.loads(text)['items']
    assert [i['type'] for i in listed] == ['倒计时', '闹钟', '闹钟', '闹钟'] and listed[0]['in'] == '5 分钟后'
    err, text = await call(c, 'self.alarm.list', kind='timer')
    assert len(json.loads(text)['items']) == 1
    err, text = await call(c, 'self.alarm.cancel', id=timer)
    assert json.loads(text)['cancelled'][0]['type'] == '倒计时'
    err, text = await call(c, 'self.alarm.cancel', id=999)
    assert not json.loads(text)['success']
    err, text = await call(c, 'self.alarm.cancel', all=True, kind='alarm')
    assert len(json.loads(text)['cancelled']) == 3 and c.alarms.active() == []


@pytest.mark.asyncio
async def test_tools_alarm_volume_and_device_volume(tmp_path, monkeypatch):
    sp = Speaker(monkeypatch, 40)
    c = make_client(tmp_path, monkeypatch)
    saved = {}
    monkeypatch.setattr(config, 'save', lambda **kw: saved.update(kw))
    seen = events(c)
    err, text = await call(c, 'self.alarm.set_volume', change=10)
    assert json.loads(text)['volume'] == 80 and saved == {'alarm_volume': 80} and sp.volume == 40
    err, text = await call(c, 'self.alarm.get_volume')
    assert json.loads(text) == {'volume': 80}
    err, text = await call(c, 'self.audio_speaker.set_volume', volume=30)
    assert sp.volume == 30 and ('volume', {'volume': 30, 'source': 'voice'}) in seen
    err, text = await call(c, 'self.audio_speaker.get_volume')
    assert json.loads(text) == {'volume': 30}


@pytest.mark.asyncio
async def test_reminder_rings_for_its_time_then_is_noted_missed(tmp_path, monkeypatch):
    sp = Speaker(monkeypatch, 40)
    c = make_client(tmp_path, monkeypatch)
    monkeypatch.setattr(alarms, 'REMINDER_PAUSE', 0)
    monkeypatch.setattr(alarms, 'REMINDER_SECONDS', 0.3)
    monkeypatch.setattr(alarms, 'reminder_chime', lambda: np.full((4800, 2), 0.1, np.float32))   # 0.1 s
    pushed = []
    orig = c.audio.player.push
    c.audio.player.push = lambda x: (pushed.append(len(x)), orig(x))
    c.alarms.create('reminder', '开会', minutes=1)
    await ring_now(c)
    await asyncio.sleep(0.8)
    assert c.ring is None and len(pushed) >= 1 and sp.volume == 40
    assert [(m['label'], m['why']) for m in c.alarms.missed] == [('开会', 'unanswered')]


def test_tone_choices():
    assert alarms.DEFAULT_TONE == 'coffee'
    assert list(alarms.TONES) == ['coffee', 'morning', 'bright', 'hop', 'marimba']
    for _name, f in alarms.FILE_TONES.items():
        assert (alarms.SOUNDS / f).exists(), f
    assert len(alarms.reminder_chime()) / OUT_RATE < 4


@pytest.mark.asyncio
async def test_closing_the_settings_stops_a_preview(tmp_path, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer
    from thinksmart_client import web as web_mod
    sp = Speaker(monkeypatch, 40)
    c = make_client(tmp_path, monkeypatch)
    async with TestClient(TestServer(web_mod.make_app(c))) as http:
        assert (await (await http.post('/api/alarm-tone', json={'tone': 'coffee', 'preview': True})).json())['ok']
        await asyncio.sleep(0.05)
        assert sp.volume == 70 and c.audio.player.buffered() > 1
        assert (await (await http.post('/api/alarm-tone', json={'stop': True})).json())['ok']
        assert sp.volume == 40 and c.audio.player.buffered() < 0.01 and c.preview is None


@pytest.mark.asyncio
async def test_a_broken_tone_file_still_rings(tmp_path, monkeypatch):
    monkeypatch.setattr(alarms, 'SOUNDS', tmp_path / 'nowhere')
    x = alarms.tone('coffee')
    assert len(x) / OUT_RATE < 4 and np.abs(x).max() > 0.5      # the marimba
    assert np.array_equal(alarms.reminder_chime(), alarms.tone('marimba'))


@pytest.mark.asyncio
async def test_a_failing_ring_does_not_break_the_wake_word(tmp_path, monkeypatch):
    sp = Speaker(monkeypatch, 40)
    c = make_client(tmp_path, monkeypatch)
    c.alarms.create('alarm', time='07:00')
    monkeypatch.setattr(alarms, 'tone', lambda name: 1 / 0)
    await ring_now(c)
    await c.wake('voice')                         # stop_alarm awaits the failed ring: no exception
    assert c.ring is None and sp.volume == 40 and c.state == 'listening'


@pytest.mark.asyncio
async def test_one_joining_a_ring_gets_its_full_time(tmp_path, monkeypatch):
    Speaker(monkeypatch, 40)
    c = make_client(tmp_path, monkeypatch)
    monkeypatch.setattr(alarms, 'REMINDER_PAUSE', 0)
    monkeypatch.setattr(alarms, 'REMINDER_SECONDS', 0.6)
    monkeypatch.setattr(alarms, 'reminder_chime', lambda: np.full((4800, 2), 0.1, np.float32))
    c.alarms.create('reminder', '喝水', seconds=60)
    c.alarms.create('reminder', '晾衣服', seconds=60.4)
    await ring_now(c)                                  # 喝水 rings (晾衣服 is 0.4 s later)
    assert [i['label'] for i in c.ring['items']] == ['喝水']
    await asyncio.sleep(0.4)
    c.alarms.clock.go(0.4)
    await c._alarm_tick(c.alarms.clock())              # 晾衣服 joins 0.4 s in
    assert [i['label'] for i in c.ring['items']] == ['喝水', '晾衣服']
    await asyncio.sleep(0.4)                           # 0.8 s: past 喝水's 0.6 s, inside 晾衣服's
    assert c.ring is not None
    await asyncio.sleep(0.5)
    assert c.ring is None and sorted(m['label'] for m in c.alarms.missed) == ['喝水', '晾衣服']


def test_one_that_joined_a_ring_is_done_when_it_ends(tmp_path):
    """It came due after the ring began: its own time, not the ring's start, is what rang."""
    clock = Clock(2026, 11, 3, 7, 0)
    a = alarms.Alarms(tmp_path, clock)
    first = a.create('alarm', minutes=1)
    joined = a.create('timer', minutes=4)
    rep = a.create('alarm', '吃药', time='07:03', repeat={'type': 'daily'})
    clock.go(60)
    ring, _ = a.take_due(clock())
    started = clock()
    clock.go(120)
    ring2, _ = a.take_due(clock(), ringing=ring)      # 07:03 吃药 joins
    clock.go(60)
    ring3, _ = a.take_due(clock(), ringing=ring + ring2)   # 07:04 the timer joins
    fired = {i['id']: clock() - 60 for i in ring2} | {i['id']: clock() for i in ring3}
    clock.go(30)
    a.finish(ring + ring2 + ring3, started, answered=True, fired=fired)
    assert a.take_due(clock() + 1) == ([], [])          # nothing rings again
    assert alarms.due(a.get(first['id'])) is None and alarms.due(a.get(joined['id'])) is None
    assert at(a.get(rep['id'])['at']) == datetime.datetime(2026, 11, 4, 7, 3)


@pytest.mark.asyncio
async def test_joined_entry_rings_once_in_the_client(tmp_path, monkeypatch):
    Speaker(monkeypatch, 40)
    c = make_client(tmp_path, monkeypatch)
    c.alarms.create('alarm', '起床', seconds=60)
    c.alarms.create('timer', '鸡蛋', seconds=120)
    await ring_now(c)
    c.alarms.clock.go(60)
    await c._alarm_tick(c.alarms.clock())               # the timer joins
    assert len(c.ring['items']) == 2
    await c.stop_alarm()
    c.alarms.clock.go(1)
    await c._alarm_tick(c.alarms.clock())
    assert c.ring is None and c.alarms.active() == []


@pytest.mark.asyncio
async def test_held_volume_keys_do_not_lose_steps(tmp_path, monkeypatch):
    sp = Speaker(monkeypatch, 40)
    orig_get = mcp.get_volume

    async def slow_get():
        await asyncio.sleep(0.01)
        return await orig_get()
    monkeypatch.setattr(mcp, 'get_volume', slow_get)
    c = make_client(tmp_path, monkeypatch)
    await asyncio.gather(*[c.volume_step(5) for _ in range(4)])   # key repeat
    assert sp.volume == 60


@pytest.mark.asyncio
async def test_quick_taps_on_preview_play_one_and_put_the_volume_back(tmp_path, monkeypatch):
    sp = Speaker(monkeypatch, 40)
    c = make_client(tmp_path, monkeypatch)
    await asyncio.gather(c.preview_tone('coffee'), c.preview_tone('morning'), c.preview_tone('marimba'))
    await asyncio.sleep(0.1)
    assert sp.volume == 70 and c.held_volume == 40
    await c._stop_preview()
    assert sp.volume == 40 and c.held_volume is None and c.audio.player.buffered() < 0.01


@pytest.mark.skipif(real_opus is None, reason='no libopus')
def test_tone_files_decode_to_the_pieces():
    lengths = {'coffee': 27.2, 'morning': 18.2, 'bright': 20.2, 'hop': 11.0}
    for name, seconds in lengths.items():
        x = alarms.tone(name)[:-int(OUT_RATE * alarms.TONE_GAP)]
        assert abs(len(x) / OUT_RATE - seconds) < 0.1, name
        rms = 20 * np.log10(np.sqrt(np.mean(x ** 2)))
        assert -24 < rms < -14 and np.abs(x).max() < 0.95, (name, rms)
    chime = alarms.reminder_chime()
    assert chime.shape[1] == 2 and 3 < len(chime) / OUT_RATE < 4


@pytest.mark.asyncio
async def test_stop_right_after_a_second_tap_stops_both(tmp_path, monkeypatch):
    sp = Speaker(monkeypatch, 40)
    c = make_client(tmp_path, monkeypatch)
    await c.preview_tone('marimba')
    await asyncio.sleep(0.05)
    first = c.preview
    await c.preview_tone('marimba')   # the second one, not started yet
    await c._stop_preview()            # cancels it before its first step
    await asyncio.sleep(0.05)
    assert first.done() and sp.volume == 40 and c.held_volume is None


@pytest.mark.asyncio
async def test_volume_key_just_as_a_ring_starts(tmp_path, monkeypatch):
    sp = Speaker(monkeypatch, 40)
    c = make_client(tmp_path, monkeypatch)
    c.alarms.create('alarm', time='07:00')
    c.alarms.clock.go(60)
    await c._alarm_tick(c.alarms.clock())
    await c.volume_step(5)             # before the ring has switched the speaker
    await asyncio.sleep(0.05)
    assert sp.volume == 75             # the ring plays at its volume + the key
    await c.stop_alarm()
    assert sp.volume == 40 and c.alarm_volume == 70


@pytest.mark.asyncio
async def test_wake_word_stops_a_preview(tmp_path, monkeypatch):
    sp = Speaker(monkeypatch, 40)
    c = make_client(tmp_path, monkeypatch)
    await c.preview_tone('coffee')
    await asyncio.sleep(0.05)
    await c.wake('voice')
    assert c.preview is None and sp.volume == 40 and c.state == 'listening'
