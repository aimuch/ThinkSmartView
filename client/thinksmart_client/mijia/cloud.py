"""Mi account QR login and the signed Mi cloud API (api.io.mi.com).

Ported from MenuBarMijia (MIT): windows/src/core/miCloud/{accountLogin,client,session}.ts and
Sources/MijiaCore/MiCloud/{MiAccountLogin,MiCloudClient}.swift. No passToken is kept: when the
serviceToken expires the user scans again (MenuBarMijia ADR-0004).
"""
import asyncio
import email.utils
import json
import logging
import random
import re
import string
import time
import urllib.parse

import aiohttp

from . import crypto

log = logging.getLogger(__name__)

REGIONS = {'cn': '中国大陆', 'de': '欧洲', 'i2': '印度', 'ru': '俄罗斯', 'sg': '新加坡 / 国际',
           'us': '美区'}
LOGIN_URL = 'https://account.xiaomi.com/longPolling/loginUrl'
START = '&&&START&&&'


class MiCloudError(Exception):
    """kind: 'expired' (scan again), 'http', 'api', 'bad', 'device' (offline / refused)."""

    def __init__(self, kind, message, code=None):
        super().__init__(message)
        self.kind = kind
        self.code = code


def normalize_region(region) -> str:
    r = str(region or '').strip().lower()
    return r if r in REGIONS else 'cn'


def api_base(region: str) -> str:
    r = normalize_region(region)
    return 'https://api.io.mi.com/app' if r == 'cn' else f'https://{r}.api.io.mi.com/app'


def parse_json_object(raw: str):
    raw = raw.strip()
    i = raw.find(START)
    if i >= 0:
        raw = raw[i + len(START):].strip()
    try:
        v = json.loads(raw)
    except ValueError:
        return None
    return v if isinstance(v, dict) else None


def id_str(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, str) and v:
        return v
    if isinstance(v, (int, float)):
        return str(int(v))
    return None


def parse_ticket(body: str) -> dict:
    j = parse_json_object(body)
    if not j:
        raise MiCloudError('bad', '小米账号服务响应不是 JSON')
    if not all(isinstance(j.get(k), str) for k in ('loginUrl', 'lp')):
        raise MiCloudError('bad', f'缺少二维码字段（code {j.get("code", "?")}）')
    try:
        timeout = float(j.get('timeout') or 300)
    except (TypeError, ValueError):
        timeout = 300
    return {'login_url': j['loginUrl'], 'lp': j['lp'], 'qr': j.get('qr', ''), 'timeout': timeout}


def parse_scan(body: str) -> dict:
    j = parse_json_object(body)
    if not j:
        raise MiCloudError('bad', '扫码结果不是 JSON')
    uid, ssecurity, location = id_str(j.get('userId')), j.get('ssecurity'), j.get('location')
    if not uid or not isinstance(ssecurity, str) or not isinstance(location, str):
        raise MiCloudError('bad', f'扫码结果缺少字段（code {j.get("code", "?")}）')
    return {'user_id': uid, 'c_user_id': id_str(j.get('cUserId')) or '', 'ssecurity': ssecurity,
            'location': location}


def token_from_set_cookie(headers):
    """(serviceToken, expires_epoch or None) from Set-Cookie headers, or (None, None)."""
    for raw in headers:
        m = re.search(r'(?:^|[;,\s])serviceToken=([^;\s,]+)', ' ' + raw)
        if not m:
            continue
        expires = None
        ma = re.search(r'max-age=(-?\d+)', raw, re.I)
        me = re.search(r'expires=([^;]+)', raw, re.I)
        if ma:
            expires = time.time() + max(0, int(ma.group(1)))
        elif me:
            try:
                expires = email.utils.parsedate_to_datetime(me.group(1).strip()).timestamp()
            except (TypeError, ValueError):
                expires = None
        return m.group(1), expires
    return None, None


