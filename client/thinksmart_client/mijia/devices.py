"""Turn a Mi device record + its MIoT spec into cards: what to read, how to show it, how to control it.

The primary-service choice follows MenuBarMijia's MiotSynthesizer (MIT): the device type (URN
segment 3) names its own service, else the registry below in priority order, limited to the
fallback claims. Features are then looked up by property short name, in the primary service first
and then in the companion services (environment, battery, power-consumption, ...). Writes snap to
the spec's range/step and value list, like MiotCommandMapper.
"""
import math

from .spec import short_name

# primary service -> card kind (order = fallback priority, as in MiotSynthesizer.registry)
REGISTRY = [
    ('air-conditioner', 'climate'), ('heater', 'climate'), ('thermostat', 'climate'),
    ('electric-blanket', 'climate'), ('dehumidifier', 'fan'), ('diffuser', 'plug'),
    ('speaker', 'speaker'), ('light', 'light'), ('fan', 'fan'), ('air-purifier', 'purifier'),
    ('air-fresh', 'purifier'), ('humidifier', 'purifier'), ('vacuum', 'vacuum'), ('curtain', 'cover'),
    ('window-opener', 'cover'), ('airer', 'cover'), ('fridge', 'appliance'),
    ('camera-control', 'camera'), ('video-doorbell', 'camera'), ('outlet', 'plug'), ('switch', 'plug'),
    ('electronic-valve', 'valve'), ('kettle', 'appliance'), ('ble-kettle', 'appliance'),
    ('water-dispenser', 'appliance'), ('cooker', 'appliance'), ('air-fryer', 'appliance'),
    ('oven', 'appliance'), ('microwave-oven', 'appliance'), ('health-pot', 'appliance'),
    ('water-heater', 'appliance'), ('washer', 'appliance'), ('dryer', 'appliance'),
    ('clothes-dryer', 'appliance'), ('dishwasher', 'appliance'), ('lock', 'lock'),
    ('motion-sensor', 'sensor'), ('occupancy-sensor', 'sensor'), ('magnet-sensor', 'sensor'),
    ('submersion-sensor', 'sensor'), ('illumination-sensor', 'sensor'), ('air-monitor', 'sensor'),
    ('temperature-humidity-sensor', 'sensor'),
]
KIND_OF = dict(REGISTRY)
FALLBACK_CLAIMS = {'air-condition-outlet': ['air-conditioner'], 'ble-speaker': ['speaker'],
                   'ceiling-fan': ['fan'], 'door': ['lock', 'video-doorbell'], 'night-light': ['light'],
                   'relay': ['switch']}
KIND_LABEL = {'light': '灯', 'plug': '开关插座', 'climate': '空调暖气', 'fan': '风扇', 'purifier': '净化加湿',
              'cover': '窗帘', 'sensor': '传感器', 'lock': '门锁', 'speaker': '音箱', 'vacuum': '扫地机',
              'appliance': '家电', 'camera': '摄像头', 'valve': '阀门', 'other': '其他'}
# what a spoken category word may mean (self.home.* tools, name matching)
KIND_WORDS = {'light': ['灯', '灯光', '台灯', '吊灯', '灯带', '射灯', '筒灯'], 'plug': ['插座', '开关', '插头'],
              'climate': ['空调', '暖气', '取暖器', '电暖器', '电热毯', '温控'], 'fan': ['风扇', '电扇', '除湿机'],
              'purifier': ['净化器', '加湿器', '新风'], 'cover': ['窗帘', '窗', '晾衣架'],
              'sensor': ['传感器', '温度计', '温湿度计'], 'lock': ['门锁', '锁'], 'speaker': ['音箱', '小爱'],
              'vacuum': ['扫地机', '扫地机器人', '吸尘器'], 'camera': ['摄像头', '监控']}
# companion services searched for readings, after the primary one
COMPANIONS = ['environment', 'temperature-humidity-sensor', 'battery', 'power-consumption', 'filter',
              'fan-control', 'play-control', 'air-purifier-favorite', 'air-fresh-favorite', 'motor-speed']
