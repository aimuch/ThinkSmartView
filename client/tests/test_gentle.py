"""温柔叫醒（设计画布第 46 块）：只对闹钟、提前开始、先慢后快、碰了就停、闹钟没了就停。"""
from thinksmart_client import gentle

ALARM = {'id': 1, 'kind': 'alarm', 'label': '起床', 'at': 10_000}


def test_settings_are_normalized():
    assert gentle.normalize(None) == gentle.DEFAULT
    assert gentle.normalize({'enabled': 1, 'minutes': 7, 'birds': 0}) == {'enabled': True, 'minutes': 10, 'birds': False}
    assert gentle.normalize({'minutes': 30})['minutes'] == 30


def test_only_alarms_within_the_lead_and_the_soonest():
    items = [{'id': 2, 'kind': 'reminder', 'at': 9_900}, {'id': 3, 'kind': 'timer', 'at': 9_950}, ALARM,
             {'id': 4, 'kind': 'alarm', 'at': 10_300}]
    assert gentle.next_alarm(items, 9_400, 600) == (ALARM, 10_000)
    assert gentle.next_alarm(items, 9_000, 600) is None                     # 还没到提前的时间
    assert gentle.next_alarm([{**ALARM, 'snooze': 9_500}], 9_000, 600)[1] == 9_500   # 推迟了：按推迟后的


def test_brightness_slow_then_fast():
    assert gentle.level(0, 600, 0) == 0
    assert gentle.level(0, 600, 300) == round(gentle.PEAK / 4)
    assert gentle.level(0, 600, 600) == gentle.PEAK and gentle.level(0, 600, 900) == gentle.PEAK


def test_off_by_default_then_start_steps_and_end_when_it_rings():
    g = gentle.Gentle()
    assert g.tick([ALARM], 9_500) is None                                  # 默认关
    g = gentle.Gentle({'enabled': True, 'minutes': 10})
    assert g.tick([ALARM], 9_300) is None                                  # 还早
    assert g.tick([ALARM], 9_400) == ('start', 0)
    assert g.tick([ALARM], 9_401) is None                                  # 变化太小不写
    what, lv = g.tick([ALARM], 9_850)
    assert what == 'step' and 30 < lv < gentle.PEAK
    assert g.view(9_850)['active']['left'] == 150
    assert g.tick([ALARM], 9_999, ringing=True) == ('end', None)           # 响铃接手
    assert g.tick([ALARM], 9_999) is None                                  # 同一个闹钟不再来


def test_a_touch_stops_this_one_only_and_a_cancelled_alarm_ends_it():
    g = gentle.Gentle({'enabled': True, 'minutes': 5})
    assert g.tick([ALARM], 9_800)[0] == 'start'
    assert g.stop() is True and g.stop() is False
    assert g.tick([ALARM], 9_810) is None
    tomorrow = {**ALARM, 'at': 10_000 + 86_400}
    assert g.tick([tomorrow], 10_000 + 86_400 - 200)[0] == 'start'         # 重复闹钟的下一次照常
    assert g.tick([], 10_000 + 86_400 - 100) == ('end', None)              # 闹钟删了
