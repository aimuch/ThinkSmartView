"""Mijia (thinksmart_client/mijia): request signing, QR login parsing, cards from real MIoT specs,
writes, the confirmation rules, name matching and the self.home.* tools.

Run like test_barge_in.py. tests/fixtures/miot_golden.json is MenuBarMijia's
Tests/Fixtures/miot_golden.json (MIT, jryang1997): real specs, devices, rooms and values.
"""
import asyncio
import json
import pathlib

import pytest

from thinksmart_client.mijia import cloud, crypto, devices as dv, policy, tools
from thinksmart_client.mijia.service import MijiaService
from thinksmart_client.mijia.spec import parse_spec

GOLDEN = json.loads((pathlib.Path(__file__).parent / 'fixtures' / 'miot_golden.json').read_text())


# ---- crypto: MenuBarMijia's known-answer vectors (Tests/MijiaCoreTests/MiCloudTests.swift) ----

SS, NONCE = 'AQIDBAUGBwgJCgsMDQ4PEA==', 'oLHC0+T1BhcBuoFA'
SIGNED = 'stUjEBQF1H9IaiRGtgWr8jmxbmJs3Xg11OVxJJfJqUI='
RESP_CIPHER = ('zQrkLOrEqjI8Np3lE6VxA2rKaQMWn5+YI9AMTef8cWUgUG0BPEyejn1/IFij2TGz+h6YR3qdIdDNGAZ38HVVeV7I/xFX7t70RZzHM/zs'
               'DtMelQgGK0XRgzlsFk4++2JAP/M/aXlqdpyZpKwhzYQ/MJ7bXSF2VIsJkgg=')


def test_rc4_classic_vector():
    assert crypto.rc4(b'Key', b'Plaintext', drop=0).hex() == 'bbf316e8d940af0ad3'


def test_signing_vectors():
    assert crypto.signed_nonce(SS, NONCE) == SIGNED
    data = '{"getVirtualModel":true,"getHuamiDevices":1}'
    f = dict(crypto.encrypt_params('/home/device_list', data, SS, NONCE))
    assert f['data'] == 'zQrgJvr34Xp4b97kO7lmB2GNcU1GhZGWLZUbXNz8fHxrLnNVOUaIiX1/Sl4='
    assert f['rc4_hash__'] == 'nV/pEbnR6lBuW/HKPIJmKEjkG21Mw8aVN5gdFQ=='
    assert f['signature'] == 'p4PJO1KloBVLQOZqU31Nl4ZPq4s='
    assert f['ssecurity'] == SS and f['_nonce'] == NONCE


def test_decrypt_response_vector():
    result = cloud.decode_response(RESP_CIPHER, 200, SIGNED)
    assert result['list'][0] == {'did': '123', 'name': '灯', 'model': 'yeelink.light.ceiling22', 'isOnline': True}


def test_nonce_layout():
    n = crypto.b64d(crypto.make_nonce(now_ms=60000 * 1234, random_bytes=bytes(8)))
    assert len(n) == 12 and int.from_bytes(n[8:], 'big') == 1234


def test_compact_json():
    assert crypto.compact_json({'b': 40.0, 'a': [1, True, None, '灯']}) == '{"a":[1,true,null,"灯"],"b":40}'
    assert crypto.compact_json({'v': 25.5}) == '{"v":25.5}'


def test_form_encode():
    assert crypto.form_encode([('qs', '%3Fsid'), ('a b', '~x')]) == 'qs=%253Fsid&a%20b=~x'


# ---- cloud responses ----

def test_errors_and_expiry():
    with pytest.raises(cloud.MiCloudError) as e:
        cloud.decode_response('', 401, SIGNED)
    assert e.value.kind == 'expired'
    with pytest.raises(cloud.MiCloudError) as e:
        cloud.decode_response('{"code":3,"message":"auth err"}', 200, SIGNED)
    assert e.value.kind == 'expired'
    with pytest.raises(cloud.MiCloudError) as e:
        cloud.decode_response('{"code":-8,"message":"bad"}', 200, SIGNED)
    assert e.value.kind == 'api'
    assert cloud.decode_response('{"code":0,"result":[1]}', 200, SIGNED) == [1]


