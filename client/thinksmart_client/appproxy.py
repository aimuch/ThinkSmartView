"""机柜（rack-screen）和智能中枢自己的网页，放在「家居」里（设计画布第 62 块）.

The two pages ask for paths from their own root (/api/..., /static/app.js), so each gets a port of its
own on this device, loopback only: 127.0.0.1:8091 is rack-screen, 8092 the smart hub. Everything that
comes in is passed to the hub's /kiosk/proxy/<app>/<path> over the existing tunnel (hub_http); the hub
adds the token from its plugin settings (it never comes here), drops what keeps a page out of a frame
and refuses the risky calls (passwords, backups, firmware). Bodies stream both ways as they come:
gzip stays gzip, the smart hub's event stream (SSE) stays open.
"""
import logging

from aiohttp import ClientSession, ClientTimeout, web

log = logging.getLogger(__name__)

APPS = {'rack': 8091, 'hub': 8092}
HOME = {'rack': '/kiosk', 'hub': '/#/'}     # what each page opens on: rack-screen's /kiosk is its screen picture drawn for this window
HOP = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailers', 'transfer-encoding',
       'upgrade', 'host', 'content-length'}


WORK_TABS = ('desk', 'hub', 'rack', 'repo')   # 工作台的分页：智能桌面、智能中枢、机柜、仓库同步
SPLIT_MIN, SPLIT_MAX = 0.3, 0.7  # 工作台两栏的左栏占多少（拖中间的竖条）


def url(app):
    return f'http://127.0.0.1:{APPS[app]}{HOME[app]}'


IDLE_CHOICES = (1, 3, 5, 10, 30, 0)   # 没人碰几分钟回首页（设计画布第 89 块）：0 不回首页
KEEP_CHOICES = (0, 5, 10, 30, -1)   # 切走后保留机柜页、智能中枢网页几分钟（设计画布第 79 块）：0 不保留，-1 一直保留


def normalize_work(value, legacy_first=''):
    """工作台的设置（设计画布第 75 块）：{'order': 四页的顺序, 'hidden': 不显示的页, 'first': 先看哪页（'' = 上次看的）,
    'rack': 机柜页放 'screen'（机柜屏的画面）或 'list', 'hub': 智能中枢页放 'tiles' 或 'web'（它自己的网页）,
    'split': {'desk', 'repo'} 左栏占多少, 'idle_min': 没人碰几分钟回首页（IDLE_CHOICES，0 不回；老的 idle_close
    true/false 读成 3/0）, 'idle_close': idle_min 不是 0（页面和老代码用）, 'keep': 切走后网页保留几分钟（KEEP_CHOICES）,
    'usage': 智能桌面的 AI 用量 'show' / 'hide'（设计画布第 91 块）, 'usage_order': 服务 id 的顺序（空 = 剩得少的在前）,
    'usage_hidden': 不显示的服务 id（设计画布第 92 块）}。legacy_first：以前存在 home_tabs 里的「先看」。"""
    v = value if isinstance(value, dict) else {}
    order = [x for x in v.get('order') or [] if x in WORK_TABS]
    order = list(dict.fromkeys(order)) + [x for x in WORK_TABS if x not in order]
    hidden = [x for x in v.get('hidden') or [] if x in WORK_TABS]
    first = v.get('first', legacy_first)
    idle = v.get('idle_min')
    if idle not in IDLE_CHOICES or isinstance(idle, bool):
        idle = 0 if v.get('idle_close') is False else 3
    split = {}
    for k in ('desk', 'repo'):
        s = (v.get('split') or {}).get(k)
        if isinstance(s, (int, float)) and not isinstance(s, bool):
            split[k] = round(min(SPLIT_MAX, max(SPLIT_MIN, float(s))), 3)
    return {'order': order, 'hidden': list(dict.fromkeys(hidden)), 'first': first if first in WORK_TABS else '',
            'rack': v.get('rack') if v.get('rack') in ('screen', 'list') else 'screen',
            'hub': v.get('hub') if v.get('hub') in ('tiles', 'web') else 'tiles',
            'split': split, 'idle_min': idle, 'idle_close': idle != 0,
            'keep': v.get('keep') if v.get('keep') in KEEP_CHOICES and not isinstance(v.get('keep'), bool) else 10,
            'usage': 'hide' if v.get('usage') == 'hide' else 'show',
            'usage_order': _ids(v.get('usage_order')), 'usage_hidden': _ids(v.get('usage_hidden'))}


def _ids(value):
    """AI 用量的服务 id 列表：去重，最多 16 个，每个最多 16 个字符。"""
    out = []
    for x in value if isinstance(value, list) else []:
        if isinstance(x, str) and x and len(x) <= 16 and x not in out:
            out.append(x)
    return out[:16]


class AppProxy:
    def __init__(self, hub_http, device_id):
        self.hub_http = hub_http.rstrip('/')
        self.device_id = device_id
        self.session = None
        self.runners = []

    def _client(self):
        if self.session is None or self.session.closed:
            self.session = ClientSession(auto_decompress=False, timeout=ClientTimeout(total=None, connect=5, sock_connect=5))
        return self.session

    def handler(self, app):
        async def handle(request):
            target = f'{self.hub_http}/kiosk/proxy/{app}{request.path}' + (f'?{request.query_string}' if request.query_string else '')
            headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP}
            headers['X-Device-Id'] = self.device_id
            body = await request.read() if request.can_read_body else None
            try:
                upstream = await self._client().request(request.method, target, headers=headers, data=body, allow_redirects=False)
            except Exception as e:
                log.warning('%s page: the hub did not answer (%s)', app, type(e).__name__)
                return web.Response(status=502, text='连不上小麦网关', content_type='text/plain')
            out = web.StreamResponse(status=upstream.status, reason=upstream.reason)
            for k, v in upstream.headers.items():
                if k.lower() not in HOP:
                    out.headers.add(k, v)
            if upstream.headers.get('Content-Length') and 'text/event-stream' not in upstream.headers.get('Content-Type', ''):
                out.content_length = int(upstream.headers['Content-Length'])
            try:
                await out.prepare(request)
                async for chunk in upstream.content.iter_any():
                    await out.write(chunk)
                await out.write_eof()
            except (ConnectionResetError, RuntimeError):
                pass          # the page went away (another tab): close the hub's side too
            finally:
                upstream.release()
            return out
        return handle

    async def start(self):
        for app, port in APPS.items():
            a = web.Application(client_max_size=10 * 1024 * 1024)
            a.router.add_route('*', '/{tail:.*}', self.handler(app))
            runner = web.AppRunner(a, access_log=None)
            await runner.setup()
            try:
                await web.TCPSite(runner, '127.0.0.1', port).start()
            except OSError as e:
                log.warning('%s page: port %s is taken (%s)', app, port, e)
                await runner.cleanup()
                continue
            self.runners.append(runner)

    async def stop(self):
        for r in self.runners:
            await r.cleanup()
        self.runners = []
        if self.session and not self.session.closed:
            await self.session.close()
