"""当智能中枢的蓝牙节点（设计画布第 73 块）：这台设备用自己的蓝牙替智能中枢（SmartHub 的 btrouter）连灯和电源，
和 ESP32 节点一样出现在它的「节点管理」里。

The SmartHub node protocol (its docs/node-protocol.md): the node dials the router's TCP port (7766) and speaks
JSON lines — hello/welcome, ping/pong, requests {"t": "req", "id", "op", ...} answered with {"t": "res", "id", "ok"},
events {"t": "evt", "ev": ...}. The BLE ops (scan, connect, read, write, notify, disconnect) go to
ble_node_helper.py, a child process on the system python3 with BlueZ over D-Bus (like bt_helper.py); "identify"
flashes the page. Everything else is answered "not supported" — hello's caps only list what this node does.

How to reach the router and the node token come from the hub (/kiosk/hubnode): the user fills the token in the
hub's admin, and this device's switch (config 'hub_node') turns it on. Off, or no token: nothing runs.
"""
import asyncio
import json
import logging
import pathlib
import shutil
import time

from . import bg

log = logging.getLogger(__name__)

HELPER = pathlib.Path(__file__).with_name('ble_node_helper.py')
SYSTEM_PYTHON = '/usr/bin/python3'
BLE_OPS = {'scan', 'connect', 'read', 'write', 'notify', 'disconnect'}
CAPS = ['identify']
RETRY = (5, 10, 30, 60)       # seconds before dialing again after a drop, growing
SETTINGS_RETRY = 120          # no token yet / not connected to the smart hub on this device: ask the hub again
SILENT = 60                   # the router pings every 15 s: nothing for this long, the link is dead


