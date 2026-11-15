"""当智能中枢的蓝牙节点（设计画布第 73 块）：假的智能中枢（节点端口）+ 假的蓝牙辅助程序，走一遍握手、心跳、扫描、通知、不支持的操作。"""
import asyncio
import json
import sys

import pytest

from thinksmart_client import hub_node

FAKE_HELPER = r'''
import json, sys
print(json.dumps({"ev": "ready", "max_conn": 3}), flush=True)
for line in sys.stdin:
    req = json.loads(line)
    if req["op"] == "scan":
        print(json.dumps({"id": req["id"], "ok": True, "devices": [{"addr": "a4:c1:38:00:00:03", "type": 0, "rssi": -50, "svc": ["1828"]}]}), flush=True)
    elif req["op"] == "connect":
        print(json.dumps({"id": req["id"], "ok": True, "conn": 1, "mtu": 185, "chrs": []}), flush=True)
        print(json.dumps({"ev": "notify", "conn": 1, "chr": "2ade", "data": "0101"}), flush=True)
    else:
        print(json.dumps({"id": req["id"], "ok": False, "err": "nope"}), flush=True)
'''


@pytest.mark.asyncio
async def test_node_session(tmp_path, monkeypatch):
    helper = tmp_path / 'helper.py'
    helper.write_text(FAKE_HELPER)
    monkeypatch.setattr(hub_node, 'HELPER', helper)
    monkeypatch.setattr(hub_node, 'SYSTEM_PYTHON', sys.executable)
    got, done = [], asyncio.Event()

    async def router(reader, writer):
        async def send(m):
            writer.write((json.dumps(m) + '\n').encode())
            await writer.drain()

        async def recv():
            m = json.loads(await asyncio.wait_for(reader.readline(), 10))
            got.append(m)
            return m
        hello = await recv()
        assert hello['t'] == 'hello' and hello['node'] == 'thinksmart-2d43' and hello['token'] == 'tok' and hello['proto'] == 1
        assert hello['caps'] == ['identify'] and hello['board']['name'] == 'ThinkSmart View · 书房中枢屏'
        await send({'t': 'welcome', 'proto': 1, 'ping': 15})
        await send({'t': 'ping'})
        assert (await recv())['t'] == 'pong'
        await send({'t': 'req', 'id': 7, 'op': 'scan', 'ms': 1000})
        r = await recv()
        assert r == {'t': 'res', 'id': 7, 'ok': True, 'devices': [{'addr': 'a4:c1:38:00:00:03', 'type': 0, 'rssi': -50, 'svc': ['1828']}]}
        await send({'t': 'req', 'id': 8, 'op': 'connect', 'addr': 'a4:c1:38:00:00:03', 'addr_type': 0, 'ms': 5000})
        msgs = [await recv(), await recv()]
        assert {'t': 'res', 'id': 8, 'ok': True, 'conn': 1, 'mtu': 185, 'chrs': []} in msgs
        assert {'t': 'evt', 'ev': 'notify', 'conn': 1, 'chr': '2ade', 'data': '0101'} in msgs
        await send({'t': 'req', 'id': 9, 'op': 'ota', 'url': 'x'})
        r = await recv()
        assert r['ok'] is False and 'not supported' in r['err']
        done.set()
        writer.close()

    server = await asyncio.start_server(router, '127.0.0.1', 0)
    port = server.sockets[0].getsockname()[1]
    seen = []

    async def hub_http(path):
        return {'ok': True, 'host': '127.0.0.1', 'port': port, 'token': 'tok', 'node': 'thinksmart-2d43', 'name': '书房中枢屏'}
    n = hub_node.HubNode(hub_http, '22:1d:e1:e9:2d:43', '0.1.697', lambda ev, v: seen.append((ev, dict(v))))
    await n.set_enabled(True)
    await asyncio.wait_for(done.wait(), 20)
    assert any(ev == 'hub_node' and v['state'] == 'online' for ev, v in seen)
    await n.set_enabled(False)
    assert n.view()['state'] == 'off' and n.proc is None
    server.close()


@pytest.mark.asyncio
async def test_waits_for_the_token():
    async def hub_http(path):
        return {'ok': False, 'error': '网关后台「外部服务 › 家里的系统 › 智能中枢 › 设置」里还没填节点令牌'}
    seen = []
    n = hub_node.HubNode(hub_http, 'dev', '0.1.697', lambda ev, v: seen.append(dict(v)))
    await n.set_enabled(True)
    await asyncio.sleep(0.2)
    assert n.view()['state'] == 'waiting' and '节点令牌' in n.view()['error']
    await n.set_enabled(False)
