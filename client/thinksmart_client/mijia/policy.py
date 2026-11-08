"""Which controls need a spoken / tapped "yes" first, after MenuBarMijia's ModelActionPolicy and
deviceProfiles.json (MIT). Turning something off is always direct, except covers.

needs_confirm(card, writes, by_voice) -> reason (Chinese, to ask the user) or None.
Locks, valves, gateways and the like are read-only cards and never reach here.
"""
import fnmatch

HEATING_TYPES = ['heater', '*-heater', 'electric-blanket', 'water-heater', 'oven', 'cooker', 'kettle',
                 '*kettle*', 'health-pot', 'air-fryer', 'microwave-oven', 'bath-heater', 'ptc-bath-heater']
HEATING_NAMES = ['热水器', '暖气', '取暖', '电热', '烤箱', '电饭', '热水壶', '浴霸']
OPENING_TYPES = ['window-opener', 'airer', 'garage-door*']


def _match(value, patterns):
    return any(fnmatch.fnmatch(value, p) for p in patterns)


def heats(card):
    return _match(card['type'], HEATING_TYPES) or any(w in card['name'] for w in HEATING_NAMES) \
        or (card['kind'] == 'climate' and card['type'] in ('heater', 'electric-blanket'))


def needs_confirm(card, writes, by_voice=True):
    values = card['values']
    turning_on = any(k == 'on' and v is True for k, v in writes)
    if card['kind'] == 'cover' and _match(card['type'], OPENING_TYPES):
        return f'{card["name"]}要打开吗？' if writes else None
    if heats(card):
        hotter = any(k == 'target_temp' and isinstance(values.get('target_temp'), (int, float))
                     and v > values['target_temp'] for k, v in writes)
        if turning_on or hotter:
            return f'{card["name"]}是加热设备，确定要{"打开" if turning_on else "调高温度"}吗？'
    if by_voice and card['kind'] == 'plug' and any(k == 'on' for k, _ in writes):
        on = dict(writes).get('on')
        return f'{card["name"]}是插座或开关，确定要{"打开" if on else "断电"}吗？'
    if by_voice and card.get('shared'):
        return f'{card["name"]}在别人共享给你的家庭里，确定要操作吗？'
    return None