def test_parse_ticket_and_scan():
    t = cloud.parse_ticket('&&&START&&&{"qr":"q","loginUrl":"https://l","lp":"https://lp","timeout":"120"}')
    assert t == {'login_url': 'https://l', 'lp': 'https://lp', 'qr': 'q', 'timeout': 120.0}
    with pytest.raises(cloud.MiCloudError):
        cloud.parse_ticket('{"code":70016}')
    s = cloud.parse_scan('&&&START&&&{"userId":123456,"ssecurity":"c3M=","location":"https://sts","cUserId":"cu"}')
    assert s == {'user_id': '123456', 'c_user_id': 'cu', 'ssecurity': 'c3M=', 'location': 'https://sts'}


def test_service_token_cookie():
    token, exp = cloud.token_from_set_cookie(['userId=1; Path=/', 'serviceToken=abc/DEF=; Max-Age=86400; Path=/'])
    assert token == 'abc/DEF=' and exp is not None
    assert cloud.token_from_set_cookie(['foo=bar']) == (None, None)
    token, exp = cloud.token_from_set_cookie(['serviceToken=t; Expires=Wed, 21 Oct 2037 07:28:00 GMT'])
    assert token == 't' and exp > 2e9


def test_parse_homes_and_devices():
    homes = cloud.parse_homes({'homelist': [{'id': 1, 'name': '家', 'roomlist': [{'id': 9, 'name': '书房', 'dids': ['a']}],
                                             'dids': ['b']}],
                               'share_home_list': [{'id': 2, 'name': '爸妈家', 'roomlist': []}]})
    assert [h['name'] for h in homes] == ['家', '爸妈家'] and homes[1]['shared']
    assert homes[0]['rooms'] == [{'id': '9', 'name': '书房', 'dids': ['a']}, {'id': 'home_1', 'name': '', 'dids': ['b']}]
    recs = cloud.parse_device_list({'list': [{'did': 'x.s2', 'model': 'm', 'name': '键2', 'isOnline': False,
                                              'extra': {'split': {'parentId': 'x', 'moduleId': 3}}}, {'did': 'y'}]})
    assert recs == [{'did': 'x.s2', 'name': '键2', 'model': 'm', 'online': False, 'spec_type': None,
                     'split_parent': 'x', 'split_siid': 3}]


# ---- cards from the golden specs ----

def golden_cards():
    specs = {m: parse_spec(s) for m, s in GOLDEN['specs'].items()}
    room_of = {d: r['name'] for r in GOLDEN['rooms'] for d in r['dids']}
    records = []
    for raw in GOLDEN['devices']:      # the fixture's records are already parsed (splitParentDid, splitSiid)
        rec = cloud.parse_device_list({'list': [raw]})[0]
        if raw.get('splitParentDid'):
            rec['split_parent'], rec['split_siid'] = raw['splitParentDid'], raw['splitSiid']
        records.append(rec)
    cards = {c['id']: c for c in dv.build_home(records, specs, room_of)}
    values = {(v['did'], v['siid'], v['piid']): v['value'] for v in GOLDEN['values']}
    for c in cards.values():
        for k, f in c['feats'].items():
            if (c['did'], f['siid'], f['piid']) in values:
                c['values'][k] = values[(c['did'], f['siid'], f['piid'])]
    return cards


def by_name(cards, name):
    found = [c for c in cards.values() if c['name'] == name]
    assert found, (name, [c['name'] for c in cards.values()])
    return found[0]


def test_light_card():
    c = by_name(golden_cards(), '书房吸顶灯')
    assert c['kind'] == 'light' and not c['readonly']
    assert {'on', 'brightness', 'color_temp'} <= set(c['feats'])
    s = dv.state(c)
    assert s['on'] is True and 'brightness' in s and s['color_temp'] == 4100
    assert dv.summary(c).startswith('开 · 亮度')
    assert dv.poll_keys(c)


def test_every_golden_device_builds_or_is_skipped_cleanly():
    cards = golden_cards()
    kinds = {c['kind'] for c in cards.values()}
    assert {'light', 'climate', 'plug', 'fan', 'sensor', 'lock', 'purifier', 'cover', 'vacuum'} <= kinds
    for c in cards.values():
        dv.summary(c)          # never raises
        dv.state(c)


