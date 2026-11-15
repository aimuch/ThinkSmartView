#!/usr/bin/python3
"""BLE central for 当智能中枢的蓝牙节点 (设计画布第 73 块), run by hub_node.py as a child process with the
SYSTEM python3, like bt_helper.py: it uses the image's dbus-python and GLib, so the client's own environment
needs no D-Bus library.

It talks JSON lines. Requests on stdin {"id": n, "op": ..., ...}, answers on stdout {"id": n, "ok": true, ...}
or {"id": n, "ok": false, "err": "..."}; events {"ev": "notify", "conn", "chr", "data"} and
{"ev": "disconnected", "conn", "reason"}. The ops and fields are the SmartHub node protocol's
(docs/node-protocol.md there): scan, connect, read, write, notify, disconnect. Addresses are
aa:bb:cc:dd:ee:ff, 16-bit UUIDs four hex digits, others the full 128-bit form, data hex.

Requests run one at a time on a worker thread (BlueZ calls block); GLib's main loop delivers the
notifications and disconnections meanwhile.
"""
import json
import queue
import sys
import threading
import time

import dbus
import dbus.mainloop.glib
from gi.repository import GLib

BUS = 'org.bluez'
ADAPTER = 'org.bluez.Adapter1'
DEVICE = 'org.bluez.Device1'
SERVICE = 'org.bluez.GattService1'
CHAR = 'org.bluez.GattCharacteristic1'
PROPS = 'org.freedesktop.DBus.Properties'
OM = 'org.freedesktop.DBus.ObjectManager'
BASE = '-0000-1000-8000-00805f9b34fb'
MAX_CONN = 3
MAX_SCAN = 40

out_lock = threading.Lock()


def send(msg):
    with out_lock:
        sys.stdout.write(json.dumps(msg, ensure_ascii=False) + '\n')
        sys.stdout.flush()


def short(uuid):
    """'00002adb-0000-1000-8000-00805f9b34fb' -> '2adb'; other UUIDs stay whole (lower case)."""
    u = str(uuid).lower()
    return u[4:8] if u.endswith(BASE) and u.startswith('0000') else u


def full(uuid):
    u = str(uuid).lower()
    return f'0000{u}{BASE}' if len(u) == 4 else u


def dev_path(adapter, addr):
    return f"{adapter}/dev_{addr.upper().replace(':', '_')}"


