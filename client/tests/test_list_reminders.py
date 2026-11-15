"""清单的提醒（设计画布第 76 块）：要在这台响铃的，排成这台的提醒；划掉、改了的撤掉；别的设备的不排。"""
import datetime
import sys
import time
import types

import numpy as np

for name in ('sounddevice', 'sherpa_onnx', 'thinksmart_client.webrtc_apm'):
    sys.modules.setdefault(name, types.ModuleType(name))
if 'thinksmart_client.opus' not in sys.modules:
    opus = types.ModuleType('thinksmart_client.opus')
    opus.Encoder = lambda *a: types.SimpleNamespace(encode=lambda pcm: b'')
    opus.Decoder = lambda *a: types.SimpleNamespace(decode=lambda p: np.zeros(0, np.float32))
    sys.modules['thinksmart_client.opus'] = opus
try:
    import thinksmart_client.speex_aec  # noqa: F401
except OSError:
    sys.modules['thinksmart_client.speex_aec'] = types.SimpleNamespace(LinearAec=object)

from thinksmart_client import alarms, app  # noqa: E402


def make(tmp_path, monkeypatch):
    monkeypatch.setattr(alarms, 'clock_synced', lambda: True)
    c = app.Client.__new__(app.Client)
    c.cfg = {'device_id': 'dev-1'}
    c.alarms = alarms.Alarms(tmp_path)
    c.ring = None
    c.published = []
    c.publish_alarms = lambda: c.published.append(1)
    c.events = types.SimpleNamespace(publish=lambda *a: None)

    async def clip(item):
        pass
    c._make_clip = clip
    monkeypatch.setattr(app.bg, 'spawn', lambda coro: coro.close())
    return c


def lists(items):
    return {'version': 1, 'lists': {'待办': items}, 'scopes': {'待办': 'device'}}


def test_ring_items_become_device_reminders(tmp_path, monkeypatch):
    c = make(tmp_path, monkeypatch)
    day = (datetime.date.today() + datetime.timedelta(days=3)).isoformat()
    fee = {'id': 'a1', 'text': '交物业费', 'done': False, 'due': day, 'time': '09:00', 'remind': {'mode': 'ring', 'before': [0, 1440], 'where': []}}
    other = {'id': 'b2', 'text': '书房的事', 'done': False, 'due': day, 'remind': {'mode': 'ring', 'before': [0], 'where': ['dev-2']}}
    shown = {'id': 'c3', 'text': '只显示', 'done': False, 'due': day, 'remind': {'mode': 'show', 'before': [0]}}
    c._set_lists(lists([fee, other, shown]))
    rs = [i for i in c.alarms.items if str(i.get('src', '')).startswith('list:')]
    assert sorted(i['label'] for i in rs) == ['交物业费', '交物业费（明天）'] and all(i['kind'] == 'reminder' for i in rs)
    assert {datetime.datetime.fromtimestamp(i['at']).strftime('%H:%M') for i in rs} == {'09:00'}
    c._set_lists(lists([fee, other, shown]))                     # 再同步一次：不重复排
    assert len([i for i in c.alarms.items if i.get('src')]) == 2
    c._set_lists(lists([dict(fee, done=True), other]))           # 划掉了：撤掉
    assert not [i for i in c.alarms.items if i.get('src')]


def test_past_reminders_are_left_for_snooze(tmp_path, monkeypatch):
    c = make(tmp_path, monkeypatch)
    day = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    x = {'id': 'a1', 'text': '倒垃圾', 'done': False, 'due': day, 'time': '20:00', 'remind': {'mode': 'ring', 'before': [0]}}
    c._set_lists(lists([x]))
    item = next(i for i in c.alarms.items if i.get('src'))
    item['at'] = time.time() - 60                                # 响过了
    c._set_lists(lists([dict(x, due=(datetime.date.today() + datetime.timedelta(days=8)).isoformat())]))   # 重复的排到了下周
    srcs = [i['src'] for i in c.alarms.items if i.get('src')]
    assert item['src'] in srcs and len(srcs) == 2                 # 响过的那条还在（能再睡），下周的也排上了