class QRLogin:
    """start() -> ticket with login_url to show as a QR code; wait(ticket) -> session dict."""

    def __init__(self, http: aiohttp.ClientSession, region='cn'):
        self.http = http
        self.region = normalize_region(region)

    async def start(self) -> dict:
        query = crypto.form_encode([
            ('_qrsize', '480'), ('qs', '%3Fsid%3Dxiaomiio%26_json%3Dtrue'),
            ('callback', 'https://sts.api.io.mi.com/sts'), ('_hasLogo', 'false'), ('sid', 'xiaomiio'),
            ('serviceParam', ''), ('_locale', 'zh_CN'), ('_dc', str(int(time.time() * 1000)))])
        async with self.http.get(f'{LOGIN_URL}?{query}', timeout=aiohttp.ClientTimeout(total=15)) as r:
            body = await r.text()
            if r.status != 200:
                raise MiCloudError('http', f'小米账号服务返回 HTTP {r.status}', r.status)
        ticket = parse_ticket(body)
        ticket['deadline'] = time.time() + ticket['timeout']
        return ticket

    async def wait(self, ticket: dict) -> dict:
        scanned = None
        while scanned is None:
            if time.time() >= ticket['deadline']:
                raise MiCloudError('bad', '二维码已过期，请刷新')
            try:
                async with self.http.get(ticket['lp'], timeout=aiohttp.ClientTimeout(total=15)) as r:
                    body = await r.text()
                    status = r.status
            except asyncio.TimeoutError:
                continue
            if status == 200 and body.strip():
                scanned = parse_scan(body)
            else:
                await asyncio.sleep(1)
        token, expires = await self._service_token(scanned['location'])
        return {'user_id': scanned['user_id'], 'c_user_id': scanned['c_user_id'],
                'ssecurity': scanned['ssecurity'], 'service_token': token, 'region': self.region,
                'expires_at': expires, 'login_at': time.time()}

    async def _service_token(self, url):
        for _ in range(5):
            async with self.http.get(url, allow_redirects=False,
                                     headers={'Content-Type': 'application/x-www-form-urlencoded'},
                                     timeout=aiohttp.ClientTimeout(total=15)) as r:
                token, expires = token_from_set_cookie(r.headers.getall('Set-Cookie', []))
                if token:
                    return token, expires
                loc = r.headers.get('Location')
                if 300 <= r.status < 400 and loc:
                    url = urllib.parse.urljoin(url, loc)
                    continue
            break
        raise MiCloudError('bad', '登录成功但没拿到 serviceToken，请重试')


def _user_agent():
    return (''.join(random.choices(string.ascii_lowercase, k=18)) + '-'
            + ''.join(random.choices('ABCDE', k=13)) + ' APP/com.xiaomi.mihome APPV/10.5.201')


def unwrap(j: dict):
    code = j.get('code', 0)
    code = int(code) if isinstance(code, (int, float)) and not isinstance(code, bool) else 0
    if code == 0:
        return j.get('result')
    message = str(j.get('message') or j.get('desc') or '')
    low = message.lower()
    if code in (2, 3) or any(s in low for s in ('auth err', 'servicetoken_expired', 'token expired',
                                                  'invalid signature')):
        raise MiCloudError('expired', '米家登录已过期，请重新扫码', code)
    raise MiCloudError('api', f'米家云指令失败（{code}）{message}', code)


def decode_response(body: str, status: int, signed: str):
    if status in (401, 403):
        raise MiCloudError('expired', '米家登录已过期，请重新扫码', status)
    if status == 429:
        raise MiCloudError('http', '米家云请求太频繁', 429)
    plain = parse_json_object(body)
    if plain is not None:
        return unwrap(plain)
    if status != 200:
        raise MiCloudError('http', f'米家云 HTTP {status}', status)
    try:
        j = json.loads(crypto.decrypt(body, signed))
    except (ValueError, UnicodeDecodeError):
        raise MiCloudError('bad', '米家云响应无法解密')
    if not isinstance(j, dict):
        raise MiCloudError('bad', '米家云解密后不是 JSON')
    return unwrap(j)


def parse_device_list(result) -> list:
    out = []
    for rec in (result or {}).get('list') or [] if isinstance(result, dict) else []:
        if not isinstance(rec, dict):
            continue
        did, model = id_str(rec.get('did')), rec.get('model')
        if not did or not isinstance(model, str):
            continue
        split = (rec.get('extra') or {}).get('split') if isinstance(rec.get('extra'), dict) else None
        d = {'did': did, 'name': rec.get('name') if isinstance(rec.get('name'), str) else model,
             'model': model, 'online': rec.get('isOnline') is not False,
             'spec_type': rec.get('spec_type') if isinstance(rec.get('spec_type'), str) else None}
        if isinstance(split, dict) and id_str(split.get('parentId')) and isinstance(split.get('moduleId'), int):
            d['split_parent'], d['split_siid'] = id_str(split['parentId']), split['moduleId']
        out.append(d)
    return out


def parse_homes(result) -> list:
    """[{id, name, shared, rooms: [{id, name, dids}]}]; devices with no room go in a nameless room."""
    homes, seen = [], set()
    if not isinstance(result, dict):
        return homes
    for key, shared in (('homelist', False), ('share_home_list', True)):
        for h in result.get(key) or []:
            if not isinstance(h, dict):
                continue
            hid = id_str(h.get('id')) or ''
            if hid in seen:
                continue
            seen.add(hid)
            rooms = []
            for r in h.get('roomlist') or []:
                if isinstance(r, dict) and id_str(r.get('id')):
                    rooms.append({'id': id_str(r['id']), 'name': r.get('name') or '',
                                  'dids': [d for d in map(id_str, r.get('dids') or []) if d]})
            loose = [d for d in map(id_str, h.get('dids') or []) if d]
            if loose:
                rooms.append({'id': f'home_{hid}', 'name': '', 'dids': loose})
            homes.append({'id': hid, 'name': h.get('name') or '我的家', 'shared': shared, 'rooms': rooms})
    return homes


