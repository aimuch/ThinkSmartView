"""[xiaozhi-hub] 换线路（设计画布第 56 块）：一个域名解析到的那台服务器连不上时，换同一个服务的别的服务器。

起因：国内 DNS 把 api.open-meteo.com 解析到 51.81.167.44，这台 ping 得通但 443 端口不通，天气全挂；
Cloudflare 的 DNS 给的是另一台（188.40.99.226），能用。国外的 DoH 也连不上、国内 DoH 给的还是那台，
所以直接用 UDP 问国外的几个 DNS，再加上内置的备用地址，逐个试 443 能不能连上，挑第一个能连的记住 PIN_TTL 秒。

用法：resp = await net_lines.get(client, url, params=...)，和 client.get 一样，只是连不上（ConnectError /
ConnectTimeout）时换线路再试一次。换线路时按 IP 连，TLS 的 SNI 和 Host 头仍是原来的域名，证书照样校验。
后台「外部服务」也可以给某个域名手动指定一个地址（services.line），指定了就只用它。
"""
import asyncio
import random
import socket
import struct
import time
from urllib.parse import urlsplit, urlunsplit

import httpx

RESOLVERS = ("1.1.1.1", "9.9.9.9", "208.67.222.222", "8.8.8.8")
BACKUP = {"api.open-meteo.com": ("188.40.99.226",)}
PIN_TTL = 6 * 3600
CONNECT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout)

_pins = {}       # 域名 → {"ip", "until", "source"}
_skipped = {}    # 域名 → {ip: 原因}（最近一次找线路时跳过的）


def _query(host: str, qid: int) -> bytes:
    q = b"".join(bytes([len(p)]) + p.encode() for p in host.strip(".").split(".")) + b"\0"
    return struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0) + q + struct.pack(">HH", 1, 1)


def _skip_name(data: bytes, i: int) -> int:
    while True:
        n = data[i]
        if n == 0:
            return i + 1
        if n & 0xC0 == 0xC0:
            return i + 2
        i += n + 1


def parse_a(data: bytes, qid: int) -> list:
    """DNS 应答里的 A 记录（只认 qid 对得上的）。"""
    if len(data) < 12:
        return []
    rid, flags, qd, an = struct.unpack(">HHHH", data[:8])
    if rid != qid or flags & 0x000F:
        return []
    i = 12
    for _ in range(qd):
        i = _skip_name(data, i) + 4
    out = []
    for _ in range(an):
        i = _skip_name(data, i)
        typ, cls, _ttl, ln = struct.unpack(">HHIH", data[i:i + 10])
        i += 10
        if typ == 1 and cls == 1 and ln == 4:
            out.append(socket.inet_ntoa(data[i:i + 4]))
        i += ln
    return out


async def udp_a(host: str, server: str, timeout: float = 2.0) -> list:
    loop = asyncio.get_running_loop()
    qid = random.randint(1, 0xFFFF)
    fut = loop.create_future()

    class P(asyncio.DatagramProtocol):
        def datagram_received(self, data, addr):
            if not fut.done():
                fut.set_result(data)

        def error_received(self, exc):
            if not fut.done():
                fut.set_exception(exc)

    transport, _ = await loop.create_datagram_endpoint(P, remote_addr=(server, 53))
    try:
        transport.sendto(_query(host, qid))
        return parse_a(await asyncio.wait_for(fut, timeout), qid)
    except (OSError, asyncio.TimeoutError):
        return []
    finally:
        transport.close()


async def system_a(host: str) -> list:
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, 443, family=socket.AF_INET, type=socket.SOCK_STREAM)
    except OSError:
        return []
    return [i[4][0] for i in infos]


async def tcp_ok(ip: str, port: int = 443, timeout: float = 2.5) -> bool:
    try:
        _, w = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout)
    except (OSError, asyncio.TimeoutError):
        return False
    w.close()
    return True


async def candidates(host: str) -> list:
    """[(ip, 从哪来的)]：本机 DNS、几个国外 DNS（UDP）、内置备用，去重，按这个顺序。"""
    found = await asyncio.gather(system_a(host), *(udp_a(host, r) for r in RESOLVERS))
    out, seen = [], set()
    for src, ips in zip(("本机 DNS",) + tuple(f"DNS {r}" for r in RESOLVERS), found):
        for ip in ips:
            if ip not in seen:
                seen.add(ip)
                out.append((ip, src))
    for ip in BACKUP.get(host, ()):
        if ip not in seen:
            seen.add(ip)
            out.append((ip, "内置备用"))
    return out


async def find(host: str):
    """挑一个 443 连得上的 (ip, 来源)；都不通返回 None。"""
    cands = await candidates(host)
    oks = await asyncio.gather(*(tcp_ok(ip) for ip, _ in cands))
    _skipped[host] = {ip: "443 端口不通" for (ip, _), ok in zip(cands, oks) if not ok}
    for (ip, src), ok in zip(cands, oks):
        if ok:
            return ip, src
    return None


def _via_ip(url: str, host: str, ip: str) -> str:
    u = urlsplit(url)
    netloc = ip + (f":{u.port}" if u.port else "")
    return urlunsplit((u.scheme, netloc, u.path, u.query, u.fragment))


async def _get_via(client, url, host, ip, **kw):
    headers = dict(kw.pop("headers", None) or {})
    headers["Host"] = host
    return await client.get(_via_ip(url, host, ip), headers=headers, extensions={"sni_hostname": host}, **kw)


def pinned(host: str):
    p = _pins.get(host)
    if p and p["until"] > time.time():
        return p
    _pins.pop(host, None)
    return None


def info(host: str) -> dict:
    """后台「外部服务」显示用：现在走哪个地址、从哪查到的、跳过了哪些。"""
    from core.agent import services

    manual = services.line(host)
    p = pinned(host)
    return {"host": host, "manual": manual, "ip": manual or (p and p["ip"]) or "",
            "source": "手动指定" if manual else (p and p["source"]) or "",
            "skipped": [{"ip": ip, "why": why} for ip, why in (_skipped.get(host) or {}).items()]}


async def get(client: httpx.AsyncClient, url: str, **kw):
    from core.agent import services

    host = urlsplit(url).hostname or ""
    manual = services.line(host)
    if manual:
        return await _get_via(client, url, host, manual, **kw)
    p = pinned(host)
    if p:
        try:
            return await _get_via(client, url, host, p["ip"], **dict(kw))
        except CONNECT_ERRORS:
            _pins.pop(host, None)
    try:
        return await client.get(url, **kw)
    except CONNECT_ERRORS as first:
        if not host or host.replace(".", "").isdigit():
            raise
        hit = await find(host)
        if not hit:
            raise first
        ip, src = hit
        resp = await _get_via(client, url, host, ip, **kw)
        _pins[host] = {"ip": ip, "until": time.time() + PIN_TTL, "source": src}
        return resp