class Node:
    def __init__(self):
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.bus = dbus.SystemBus()
        self.om = dbus.Interface(self.bus.get_object(BUS, '/'), OM)
        self.adapter = next((p for p, i in self.om.GetManagedObjects().items() if ADAPTER in i), None)
        if not self.adapter:
            raise RuntimeError('no Bluetooth adapter')
        self.conns = {}         # conn number -> device path
        self.notifying = {}     # characteristic path -> (conn, short uuid)
        self.seen = {}          # scanning: device path -> RSSI heard during this scan
        self.scanning = False
        self.bus.add_signal_receiver(self._changed, signal_name='PropertiesChanged', dbus_interface=PROPS,
                                     bus_name=BUS, path_keyword='path')

    # ---- events (GLib main loop) ----
    def _changed(self, iface, changed, _invalid, path=None):
        if iface == CHAR and 'Value' in changed and path in self.notifying:
            conn, uuid = self.notifying[path]
            send({'ev': 'notify', 'conn': conn, 'chr': uuid, 'data': bytes(changed['Value']).hex()})
        elif iface == DEVICE and changed.get('Connected') is False:
            for conn, p in list(self.conns.items()):
                if p == path:
                    del self.conns[conn]
                    for c in [c for c, (n, _) in self.notifying.items() if n == conn]:
                        del self.notifying[c]
                    send({'ev': 'disconnected', 'conn': conn, 'reason': 19})
        elif iface == DEVICE and self.scanning and 'RSSI' in changed:
            self.seen[path] = int(changed['RSSI'])

    # ---- helpers ----
    def _props(self, path, iface):
        return dbus.Interface(self.bus.get_object(BUS, path), PROPS).GetAll(iface)

    def _adapter(self):
        return dbus.Interface(self.bus.get_object(BUS, self.adapter), ADAPTER)

    def _chars(self, path):
        """[(char path, service uuid, char uuid, flags)] under a connected device."""
        objs = self.om.GetManagedObjects()
        svcs = {p: str(i[SERVICE]['UUID']) for p, i in objs.items() if SERVICE in i and p.startswith(path + '/')}
        out = []
        for p, i in objs.items():
            if CHAR in i and p.startswith(path + '/'):
                c = i[CHAR]
                out.append((p, svcs.get(str(c['Service'])), str(c['UUID']), [str(f) for f in c.get('Flags', [])]))
        return out

    def _char(self, conn, uuid):
        path = self.conns.get(conn)
        if not path:
            raise ValueError(f'no connection {conn}')
        want = full(uuid)
        for p, _svc, u, flags in self._chars(path):
            if u.lower() == want:
                return p, flags
        raise ValueError(f'no characteristic {uuid}')

    # ---- ops ----
    def op_scan(self, ms=3000, svc=None, **_):
        ms = max(500, min(30000, int(ms)))
        a = self._adapter()
        flt = {'Transport': 'le', 'DuplicateData': True}
        if svc:
            flt['UUIDs'] = dbus.Array([full(u) for u in svc], signature='s')
        a.SetDiscoveryFilter(flt)
        self.seen, self.scanning = {}, True
        started = False
        try:
            try:
                a.StartDiscovery()
                started = True
            except dbus.DBusException as e:
                if 'InProgress' not in str(e):
                    raise
            time.sleep(ms / 1000)
        finally:
            self.scanning = False
            if started:
                try:
                    a.StopDiscovery()
                except dbus.DBusException:
                    pass
        objs = self.om.GetManagedObjects()
        devices = []
        for path, rssi in self.seen.items():
            d = (objs.get(path) or {}).get(DEVICE)
            if not d:
                continue
            x = {'addr': str(d['Address']).lower(), 'type': 1 if str(d.get('AddressType')) == 'random' else 0,
                 'rssi': rssi, 'svc': [short(u) for u in d.get('UUIDs', [])]}
            if d.get('Name'):
                x['name'] = str(d['Name'])
            if d.get('ManufacturerData'):
                x['mfr'] = {str(int(k)): bytes(v).hex() for k, v in d['ManufacturerData'].items()}
            devices.append(x)
        devices.sort(key=lambda x: -x['rssi'])
        res = {'devices': devices[:MAX_SCAN]}
        if len(devices) > MAX_SCAN:
            res['truncated'] = True
        return res

    def op_connect(self, addr, addr_type=0, ms=10000, **_):
        path = dev_path(self.adapter, addr)
        for conn, p in self.conns.items():
            if p == path:
                return {'conn': conn, 'mtu': self._mtu(path), 'chrs': self._chr_list(path)}
        if len(self.conns) >= MAX_CONN:
            raise ValueError('too many connections')
        deadline = time.monotonic() + max(1000, min(30000, int(ms))) / 1000
        known = path in self.om.GetManagedObjects()
        if not known:   # BlueZ only connects to a device it has seen: ask for it by address (bluetoothd >= 5.49)
            try:
                self._adapter().ConnectDevice({'Address': addr.upper(), 'AddressType': 'random' if addr_type else 'public'})
            except dbus.DBusException as e:
                if 'AlreadyExists' not in str(e):
                    raise ValueError(f'connect failed: {e.get_dbus_message() or e}')
        dev = dbus.Interface(self.bus.get_object(BUS, path), DEVICE)
        try:
            if known:
                dev.Connect(timeout=max(1, deadline - time.monotonic()))
        except dbus.DBusException as e:
            raise ValueError(f'connect failed: {e.get_dbus_message() or e}')
        while time.monotonic() < deadline:
            p = self._props(path, DEVICE)
            if p.get('Connected') and p.get('ServicesResolved'):
                conn = next(n for n in range(1, MAX_CONN + 1) if n not in self.conns)
                self.conns[conn] = path
                return {'conn': conn, 'mtu': self._mtu(path), 'chrs': self._chr_list(path)}
            time.sleep(0.1)
        try:
            dev.Disconnect()
        except dbus.DBusException:
            pass
        raise ValueError('connect failed; timed out')

    def _mtu(self, path):
        for p, *_ in self._chars(path):
            try:
                return int(self._props(p, CHAR).get('MTU', 23))
            except dbus.DBusException:
                pass
        return 23

    def _chr_list(self, path):
        names = {'read': 'read', 'write': 'write', 'write-without-response': 'write_nr', 'notify': 'notify', 'indicate': 'indicate'}
        out = []
        for _p, svc, uuid, flags in self._chars(path):
            if svc and short(svc) in ('1800', '1801'):
                continue
            c = {'uuid': short(uuid), 'props': [names[f] for f in flags if f in names]}
            if svc:
                c['svc'] = short(svc)
            out.append(c)
        res, size = [], 0
        for c in out:
            size += len(json.dumps(c))
            if size > 3000:
                break
            res.append(c)
        return res

    def op_write(self, conn, chr, data, rsp=False, **_):
        p, _flags = self._char(int(conn), chr)
        dbus.Interface(self.bus.get_object(BUS, p), CHAR).WriteValue(
            dbus.Array(bytes.fromhex(data), signature='y'), {'type': 'request' if rsp else 'command'})
        return {}

    def op_read(self, conn, chr, **_):
        p, _flags = self._char(int(conn), chr)
        return {'data': bytes(dbus.Interface(self.bus.get_object(BUS, p), CHAR).ReadValue({})).hex()}

    def op_notify(self, conn, chr, on=True, **_):
        p, _flags = self._char(int(conn), chr)
        c = dbus.Interface(self.bus.get_object(BUS, p), CHAR)
        if on:
            self.notifying[p] = (int(conn), short(full(chr)))
            try:
                c.StartNotify()
            except dbus.DBusException as e:
                if 'InProgress' not in str(e) and 'AlreadyNotifying' not in str(e):
                    raise
        else:
            self.notifying.pop(p, None)
            try:
                c.StopNotify()
            except dbus.DBusException:
                pass
        return {}

    def op_disconnect(self, conn, **_):
        path = self.conns.pop(int(conn), None)
        if path:
            for c in [c for c, (n, _) in self.notifying.items() if n == int(conn)]:
                del self.notifying[c]
            try:
                dbus.Interface(self.bus.get_object(BUS, path), DEVICE).Disconnect()
            except dbus.DBusException:
                pass
        return {}

    def handle(self, req):
        op = req.get('op')
        fn = getattr(self, f'op_{op}', None)
        if not fn:
            return {'id': req.get('id'), 'ok': False, 'err': f'unknown op {op}'}
        args = {k: v for k, v in req.items() if k not in ('id', 'op', 't')}
        try:
            return {'id': req.get('id'), 'ok': True, **fn(**args)}
        except (ValueError, TypeError, KeyError) as e:
            return {'id': req.get('id'), 'ok': False, 'err': str(e)}
        except dbus.DBusException as e:
            return {'id': req.get('id'), 'ok': False, 'err': e.get_dbus_message() or str(e)}

    def disconnect_all(self):
        for conn in list(self.conns):
            self.op_disconnect(conn)


def main():
    try:
        node = Node()
    except Exception as e:  # noqa: BLE001
        send({'ev': 'error', 'err': str(e)})
        return 1
    jobs = queue.Queue()

    def worker():
        while True:
            req = jobs.get()
            if req is None:            # stdin closed: finish what was asked, then stop
                GLib.idle_add(loop.quit)
                break
            send(node.handle(req))

    def reader():
        for line in sys.stdin:
            try:
                req = json.loads(line)
            except ValueError:
                continue
            if isinstance(req, dict):
                jobs.put(req)
        jobs.put(None)

    loop = GLib.MainLoop()
    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=reader, daemon=True).start()
    send({'ev': 'ready', 'max_conn': MAX_CONN})
    try:
        loop.run()
    finally:
        node.disconnect_all()
    return 0


if __name__ == '__main__':
    sys.exit(main())