def test_multi_key_panel_gives_one_card_per_key():
    cards = golden_cards()
    panel = [c for c in cards.values() if c['model'] == 'lemesh.switch.sw2f12']
    assert sorted(c['name'] for c in panel) == ['灯带', '筒灯']          # the split keys, not the panel
    assert {c['did'] for c in panel} == {'did_panel'} and {c['siid'] for c in panel} == {2, 3}


def test_curtain_names_match_menubarmijia():
    names = {c['name'] for c in golden_cards().values() if c['kind'] == 'cover'}
    assert set(GOLDEN['expectedNames'].values()) <= names      # 客厅窗帘, and 布帘 / 纱帘 of the dual-track motor


def test_lock_and_valve_are_read_only():
    cards = golden_cards()
    for c in cards.values():
        if c['kind'] in ('lock', 'valve', 'breaker', 'camera', 'sensor'):
            assert c['readonly'], c['name']


def test_brightness_write_snaps_and_turns_on():
    c = by_name(golden_cards(), '书房吸顶灯')
    c['values']['on'] = False
    w = dv.writes_for(c, 'brightness', 40)
    assert w[0] == ('on', True) and w[1][0] == 'brightness' and isinstance(w[1][1], int)
    assert dv.writes_for(c, 'brightness', 0) == [('on', False)]
    raw = dv.raw_writes(c, w)
    assert raw[0][0] == c['did'] and len(raw[0]) == 4


def test_climate_writes():
    cards = golden_cards()
    ac = next(c for c in cards.values() if c['kind'] == 'climate' and c['type'] == 'air-conditioner')
    w = dict(dv.writes_for(ac, 'target_temperature', 99))
    hi = ac['feats']['target_temp']['range'][1]
    assert w['target_temp'] == hi
    if ac['feats']['mode'].get('list'):
        assert 'mode' in dict(dv.writes_for(ac, 'mode', '制冷'))
        with pytest.raises(dv.ControlError):
            dv.writes_for(ac, 'mode', '不存在的模式')


def test_unsupported_op_says_why():
    c = by_name(golden_cards(), '书房吸顶灯')
    with pytest.raises(dv.ControlError, match='温度'):
        dv.writes_for(c, 'target_temperature', 26)


def test_airer_position_is_inverted():
    cards = golden_cards()
    airer = [c for c in cards.values() if c['type'] == 'airer']
    for c in airer:
        if 'target_position' in c['feats']:
            (_, raw), = dv.writes_for(c, 'position', 30)
            assert raw == dv.snap(c['feats']['target_position'], dv.from_percent(c['feats']['target_position'], 70))


# ---- confirmations ----

def card(kind='light', type_='light', name='灯', values=None):
    return {'kind': kind, 'type': type_, 'name': name, 'values': values or {}, 'readonly': False}


def test_policy():
    assert policy.needs_confirm(card(), [('on', True)]) is None
    plug = card('plug', 'outlet', '插座')
    assert policy.needs_confirm(plug, [('on', False)], by_voice=True)
    assert policy.needs_confirm(plug, [('on', False)], by_voice=False) is None
    heater = card('climate', 'heater', '电暖器', {'target_temp': 20})
    assert policy.needs_confirm(heater, [('on', True)])
    assert policy.needs_confirm(heater, [('on', False)]) is None
    assert policy.needs_confirm(heater, [('target_temp', 24)])
    assert policy.needs_confirm(heater, [('target_temp', 18)]) is None
    assert policy.needs_confirm(card('cover', 'window-opener', '窗'), [('motor', 1)])
    assert policy.needs_confirm(card('cover', 'curtain', '窗帘'), [('motor', 1)]) is None
    assert policy.needs_confirm(card('appliance', 'water-heater', '热水器'), [('on', True)])


# ---- service: matching and voice tools with a fake cloud ----

class FakeCloud:
    def __init__(self):
        self.sets = []

    async def set_props(self, writes):
        self.sets.append(writes)

    async def get_props(self, keys):
        return {}


