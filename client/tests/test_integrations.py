"""工作台和蓝牙灯（设计画布第 38 块）：客户端经网关的 /kiosk/integrations 拿数据，机柜告警连续两次才念，恢复前不重复。

运行方法见 test_barge_in.py 文件头。
"""
import asyncio

from tests.test_looks import make_client as _make


def make_client(tmp_path, monkeypatch):
    c = _make(tmp_path, monkeypatch)
    c.integrations, c._rack_seen, c._rack_told = None, {}, set()
    return c


def rack(alerts):
    return {'available': {'rack_status': True}, 'rack_status': {'alerts': alerts, 'hosts': [], 'problems': list(alerts.values())}}


def test_rack_alerts_need_two_looks(tmp_path, monkeypatch):
    asyncio.run(_test_rack_alerts_need_two_looks(tmp_path, monkeypatch))


async def _test_rack_alerts_need_two_looks(tmp_path, monkeypatch):
    c = make_client(tmp_path, monkeypatch)
    said = []

    async def raise_alert(text, urgent):
        said.append(text)
    monkeypatch.setattr(c, '_raise_alert', raise_alert)
    replies = [rack({'host:a': 'PVE掉线了'})] * 3 + [rack({})] + [rack({'host:a': 'PVE掉线了'})] * 2
    calls = []

    async def hub_http(path, body=None):
        calls.append(path)
        return {'ok': True, **replies[len(calls) - 1]}
    monkeypatch.setattr(c, '_hub_http', hub_http)
    c.cfg['alerts'] = {**c.cfg['alerts'], 'rack': True}
    for _ in range(6):
        await c.refresh_integrations()
    assert said == ['PVE掉线了', 'PVE掉线了']          # 第二次看到才念；恢复以后再掉线，又念一次
    assert calls[0].startswith('/kiosk/integrations?device=')
    assert c.integrations['available'] == {'rack_status': True}


def test_rack_alerts_off_by_default(tmp_path, monkeypatch):
    asyncio.run(_test_rack_alerts_off_by_default(tmp_path, monkeypatch))


async def _test_rack_alerts_off_by_default(tmp_path, monkeypatch):
    c = make_client(tmp_path, monkeypatch)
    assert c.cfg['alerts']['rack'] is False
    said = []

    async def raise_alert(text, urgent):
        said.append(text)

    async def hub_http(path, body=None):
        return {'ok': True, **rack({'host:a': 'PVE掉线了'})}
    monkeypatch.setattr(c, '_raise_alert', raise_alert)
    monkeypatch.setattr(c, '_hub_http', hub_http)
    for _ in range(3):
        await c.refresh_integrations()
    assert said == []


def test_smarthub_act_updates_view(tmp_path, monkeypatch):
    asyncio.run(_test_smarthub_act_updates_view(tmp_path, monkeypatch))


async def _test_smarthub_act_updates_view(tmp_path, monkeypatch):
    c = make_client(tmp_path, monkeypatch)
    c.integrations = {'available': {'smart_hub': True}, 'smart_hub': {'devices': [{'id': 'v', 'on': False}, {'id': 'w', 'on': False}]}}
    sent = []

    async def hub_http(path, body=None):
        sent.append((path, body))
        return {'ok': True, 'device': {'id': 'v', 'on': True}}
    monkeypatch.setattr(c, '_hub_http', hub_http)
    r = await c.smarthub_act('v', 'on')
    assert r['ok'] and sent[0][0] == '/kiosk/smarthub' and sent[0][1]['id'] == 'v' and sent[0][1]['device'] == c.cfg['device_id']
    assert c.integrations['smart_hub']['devices'] == [{'id': 'v', 'on': True}, {'id': 'w', 'on': False}]
