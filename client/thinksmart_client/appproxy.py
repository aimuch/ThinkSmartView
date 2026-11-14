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
HOME = {'rack': '/setup#overview', 'hub': '/#/'}     # what each page opens on
HOP = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailers', 'transfer-encoding',
       'upgrade', 'host', 'content-length'}


TABS = ('mi', 'hub', 'rack')     # 家居 的分页：米家（设备自己画的）、智能中枢、机柜


def url(app):
    return f'http://127.0.0.1:{APPS[app]}{HOME[app]}'


def normalize_tabs(value):
    """{'show': 显示哪些分页（按 TABS 的顺序）, 'first': 打开「家居」先看哪个（'' = 上次看的）}。"""
    value = value if isinstance(value, dict) else {}
    show = value.get('show')
    show = [t for t in TABS if t in show] if isinstance(show, list) else list(TABS)
    first = value.get('first') if value.get('first') in TABS else ''
    return {'show': show or ['mi'], 'first': first if first in (show or ['mi']) else ''}


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