def make_service(tmp_path):
    events = []
    svc = MijiaService(lambda e, d: events.append((e, d)), data_dir=tmp_path)
    svc.session = {'user_id': '12345678', 'ssecurity': SS, 'service_token': 't', 'region': 'cn'}
    svc.state = 'ready'
    svc.cards = golden_cards()
    fake = FakeCloud()
    svc._client = lambda: fake
    return svc, fake, events


def test_find(tmp_path):
    svc, _, _ = make_service(tmp_path)
    assert [c['name'] for c in svc.find('书房吸顶灯')] == ['书房吸顶灯']
    assert [c['name'] for c in svc.find('书房的吸顶灯')] == ['书房吸顶灯']
    lights = {c['id'] for c in svc.cards.values() if c['kind'] == 'light'}
    assert lights <= {c['id'] for c in svc.find('灯')}     # plus switch keys named 筒灯 / 灯带
    assert all(c['room'] == '客厅' for c in svc.find('客厅的空调'))
    assert svc.find('不存在的东西') == []


def run(coro):
    return asyncio.run(coro)


def call(svc, name, **args):
    async def go():
        svc.fresh = _noop_fresh
        return json.loads(await tools.handle(name, args, svc))
    return run(go())


async def _noop_fresh(_cards):
    return None


def test_tool_control_light_speaks_directly(tmp_path):
    svc, fake, events = make_service(tmp_path)
    svc._refresh_soon = _noop_fresh
    r = call(svc, 'self.home.control', device='书房吸顶灯', op='off')
    assert r == {'action': 'RESPONSE', 'response': '已关闭书房吸顶灯'}
    assert fake.sets and fake.sets[0][0][3] is False
    assert any(e == 'mijia' for e, _ in events)


def test_tool_control_plug_asks_first(tmp_path):
    svc, fake, _ = make_service(tmp_path)
    svc._refresh_soon = _noop_fresh
    plug = next(c for c in svc.cards.values() if c['kind'] == 'plug' and c['model'] == 'cuco.plug.cp2d')
    r = call(svc, 'self.home.control', device=plug['name'], op='off')
    assert r['need_confirm'] and not fake.sets
    r = call(svc, 'self.home.control', device=plug['name'], op='off', confirm=True)
    assert r['action'] == 'RESPONSE' and fake.sets


def test_tool_control_ambiguous_and_missing(tmp_path):
    svc, fake, _ = make_service(tmp_path)
    r = call(svc, 'self.home.control', device='灯', op='on')
    lights = [c for c in svc.cards.values() if c['kind'] == 'light']
    if len(lights) > 1:
        assert r['candidates'] and not fake.sets
    r = call(svc, 'self.home.control', device='火箭', op='on')
    assert r['success'] is False and '找不到' in r['reason']


def test_tool_read_only_lock(tmp_path):
    svc, fake, _ = make_service(tmp_path)
    lock = next(c for c in svc.cards.values() if c['kind'] == 'lock')
    r = call(svc, 'self.home.control', device=lock['name'], op='on')
    assert not fake.sets and r.get('failed')


def test_tool_list_and_state(tmp_path):
    svc, _, _ = make_service(tmp_path)
    r = call(svc, 'self.home.list_devices', room='书房')
    assert r['devices'] and all(d['room'] == '书房' for d in r['devices'])
    r = call(svc, 'self.home.get_state', device='书房吸顶灯')
    assert r['devices'][0]['detail']['on'] is True


def test_tools_when_logged_out(tmp_path):
    svc = MijiaService(lambda *a: None, data_dir=tmp_path)
    r = json.loads(run(tools.handle('self.home.list_devices', {}, svc)))
    assert r['success'] is False and '扫码' in r['reason']


def test_view_shape(tmp_path):
    svc, _, _ = make_service(tmp_path)
    v = svc.view()
    assert v['state'] == 'ready' and v['account']['user'] == '12****78'
    assert 'service_token' not in json.dumps(v) and 't' != v['account'].get('user')
    tile = v['rooms'][0]['cards'][0]
    assert {'id', 'name', 'summary', 'controls', 'state'} <= set(tile)


def test_session_file_is_private(tmp_path):
    import os
    from thinksmart_client.mijia.store import write_json
    write_json(tmp_path / 'session.json', {'a': 1}, private=True)
    assert os.stat(tmp_path / 'session.json').st_mode & 0o777 == 0o600