# feature key -> property short names, first match wins
FEATURES = {
    'on': ['on'], 'brightness': ['brightness'], 'color_temp': ['color-temperature'], 'mode': ['mode'],
    'target_temp': ['target-temperature'], 'temp': ['temperature', 'indoor-temperature'],
    'humidity': ['relative-humidity'], 'target_humidity': ['target-humidity'], 'pm25': ['pm2.5-density'],
    'aqi': ['air-quality'], 'battery': ['battery-level'], 'fan_level': ['fan-level', 'favorite-fan-level',
                                                                         'favorite-level', 'speed-level'],
    'position': ['current-position'], 'target_position': ['target-position'], 'motor': ['motor-control'],
    'power_w': ['electric-power'], 'energy': ['power-consumption'], 'volume': ['volume'], 'mute': ['mute'],
    'filter': ['filter-life-level'], 'motion': ['motion-state', 'occupancy-status'],
    'contact': ['contact-state'], 'leak': ['submersion-state'], 'illum': ['illumination'],
    'door_state': ['door-state'], 'status': ['status'], 'swing': ['horizontal-swing'],
}
TRACK_NAMES = {'drape curtain': '布帘', 'lace curtain': '纱帘', 'left curtain': '左帘', 'right curtain': '右帘'}
READ_ONLY_KINDS = {'lock', 'valve', 'camera', 'sensor', 'appliance'}


def readable(p):
    return 'read' in p['access']


def writable(p):
    return 'write' in p['access']


def primary_services(spec):
    """[(service, kind)]: usually one; a multi-key switch panel without split records gives one per key."""
    services = spec['services']
    dtype = short_name(spec['type'])
    named = lambda n: [s for s in services if s['name'] == n]
    preferred = {'outlet': ['outlet', 'switch'], 'camera': ['camera-control', 'video-doorbell'],
                 'cateye': ['camera-control', 'video-doorbell'],
                 'kettle': ['kettle', 'ble-kettle', 'water-dispenser']}.get(dtype, [dtype])
    for name in preferred:
        if name in KIND_OF and named(name):
            kind = KIND_OF[name]
            if kind == 'plug':
                keys = [s for s in named(name) if any(p['name'] == 'on' for p in s['props'])]
                if len(keys) > 1:
                    return [(s, kind) for s in keys]
            if kind == 'valve' and _meters_power(spec):
                kind = 'breaker'
            if kind == 'cover' and len(named(name)) > 1:
                return [(s, kind) for s in named(name)]     # a dual-track motor: drape + lace
            return [(named(name)[0], kind)]
    for name, kind in REGISTRY:
        allowed = (name == 'light' and ('light' in dtype or 'lamp' in dtype)) or name in FALLBACK_CLAIMS.get(dtype, [])
        if allowed and named(name):
            return [(named(name)[0], kind)]
    if dtype.endswith('sensor'):
        env = named('environment')
        if env and any(p['name'] in ('temperature', 'relative-humidity') for p in env[0]['props']):
            return [(env[0], 'sensor')]
    return []


def _meters_power(spec):
    return any(s['name'] == 'power-consumption' and any(p['name'] == 'electric-power' for p in s['props'])
               for s in spec['services'])


def _find(spec, primary, names, companions=True):
    pools = [primary] + ([s for c in COMPANIONS for s in spec['services'] if s['name'] == c and s is not primary]
                         if companions else [])
    for svc in pools:
        for n in names:
            for p in svc['props']:
                if p['name'] == n and readable(p) or (p['name'] == n and writable(p)):
                    return {**p, 'siid': svc['iid'], 'piid': p['iid'], 'service': svc['name']}
    return None


def build_cards(device, spec, room=''):
    """Cards for one device record; [] when nothing usable."""
    if not spec:
        return []
    call_did = device.get('split_parent') or device['did']
    prims = primary_services(spec)
    if device.get('split_siid') is not None:
        prims = [(s, 'plug') for s in spec['services'] if s['iid'] == device['split_siid']][:1]
    cards = []
    for i, (svc, kind) in enumerate(prims):
        feats = {}
        for key, names in FEATURES.items():
            own_only = key in ('on', 'brightness', 'color_temp', 'mode', 'status') or (kind == 'plug' and len(prims) > 1)
            f = _find(spec, svc, names, companions=not own_only)
            if f:
                feats[key] = f
        if kind == 'climate' and 'temp' in feats and feats['temp']['siid'] == svc['iid'] and 'target_temp' not in feats:
            pass
        track = TRACK_NAMES.get(svc['desc'].strip().lower()) if kind == 'cover' else None
        name = device['name'] if len(prims) == 1 else (track or f'{device["name"]} {i + 1}号')
        cards.append({
            'id': f'{device["did"]}#{svc["iid"]}', 'did': call_did, 'name': name, 'room': room,
            'model': device['model'], 'type': short_name(spec['type']), 'kind': kind,
            'online': device.get('online', True), 'siid': svc['iid'], 'feats': feats, 'values': {},
            'readonly': kind in READ_ONLY_KINDS or kind == 'breaker'})
    return cards


