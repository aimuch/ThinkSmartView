"""门口来人（door.py，设计画布第 86 块）：弹出、刷新、收起，响不响。"""
import asyncio
import types

import pytest

from thinksmart_client import alarms, app, door


def make(now):
    events = []
    d = door.Door('http://hub', 'dev', lambda k, v: events.append((k, v)), now=lambda: now[0])
    return d, events


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_show_repeat_more_close_and_expire():
    now = [1000.0]
    d, events = make(now)

    async def go():
        assert d.show({'id': 'a', 'why': '门铃按了', 'name': '门铃', 'at': 1000, 'camera': True})
        v = d.view()
        assert v['open'] and v['until'] == 1060 and v['camera'] and events[-1][0] == 'door'
        now[0] = 1020
        assert not d.show({'id': 'a', 'why': '门铃按了', 'at': 1020})      # 同一个触发又来：不算新的
        assert d.view()['until'] == 1080 and d.view()['opened'] == 1000
        d.more()
        assert d.view()['until'] == 1140
        for _ in range(4):
            d.more()
        assert d.view()['until'] == 1300                                    # 最多连着开 5 分钟
        now[0] = 1299.5
        with pytest.raises(ValueError, match='5 分钟'):
            d.more()
        d.close()
        assert d.view() is None and events[-1] == ('door', {'open': False})
        with pytest.raises(ValueError, match='收起'):
            d.more()
        d.show({'id': 'b', 'why': '门口有人走动'})
        now[0] = 1400
        assert d.view() is None                                             # 过了时间自己没了
    run(go())


class Stub:
    def __init__(self, now, sleeping=False, dnd=False, silenced=False, busy=False):
        self.door, _ = make(now)
        self.sleeping = sleeping
        self._dnd, self._sil, self._busy = dnd, silenced, busy
        self.pushed, self.poked = [], 0
        self.screen = types.SimpleNamespace(poke=self._poke)
        self.audio = types.SimpleNamespace(player=types.SimpleNamespace(push=self.pushed.append))
        self.intercom = None
        self.ring = None

    def _poke(self):
        self.poked += 1

    def dnd_active(self):
        return self._dnd

    def _silenced(self):
        return self._sil

    def in_turn(self):
        return self._busy


def fire(stub, **e):
    run(app.Client.door_event(stub, {'type': 'door', 'id': 'x', 'why': '门铃按了', 'camera': True, **e}))


def test_door_event_rules():
    now = [1000.0]
    s = Stub(now)
    fire(s)
    assert s.door.view() and s.poked == 1 and len(s.pushed) == 1         # 平常：亮屏、叮咚
    fire(s)
    assert len(s.pushed) == 1                                            # 同一个触发又来：不再响
    for kw in ({'silenced': True}, {'busy': True}):
        t = Stub(now, **kw)
        fire(t)
        assert t.door.view() and t.poked == 1 and not t.pushed           # 静音、正在对话：弹、不出声
    night = Stub(now, sleeping=True)
    fire(night)
    assert night.door.view() is None and not night.poked                 # 睡眠时段默认不弹
    fire(night, night=True)
    assert night.door.view() and not night.pushed                        # 开了「睡觉时也弹」：弹、不出声
    dnd = Stub(now, dnd=True)
    fire(dnd, test=True)
    assert dnd.door.view() and not dnd.pushed                            # 后台的测试：勿扰也弹，不出声


def test_doorbell_chime():
    x = alarms.doorbell_chime()
    assert x.ndim == 2 and x.shape[1] == 2 and 2.0 < len(x) / alarms.OUT_RATE < 3.0
    assert abs(float(abs(x).max()) - 0.55) < 0.01
