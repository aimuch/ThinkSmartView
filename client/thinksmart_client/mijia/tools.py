"""self.home.* device tools: the hub's model lists, reads and controls the user's Mijia devices.

Three generic tools instead of one per device (every tool is sent with every request). The model
passes the name as the user said it; matching happens here (service.find). Results:
- a done control: {"action": "RESPONSE", "response": "已打开书房台灯"}: the hub speaks it at once;
- anything else is JSON the model turns into the answer (states to read out, candidates to ask
  about, a confirmation to ask for, a reason it failed). Never isError: the hub drops that text.
"""
import asyncio
import json

from . import devices as dv
from .service import ALL_WORDS, VOICE_TIMEOUT

OPS = ['on', 'off', 'toggle', 'brightness', 'color_temp', 'target_temperature', 'target_humidity', 'mode',
       'fan_level', 'position', 'open', 'close', 'stop', 'volume']
KINDS = [k for k in dv.KIND_LABEL if k != 'other']

TOOLS = [
    {
        'name': 'self.home.list_devices',
        'description': '列出用户米家里的智能家居设备和它们现在的状态（开关、亮度、温度、湿度、电量等）。'
                       '用户问「家里哪些灯开着」「客厅温度多少」「有哪些设备」「门关了吗」时调用，'
                       '按结果用一两句话回答，不要逐条念全部。room 只在用户说了房间时传；'
                       'kind 可选：light 灯、plug 开关插座、climate 空调暖气、fan 风扇、purifier 净化加湿、'
                       'cover 窗帘、sensor 传感器、lock 门锁、speaker 音箱、vacuum 扫地机、appliance 家电。'
                       '这是家里的米家设备，不是这台设备本身（本机音量、屏幕用 self.audio_speaker / self.screen）。',
        'inputSchema': {'type': 'object',
                        'properties': {'room': {'type': 'string', 'description': '房间名，如 客厅'},
                                       'kind': {'type': 'string', 'enum': KINDS}}},
    },
    {
        'name': 'self.home.get_state',
        'description': '读取某个米家设备的详细状态。device 填用户说的设备名，可以带房间，如「书房台灯」「客厅空调」。',
        'inputSchema': {'type': 'object', 'properties': {'device': {'type': 'string'}},
                        'required': ['device']},
    },
    {
        'name': 'self.home.control',
        'description': '控制用户米家里的智能家居设备。device 填用户说的设备名（可带房间，如「书房台灯」「客厅的灯」；'
                       '说「全部灯」「所有灯」时照样填，会一起控制）。op：on 打开、off 关闭、toggle 切换、'
                       'brightness 亮度 value=0-100、color_temp 色温 value=开尔文（暖光约 2700，白光约 5000）、'
                       'target_temperature 温度 value=度数、target_humidity 湿度 value=百分比、'
                       'mode 模式 value=模式名（制冷、制热、除湿、送风、自动、睡眠…）、fan_level 风速 value=档位名或 0-100、'
                       'position 窗帘开合 value=0-100、open/close/stop 窗帘打开/关闭/停止、volume 音箱音量 value=0-100。'
                       '「调亮一点」这类相对调整先用 get_state 读当前值再算。'
                       '返回 need_confirm 时把 reason 问用户，用户明确同意后带 confirm=true 再调一次；'
                       '返回 candidates 时问用户是哪一个。',
        'inputSchema': {'type': 'object',
                        'properties': {'device': {'type': 'string'}, 'op': {'type': 'string', 'enum': OPS},
                                       'value': {'type': 'string', 'description': '数值或模式名，按 op 填'},
                                       'confirm': {'type': 'boolean'}},
                        'required': ['device', 'op']},
    },
]
NAMES = {t['name'] for t in TOOLS}


def _dumps(v):
    return json.dumps(v, ensure_ascii=False)


def _not_ready(svc):
    if not svc.session:
        return {'success': False, 'reason': '米家还没登录。请用户在屏幕上打开「家居」页，用米家 App 扫码登录。'}
    if svc.state == 'expired':
        return {'success': False, 'reason': '米家登录已过期，请用户在屏幕的「家居」页重新扫码。'}
    if svc.state == 'loading':
        return {'success': False, 'reason': '米家设备还在加载，请稍等几秒再试。'}
    if svc.state == 'error' and not svc.cards:
        return {'success': False, 'reason': f'米家暂时连不上：{svc.error}'}
    return None


def _brief(c):
    return {'name': c['name'], 'room': c['room'], 'type': dv.KIND_LABEL.get(c['kind'], c['kind']),
            'online': c['online'], 'state': dv.summary(c)}


def _value(op, raw):
    if raw is None or op in ('mode', 'fan_level'):
        return raw
    try:
        return float(raw) if '.' in str(raw) else int(raw)
    except (TypeError, ValueError):
        return raw


async def handle(name, args, svc):
    """JSON text for one self.home.* call."""
    bad = _not_ready(svc)
    if bad:
        return _dumps(bad)
    if name == 'self.home.list_devices':
        cards = svc.find('', kind=args.get('kind'), room=args.get('room'))[:40]
        await svc.fresh(cards)
        if not cards:
            return _dumps({'devices': [], 'note': '没有符合条件的设备',
                           'rooms': sorted({c['room'] for c in svc.cards.values() if c['room']})})
        return _dumps({'devices': [_brief(c) for c in cards]})
    if name == 'self.home.get_state':
        cards = svc.find(args.get('device', ''))[:5]
        if not cards:
            return _dumps({'success': False, 'reason': f'找不到「{args.get("device")}」',
                           'devices': [c['name'] for c in list(svc.cards.values())[:20]]})
        await svc.fresh(cards)
        return _dumps({'devices': [{**_brief(c), 'detail': dv.state(c)} for c in cards]})
    if name == 'self.home.control':
        query, op = str(args.get('device') or ''), str(args.get('op') or '')
        if op not in OPS:
            return _dumps({'success': False, 'reason': f'不支持的 op：{op}'})
        value, confirm = _value(op, args.get('value')), bool(args.get('confirm'))
        cards = [c for c in svc.find(query) if not c['readonly']] or svc.find(query)
        if not cards:
            return _dumps({'success': False, 'reason': f'找不到「{query}」',
                           'devices': [c['name'] for c in list(svc.cards.values())[:20]]})
        everything = any(w in query for w in ALL_WORDS)
        if len(cards) > 1 and not everything:
            return _dumps({'success': False, 'candidates': [f'{c["room"]}{c["name"]}' for c in cards[:8]],
                           'reason': '有好几个设备对得上，问用户是哪一个'})
        cards = cards[:10]
        if len(cards) > 3 and not confirm:
            return _dumps({'success': False, 'need_confirm': True,
                           'reason': f'要一起操作这 {len(cards)} 个设备吗：' + '、'.join(c['name'] for c in cards)})
        results = await asyncio.wait_for(asyncio.gather(
            *(svc.control(c['id'], op, value, confirm=confirm or len(cards) > 3, by_voice=True) for c in cards)),
            VOICE_TIMEOUT + 5)
        asks = [r for r in results if r.get('need_confirm')]
        if asks:
            return _dumps({'success': False, 'need_confirm': True, 'reason': '；'.join(r['text'] for r in asks)})
        done = [r['text'] for r in results if r['ok']]
        failed = [r['text'] for r in results if not r['ok']]
        if done and not failed:
            return _dumps({'action': 'RESPONSE', 'response': '，'.join(done)})
        return _dumps({'success': bool(done), 'done': done, 'failed': failed})
    raise ValueError(f'Unknown tool: {name}')