def build_home(records, specs, room_of):
    """Cards for a home's device records, in room order. A panel whose keys come as split records
    (did '<parent>.s2', ...) shows the keys, not the panel itself."""
    split_parents = {r['split_parent'] for r in records if r.get('split_parent')}
    order = {d: i for i, d in enumerate(room_of)}
    where = lambda r: order.get(r['did'], order.get(r.get('split_parent'), 1e9))
    cards = []
    for r in sorted(records, key=where):
        if r['did'] in split_parents:
            continue
        room = room_of.get(r['did'], room_of.get(r.get('split_parent'), ''))
        cards += build_cards(r, specs.get(r['model']), room)
    return cards


def poll_keys(card):
    return [(card['did'], f['siid'], f['piid']) for f in card['feats'].values() if readable(f)]


# ---- reading ----

def _bool(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v.strip().lower() in ('true', '1', 'on')
    return None


def _desc(f, v):
    for item in f.get('list') or []:
        if item['value'] == v:
            return item['desc']
    return None


def percent(f, v):
    """Raw value -> 0-100 by the spec range (brightness 1-100, fan speed 1-2000 ...)."""
    rng = f.get('range')
    if not rng or not isinstance(v, (int, float)):
        return v
    lo, hi = rng[0], rng[1]
    if hi == 100 and lo <= 1:
        return round(v)
    if lo > 1 and hi > lo:
        return round((v - lo) * 100 / (hi - lo))
    return round(v * 100 / hi) if hi else v


MODE_WORDS = {'cool': '制冷', 'cooling': '制冷', 'cold': '制冷', 'heat': '制热', 'heating': '制热', 'warm': '制热',
              'dry': '除湿', 'dehumidify': '除湿', 'fan': '送风', 'fan only': '送风', 'wind': '送风',
              'auto': '自动', 'automatic': '自动', 'smart': '智能', 'sleep': '睡眠', 'favorite': '喜爱',
              'manual': '手动', 'silent': '静音', 'strong': '强力', 'none': '无', 'idle': '空闲',
              'reading': '阅读', 'night': '夜灯', 'tv': '电视', 'computer': '电脑',
              'no one': '无人', 'has one': '有人', 'someone': '有人', 'nobody': '无人', 'no motion': '无人',
              'motion': '有人', 'occupied': '有人', 'unoccupied': '无人', 'open': '打开', 'close': '关闭',
              'closed': '关闭', 'locked': '已上锁', 'unlocked': '未上锁'}


def zh(text):
    return MODE_WORDS.get((text or '').strip().lower(), text or '')


def state(card):
    """Plain dict of the readable state (for the page and the voice tools)."""
    f, v = card['feats'], card['values']
    out = {}

    def val(key):
        return v.get(key)
    if 'on' in f and val('on') is not None:
        out['on'] = _bool(val('on'))
    if 'brightness' in f and val('brightness') is not None:
        out['brightness'] = percent(f['brightness'], val('brightness'))
    if 'color_temp' in f and val('color_temp') is not None:
        out['color_temp'] = val('color_temp')
    for key in ('mode', 'status', 'motor'):
        if key in f and val(key) is not None:
            out[key] = zh(_desc(f[key], val(key))) or val(key)
    if 'fan_level' in f and val('fan_level') is not None:
        fl = f['fan_level']
        out['fan_level'] = (zh(_desc(fl, val('fan_level'))) or val('fan_level')) if fl.get('list') else \
            f'{percent(fl, val("fan_level"))}%'
    for key in ('target_temp', 'temp', 'humidity', 'target_humidity', 'pm25', 'battery', 'power_w', 'energy',
                'volume', 'filter', 'illum', 'aqi'):
        if key in f and isinstance(val(key), (int, float)) and not isinstance(val(key), bool):
            x = val(key)
            out[key] = round(x, 1) if isinstance(x, float) else x
    if 'aqi' in f and f['aqi'].get('list') and val('aqi') is not None:
        out['aqi'] = _desc(f['aqi'], val('aqi')) or val('aqi')
    for key in ('position', 'target_position'):
        if key in f and isinstance(val(key), (int, float)):
            pos = percent(f[key], val(key))
            out[key] = 100 - pos if card['type'] == 'airer' else pos
    for key in ('motion', 'contact', 'leak', 'mute', 'swing'):
        if key in f and val(key) is not None:
            out[key] = _bool(val(key)) if f[key]['format'] == 'bool' else (zh(_desc(f[key], val(key))) or val(key))
    if 'door_state' in f and val('door_state') is not None:
        out['door_state'] = zh(_desc(f['door_state'], val('door_state'))) or val('door_state')
    if card['kind'] == 'plug' and 'temp' in out:
        out['device_temp'] = out.pop('temp')    # a switch's or plug's own chip, not the room
    return out


def summary(card):
    """One line of Chinese for the card and for speaking: '开 · 亮度 60%', '制冷 26°C · 室温 28°C'."""
    s = state(card)
    if not card['online']:
        return '离线'
    parts = []
    if 'on' in s:
        parts.append('开' if s['on'] else '关')
    if s.get('on') is not False:
        if card['kind'] == 'climate':
            if 'mode' in s:
                parts.append(str(s['mode']))
            if 'target_temp' in s:
                parts.append(f'{s["target_temp"]}°C')
        elif 'mode' in s and card['kind'] in ('fan', 'purifier'):
            parts.append(str(s['mode']))
        if 'brightness' in s:
            parts.append(f'亮度 {s["brightness"]}%')
        if 'color_temp' in s:
            parts.append(f'{s["color_temp"]}K')
        if 'fan_level' in s:
            parts.append(f'风速 {s["fan_level"]}')
    if 'position' in s:
        parts.append(f'开合 {s["position"]}%')
    if 'temp' in s:
        parts.append(('室温 ' if card['kind'] == 'climate' else '') + f'{s["temp"]}°C')
    if 'humidity' in s:
        parts.append(f'湿度 {s["humidity"]}%')
    if 'pm25' in s:
        parts.append(f'PM2.5 {s["pm25"]}')
    if 'power_w' in s and s.get('on'):
        parts.append(f'{s["power_w"]}W')
    if 'motion' in s:
        parts.append('有人' if s['motion'] is True else ('无人' if s['motion'] is False else str(s['motion'])))
    if 'contact' in s:
        parts.append('门窗已关' if s['contact'] is True else ('门窗打开' if s['contact'] is False else str(s['contact'])))
    if 'leak' in s:
        parts.append('漏水！' if s['leak'] is True else '无漏水')
    if 'door_state' in s:
        parts.append(str(s['door_state']))
    if 'volume' in s and card['kind'] == 'speaker':
        parts.append(f'音量 {s["volume"]}')
    if 'battery' in s:
        parts.append(f'电量 {s["battery"]}%')
    if 'status' in s and card['kind'] in ('appliance', 'vacuum'):
        parts.append(str(s['status']))
    return ' · '.join(parts) or ('在线' if card['values'] else '—')


# ---- writing ----

class ControlError(Exception):
    pass


def snap(f, value):
    fmt = f['format']
    if fmt == 'bool':
        return bool(value)
    rng = f.get('range')
    x = float(value)
    if rng:
        x = min(max(x, rng[0]), rng[1])
        step = rng[2] if len(rng) > 2 and rng[2] else 0
        if step:
            x = rng[0] + round((x - rng[0]) / step) * step
    if fmt.startswith(('int', 'uint')):
        return int(round(x))
    return round(x, 2)


def from_percent(f, pct):
    rng = f.get('range')
    if not rng:
        return pct
    lo, hi = rng[0], rng[1]
    if hi == 100 and lo <= 1:
        return max(lo, pct)
    if lo > 1 and hi > lo:
        return lo + (hi - lo) * pct / 100
    return max(lo, hi * pct / 100)


def _match_list(f, wanted):
    """Value of the value-list item whose description matches (English or Chinese), or None."""
    w = str(wanted).strip().lower()
    items = f.get('list') or []
    for it in items:
        d = it['desc'].strip().lower()
        if w in (d, zh(d).lower(), str(it['value'])):
            return it['value']
    for it in items:
        d = it['desc'].strip().lower()
        if w and (w in d or w in zh(d).lower()):
            return it['value']
    return None


def writes_for(card, op, value=None):
    """[(feature, raw value)] for op; raises ControlError with a Chinese reason."""
    f = card['feats']
    need = lambda k: f.get(k) if f.get(k) and writable(f[k]) else None

    def with_on(rest):
        return ([('on', True)] if need('on') and card['values'].get('on') is not True else []) + rest
    if op in ('on', 'off', 'toggle'):
        if card['kind'] == 'cover':
            return writes_for(card, 'open' if op == 'on' else 'close')
        if not need('on'):
            raise ControlError('这个设备不能远程开关')
        target = (op == 'on') if op != 'toggle' else not _bool(card['values'].get('on'))
        return [('on', target)]
    if op == 'brightness':
        if not need('brightness'):
            raise ControlError('这个设备不能调亮度')
        pct = _num(value, 0, 100)
        if pct == 0 and need('on'):
            return [('on', False)]
        return with_on([('brightness', snap(f['brightness'], from_percent(f['brightness'], pct)))])
    if op == 'color_temp':
        if not need('color_temp'):
            raise ControlError('这个设备不能调色温')
        return with_on([('color_temp', snap(f['color_temp'], _num(value, 1000, 10000)))])
    if op == 'target_temperature':
        if not need('target_temp'):
            raise ControlError('这个设备不能设温度')
        return with_on([('target_temp', snap(f['target_temp'], _num(value, -50, 120)))])
    if op == 'target_humidity':
        if not need('target_humidity'):
            raise ControlError('这个设备不能设湿度')
        return with_on([('target_humidity', snap(f['target_humidity'], _num(value, 0, 100)))])
    if op == 'mode':
        if not need('mode') or not f['mode'].get('list'):
            raise ControlError('这个设备没有可选模式')
        raw = _match_list(f['mode'], value)
        if raw is None:
            raise ControlError('没有这个模式，可选：' + '、'.join(zh(i['desc']) for i in f['mode']['list']))
        return with_on([('mode', raw)])
    if op == 'fan_level':
        fl = need('fan_level')
        if not fl:
            raise ControlError('这个设备不能调风速')
        if fl.get('list'):
            raw = _match_list(fl, value)
            if raw is None:
                raise ControlError('没有这个档位，可选：' + '、'.join(zh(i['desc']) for i in fl['list']))
            return with_on([('fan_level', raw)])
        return with_on([('fan_level', snap(fl, from_percent(fl, _num(value, 0, 100))))])
    if op == 'position':
        if not need('target_position'):
            raise ControlError('这个设备不能设开合位置')
        pos = _num(value, 0, 100)
        pos = 100 - pos if card['type'] == 'airer' else pos
        return [('target_position', snap(f['target_position'], from_percent(f['target_position'], pos)))]
    if op in ('open', 'close', 'stop'):
        m = need('motor')
        if m and m.get('list'):
            words = {'open': ['open', 'up', '打开', '上升'], 'close': ['close', 'down', 'dowm', '关闭', '下降'],
                     'stop': ['pause', 'stop', '暂停', '停止']}[op]
            for w in words:
                raw = _match_list(m, w)
                if raw is not None:
                    return [('motor', raw)]
        if op != 'stop' and need('target_position'):
            return writes_for(card, 'position', 100 if op == 'open' else 0)
        raise ControlError('这个设备不支持' + {'open': '打开', 'close': '关闭', 'stop': '停止'}[op])
    if op == 'volume':
        if not need('volume'):
            raise ControlError('这个设备不能调音量')
        return [('volume', snap(f['volume'], from_percent(f['volume'], _num(value, 0, 100))))]
    raise ControlError(f'不支持的操作 {op}')


def _num(value, lo, hi):
    try:
        x = float(value)
    except (TypeError, ValueError):
        raise ControlError('缺少数值') from None
    if not math.isfinite(x):
        raise ControlError('数值不对')
    return min(max(x, lo), hi)


def raw_writes(card, writes):
    return [(card['did'], card['feats'][k]['siid'], card['feats'][k]['piid'], v) for k, v in writes]