class HubNode:
    def __init__(self, hub_http, device_id, version, publish, enabled=False):
        self.hub_http = hub_http          # async (path) -> dict, the hub's /kiosk API
        self.device_id = device_id
        self.version = version
        self.publish = publish            # publish(event, data) to the page
        self.enabled = enabled            # the user's switch (config 'hub_node')
        self.state = 'off'                # off | waiting | connecting | online | error
        self.error = ''
        self.node = ''
        self.server = ''
        self.since = None                 # online since (time.time())
        self.served = 0                   # BLE requests answered since online
        self.proc = None
        self._task = None
        self._writer = None
        self._helper_ids = 0
        self._pending = {}                # helper request id -> future
        self._conns = set()

    # ---- what the page shows ----

    def view(self):
        return {'on': self.enabled, 'state': self.state, 'error': self.error, 'node': self.node, 'server': self.server,
                'since': self.since, 'served': self.served, 'conns': len(self._conns)}

    def _set(self, state, error=''):
        if (state, error) != (self.state, self.error):
            self.state, self.error = state, error
            if state != 'online':
                self.since = None
            log.info('hub node: %s %s', state, error)
            self.publish('hub_node', self.view())

    # ---- switch ----

    def start(self):
        if self.enabled and not self._task:
            self._task = bg.spawn(self._run())

    async def set_enabled(self, on):
        self.enabled = bool(on)
        if self.enabled:
            self.start()
            self._set('waiting')
        else:
            await self.stop()
            self._set('off')
        return self.view()

    async def stop(self):
        task, self._task = self._task, None
        if task:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        await self._stop_helper()

    # ---- the BLE helper ----

    async def _start_helper(self):
        if self.proc and self.proc.returncode is None:
            return True
        if not shutil.which(SYSTEM_PYTHON) or not HELPER.exists():
            self._set('error', '这台设备没有系统的 python3，开不了蓝牙节点')
            return False
        self.proc = await asyncio.create_subprocess_exec(
            SYSTEM_PYTHON, str(HELPER), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=1 << 20)
        first = json.loads(await asyncio.wait_for(self.proc.stdout.readline(), 15) or b'{}')
        if first.get('ev') != 'ready':
            self._set('error', f"蓝牙用不了：{first.get('err') or '辅助程序没起来'}")
            await self._stop_helper()
            return False
        bg.spawn(self._read_helper(self.proc))
        return True

    async def _stop_helper(self):
        proc, self.proc = self.proc, None
        if proc and proc.returncode is None:
            try:
                proc.stdin.close()
                await asyncio.wait_for(proc.wait(), 5)   # it disconnects everything on the way out
            except Exception:   # noqa: BLE001
                proc.kill()
        for f in self._pending.values():
            if not f.done():
                f.set_result({'ok': False, 'err': 'bluetooth helper stopped'})
        self._pending.clear()
        self._conns.clear()

    async def _read_helper(self, proc):
        async for line in proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if 'id' in msg and msg['id'] in self._pending:
                f = self._pending.pop(msg['id'])
                if not f.done():
                    f.set_result(msg)
            elif msg.get('ev') in ('notify', 'disconnected'):
                if msg['ev'] == 'disconnected':
                    self._conns.discard(msg.get('conn'))
                await self._send({'t': 'evt', **msg})
        for f in self._pending.values():
            if not f.done():
                f.set_result({'ok': False, 'err': 'bluetooth helper stopped'})

    async def _ble(self, req, timeout):
        if not await self._start_helper():
            return {'ok': False, 'err': 'bluetooth unavailable'}
        self._helper_ids += 1
        hid = self._helper_ids
        fut = asyncio.get_running_loop().create_future()
        self._pending[hid] = fut
        args = {k: v for k, v in req.items() if k not in ('t', 'id')}
        self.proc.stdin.write((json.dumps({**args, 'id': hid}) + '\n').encode())
        await self.proc.stdin.drain()
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self._pending.pop(hid, None)
            return {'ok': False, 'err': 'timed out'}

    # ---- the link to the router ----

    async def _send(self, msg):
        w = self._writer
        if w is None or w.is_closing():
            return
        w.write((json.dumps(msg, ensure_ascii=False, separators=(',', ':')) + '\n').encode())
        try:
            await w.drain()
        except (ConnectionError, OSError):
            pass

    async def _run(self):
        tries = 0
        while self.enabled:
            try:
                cfg = await self.hub_http('/kiosk/hubnode?device=' + self.device_id)
            except Exception as e:  # noqa: BLE001
                cfg = {'ok': False, 'error': f'连不上小麦网关：{type(e).__name__}'}
            if not cfg or not cfg.get('ok'):
                self._set('waiting', (cfg or {}).get('error') or '网关没给节点设置')
                await asyncio.sleep(SETTINGS_RETRY)
                continue
            self.node, self.server = cfg['node'], f"{cfg['host']}:{cfg['port']}"
            ok = await self._session(cfg)
            tries = 0 if ok else tries + 1
            await self._stop_helper()             # a new session starts with no connections
            if self.enabled:
                await asyncio.sleep(RETRY[min(tries, len(RETRY) - 1)])

    async def _session(self, cfg):
        """One connection to the router. True when it got as far as the welcome."""
        self._set('connecting')
        try:
            reader, self._writer = await asyncio.wait_for(asyncio.open_connection(cfg['host'], int(cfg['port']), limit=1 << 20), 10)
        except (OSError, asyncio.TimeoutError) as e:
            self._set('error', f"连不上智能中枢的节点端口 {self.server}（{type(e).__name__}）")
            return False
        local_ip = self._writer.get_extra_info('sockname', ('', 0))[0]
        await self._send({'t': 'hello', 'node': cfg['node'], 'token': cfg['token'], 'proto': 1, 'fw': self.version,
                          'chip': 'apq8053', 'max_conn': 3, 'ip': local_ip, 'server': self.server, 'caps': CAPS,
                          'mode': 'node', 'reset': {'reason': 'sw'},
                          'board': {'id': 'thinksmart-view', 'name': f"ThinkSmart View · {cfg.get('name') or ''}".rstrip(' ·'),
                                    'screen': None, 'keys': [], 'rgb': 0, 'ir': {'tx': False, 'rx': False}}})
        welcomed, started = False, time.monotonic()
        try:
            while True:
                line = await asyncio.wait_for(reader.readline(), SILENT)
                if not line:
                    break
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                t = msg.get('t')
                if t == 'welcome':
                    welcomed = True
                    self.since, self.served = time.time(), 0
                    self._set('online')
                elif t == 'error':
                    self._set('error', '节点令牌不对：网关后台智能中枢的「节点令牌」要和智能中枢 nodes.token 一样'
                              if msg.get('err') == 'unauthorized' else f"智能中枢拒绝了：{msg.get('err')}")
                    return welcomed
                elif t == 'ping':
                    await self._send({'t': 'pong', 'up': int(time.monotonic() - started)})
                elif t == 'req':
                    bg.spawn(self._request(msg))
        except asyncio.TimeoutError:
            log.info('hub node: nothing from the router for %d s', SILENT)
        except (ConnectionError, OSError) as e:
            log.info('hub node: link dropped: %s', e)
        finally:
            w, self._writer = self._writer, None
            if w:
                w.close()
        if self.enabled:
            self._set('connecting' if welcomed else 'error', '' if welcomed else self.error or '智能中枢断开了连接')
        return welcomed

    async def _request(self, req):
        op, rid = req.get('op'), req.get('id')
        if op in BLE_OPS:
            timeout = 40 if op in ('scan', 'connect') else 15
            res = await self._ble(req, timeout)
            out = {'t': 'res', 'id': rid, 'ok': bool(res.get('ok'))}
            out.update({k: v for k, v in res.items() if k not in ('id', 'ok')})
            if op == 'connect' and out['ok']:
                self._conns.add(out.get('conn'))
            elif op == 'disconnect':
                self._conns.discard(req.get('conn'))
            self.served += 1
            if op in ('connect', 'disconnect'):
                self.publish('hub_node', self.view())
            await self._send(out)
        elif op == 'identify':
            ms = max(1000, min(30000, int(req.get('ms') or 5000)))
            self.publish('identify', {'ms': ms, 'by': '智能中枢'})
            await self._send({'t': 'res', 'id': rid, 'ok': True})
        else:
            await self._send({'t': 'res', 'id': rid, 'ok': False, 'err': f'not supported: {op}'})