class MiCloud:
    def __init__(self, http: aiohttp.ClientSession, session: dict, timeout=10):
        self.http = http
        self.session = session
        self.timeout = timeout
        self.ua = _user_agent()

    async def request(self, api: str, data):
        path = '/' + api.strip('/')
        s = self.session
        nonce = crypto.make_nonce()
        signed = crypto.signed_nonce(s['ssecurity'], nonce)
        fields = crypto.encrypt_params(path, crypto.compact_json(data), s['ssecurity'], nonce)
        headers = {
            'User-Agent': self.ua, 'x-xiaomi-protocal-flag-cli': 'PROTOCAL-HTTP2',
            'MIOT-ENCRYPT-ALGORITHM': 'ENCRYPT-RC4', 'Accept-Encoding': 'identity',
            'Content-Type': 'application/x-www-form-urlencoded',
            'Cookie': '; '.join([f'userId={s["user_id"]}', f'serviceToken={s["service_token"]}',
                                 f'yetAnotherServiceToken={s["service_token"]}', 'locale=zh_CN',
                                 'timezone=GMT+08:00', 'is_daylight=0', 'dst_offset=0',
                                 'channel=MI_APP_STORE'])}
        try:
            async with self.http.post(api_base(s.get('region')) + path, data=crypto.form_encode(fields),
                                      headers=headers,
                                      timeout=aiohttp.ClientTimeout(total=self.timeout)) as r:
                body = await r.text()
                status = r.status
        except asyncio.TimeoutError:
            raise MiCloudError('http', '米家云没有响应')
        except aiohttp.ClientError as e:
            raise MiCloudError('http', f'连不上米家云：{e.__class__.__name__}')
        return decode_response(body, status, signed)

    async def homes(self) -> list:
        data = {'fg': True, 'fetch_share': True, 'fetch_share_dev': True, 'limit': 300, 'app_ver': 7}
        try:
            return parse_homes(await self.request('v2/homeroom/gethome_merged', {**data, 'plat_form': 0}))
        except MiCloudError as e:
            if e.kind == 'expired':
                raise
            return parse_homes(await self.request('v2/homeroom/gethome', data))

    async def devices(self, dids=None) -> list:
        try:
            return await self._devices_paged(dids)
        except MiCloudError as e:
            if e.kind == 'expired' or dids:
                raise
            return parse_device_list(await self.request('home/device_list', {
                'getVirtualModel': True, 'getHuamiDevices': 1, 'get_split_device': False,
                'support_smart_home': True}))

    async def devices_by_dids(self, dids) -> list:
        out = []
        for i in range(0, len(dids), 150):
            out += await self._devices_paged(list(dids[i:i + 150]))
        return _unique(out)

    async def _devices_paged(self, dids=None) -> list:
        out, start = [], ''
        for _ in range(20):
            data = {'get_split_device': True, 'support_smart_home': True, 'limit': 200}
            if dids:
                data['dids'] = dids
            if start:
                data['start_did'] = start
            res = await self.request('v2/home/device_list_page', data)
            out += parse_device_list(res)
            nxt = id_str((res or {}).get('next_start_did')) if isinstance(res, dict) else None
            if not isinstance(res, dict) or res.get('has_more') is not True or not nxt or nxt == start:
                break
            start = nxt
        return _unique(out)

    async def get_props(self, keys) -> dict:
        """keys: [(did, siid, piid)] -> {(did, siid, piid): value}; failed items are left out."""
        values = {}
        keys = list(keys)
        for i in range(0, len(keys), 100):
            res = await self.request('miotspec/prop/get', {
                'params': [{'did': d, 'siid': s, 'piid': p} for d, s, p in keys[i:i + 100]]})
            for it in res if isinstance(res, list) else []:
                if isinstance(it, dict) and it.get('code', 0) == 0 and 'value' in it:
                    did = id_str(it.get('did'))
                    if did and isinstance(it.get('siid'), int) and isinstance(it.get('piid'), int):
                        values[(did, it['siid'], it['piid'])] = it['value']
        return values

    async def set_props(self, writes):
        """writes: [(did, siid, piid, value)]."""
        res = await self.request('miotspec/prop/set', {
            'params': [{'did': d, 'siid': s, 'piid': p, 'value': v} for d, s, p, v in writes]})
        for it in res if isinstance(res, list) else []:
            if isinstance(it, dict) and isinstance(it.get('code'), int) and it['code'] < 0:
                raise MiCloudError('device', _device_error(it['code']), it['code'])

    async def action(self, did, siid, aiid, inputs=()):
        res = await self.request('miotspec/action', {
            'params': {'did': did, 'siid': siid, 'aiid': aiid, 'in': list(inputs)}})
        if isinstance(res, dict) and isinstance(res.get('code'), int) and res['code'] < 0:
            raise MiCloudError('device', _device_error(res['code']), res['code'])
        return res


def _device_error(code):
    if code == -704042011:
        return '设备离线'
    if code in (-704002000, -704030013):
        return '设备不支持这个设置'
    return f'设备没有响应（{code}）'


def _unique(records):
    seen, out = set(), []
    for r in records:
        if r['did'] not in seen:
            seen.add(r['did'])
            out.append(r)
    return out
