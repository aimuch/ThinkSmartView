"""温柔叫醒（设计画布第 46 块）：闹钟响之前，屏幕从最暗慢慢亮到 PEAK，鸟叫从无声慢慢起来。

只对闹钟（提醒、倒计时不算）。提前 minutes 分钟开始，按最早的那个闹钟；它被取消、推迟、改时间就停，
响了也停（响铃接手）。用户碰屏幕或点「停下」：这一次的不再继续（同一个闹钟的同一个时间不会重来）。
想法来自 fubin0478/thinksmart-view（MIT），这里按闹钟来排，不只是早上固定一个时间。
"""
from . import alarms

MINUTES = (5, 10, 15, 30)
DEFAULT = {'enabled': False, 'minutes': 10, 'birds': True}
PEAK = 60          # 响铃时的亮度（0-100，和设置里的亮度同一把尺子）
STEP = 2           # 亮度变化小于这个不再写（每秒算一次，免得一直写文件）


def normalize(value):
    v = dict(DEFAULT)
    if isinstance(value, dict):
        if 'enabled' in value:
            v['enabled'] = bool(value['enabled'])
        if value.get('minutes') in MINUTES:
            v['minutes'] = value['minutes']
        if 'birds' in value:
            v['birds'] = bool(value['birds'])
    return v


def next_alarm(items, now, lead):
    """最早的、lead 秒之内要响的闹钟（不是提醒、倒计时）；没有就是 None。"""
    best = None
    for item in items:
        at = alarms.due(item)
        if item.get('kind') != 'alarm' or at is None or not now < at <= now + lead:
            continue
        if best is None or at < best[1]:
            best = (item, at)
    return best


def level(start, at, now):
    """先慢后快：开始时 0，响铃时 PEAK。"""
    p = 0.0 if at <= start else min(1.0, max(0.0, (now - start) / (at - start)))
    return round(PEAK * p * p)


class Gentle:
    def __init__(self, settings=None):
        self.settings = normalize(settings)
        self.active = None        # {'id', 'at', 'label', 'start', 'level'}
        self.done = set()         # (id, at) 已经叫过或被停下的，不再来

    def tick(self, items, now, ringing=False):
        """每秒调一次。返回 ('start'|'step'|'end', 亮度) 或 None（什么都不用做）。"""
        if len(self.done) > 50:
            self.done = {d for d in self.done if d[1] > now - 86400}
        found = None if ringing or not self.settings['enabled'] else next_alarm(items, now, self.settings['minutes'] * 60)
        if self.active:
            a = self.active
            if not found or (found[0]['id'], found[1]) != (a['id'], a['at']):
                self.done.add((a['id'], a['at']))
                self.active = None
                return 'end', None
            lv = level(a['start'], a['at'], now)
            if abs(lv - a['level']) >= STEP or (lv == 0) != (a['level'] == 0):
                a['level'] = lv
                return 'step', lv
            return None
        if found and (found[0]['id'], found[1]) not in self.done:
            item, at = found
            self.active = {'id': item['id'], 'at': at, 'label': item.get('label') or '', 'start': now, 'level': 0}
            return 'start', 0
        return None

    def stop(self):
        """碰屏幕、点「停下」：这一次到此为止。返回刚才是不是在叫醒。"""
        if not self.active:
            return False
        self.done.add((self.active['id'], self.active['at']))
        self.active = None
        return True

    def view(self, now):
        a = self.active
        return {**self.settings, 'minutes_choices': list(MINUTES),
                'active': a and {'at': a['at'], 'label': a['label'], 'left': max(0, round(a['at'] - now))}}
