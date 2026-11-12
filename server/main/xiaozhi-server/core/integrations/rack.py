"""机柜：主机和 Ping 检测从 Prometheus 读（不用令牌）；填了 rack-screen 的令牌，再读它自己的检测项。

Prometheus 的查询照 rack-screen（smart-screen 仓库）的写法：node_exporter 的 up、CPU、内存、温度，
blackbox 的 probe_success。macOS 上的 node_exporter 没有 MemAvailable，用 free + inactive 算。
"""

import time
import urllib.parse

from core.integrations.common import Cache, IntegrationError, request

_cache = Cache(10)
_down_since = {}   # 实例 -> 第一次看到掉线的时间（网关一直开着，够用）

Q = {
    "up": 'up{job=~".*node.*"}',
    "name": "node_uname_info",
    "cpu": '100 * (1 - avg by (instance) (rate(node_cpu_seconds_total{mode="idle"}[2m])))',
    "mem": "100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)"
           " or 100 * (1 - (node_memory_free_bytes + node_memory_inactive_bytes) / node_memory_total_bytes)",
    "temp": "max by (instance) (node_hwmon_temp_celsius) or max by (instance) (node_thermal_zone_temp)",
    "disk": '100 * max by (instance) (1 - node_filesystem_avail_bytes{fstype!~"tmpfs|overlay|squashfs|ramfs"}'
            ' / node_filesystem_size_bytes{fstype!~"tmpfs|overlay|squashfs|ramfs"})',
    "probe": "probe_success",
    "probe_ms": "probe_duration_seconds * 1000",
}
HOT_TEMP, BUSY_CPU = 70, 90


async def _query(s: dict, expr: str) -> list:
    url = f"{s['prometheus']}/api/v1/query?" + urllib.parse.urlencode({"query": expr})
    data = await request("GET", url, timeout=6)
    if not isinstance(data, dict) or data.get("status") != "success":
        raise IntegrationError("Prometheus 回的不对")
    return (data.get("data") or {}).get("result") or []


def _by_instance(rows: list) -> dict:
    out = {}
    for r in rows:
        try:
            out[r["metric"].get("instance", "")] = float(r["value"][1])
        except (KeyError, TypeError, ValueError, IndexError):
            continue
    return out


async def _monitors(s: dict) -> list:
    """rack-screen 自己的检测项（要令牌）。"""
    data = await request("GET", f"{s['url']}/api/monitors", headers={"X-Token": s["token"]}, timeout=6)
    out = []
    for m in (data or {}).get("monitors") or []:
        if not isinstance(m, dict) or m.get("state") == "paused":
            continue
        out.append({"name": str(m.get("name") or m.get("id") or ""), "state": m.get("state") or "nodata",
                    "ms": m.get("latency_ms"), "msg": str(m.get("msg") or "")[:80]})
    return out


async def summary(s: dict, fresh: bool = False) -> dict:
    """-> {hosts, probes, monitors, problems, up, total, monitor_error}。"""
    async def fetch():
        res = {}
        for key, expr in Q.items():
            res[key] = await _query(s, expr)
        names = {r["metric"].get("instance", ""): r["metric"].get("nodename") or "" for r in res["name"]}
        cpu, mem, temp, disk = (_by_instance(res[k]) for k in ("cpu", "mem", "temp", "disk"))
        now = time.time()
        hosts = []
        for inst, up in sorted(_by_instance(res["up"]).items()):
            ok = up >= 1
            if ok:
                _down_since.pop(inst, None)
            else:
                _down_since.setdefault(inst, now)
            name = names.get(inst) or inst.rsplit(":", 1)[0]
            h = {"instance": inst, "name": name, "up": ok,
                 "cpu": None if inst not in cpu else round(cpu[inst]),
                 "mem": None if inst not in mem else round(mem[inst]),
                 "temp": None if inst not in temp else round(temp[inst]),
                 "disk": None if inst not in disk else round(disk[inst])}
            h["down_minutes"] = None if ok else int((now - _down_since[inst]) // 60)
            h["hot"] = ok and ((h["temp"] or 0) >= HOT_TEMP or (h["cpu"] or 0) >= BUSY_CPU)
            hosts.append(h)
        ms = _by_instance(res["probe_ms"])
        probes = [{"name": inst, "up": v >= 1, "ms": None if inst not in ms else round(ms[inst])}
                  for inst, v in sorted(_by_instance(res["probe"]).items())]
        out = {"hosts": hosts, "probes": probes, "monitors": [], "monitor_error": None}
        if s.get("token"):
            try:
                out["monitors"] = await _monitors(s)
            except IntegrationError as e:
                out["monitor_error"] = f"rack-screen：{e}"
        problems = [f"{h['name']}掉线了" for h in hosts if not h["up"]]
        problems += [f"{h['name']} {h['temp']}℃" if (h["temp"] or 0) >= HOT_TEMP else f"{h['name']} CPU {h['cpu']}%"
                     for h in hosts if h["hot"]]
        problems += [f"Ping {p['name']} 不通" for p in probes if not p["up"]]
        problems += [f"{m['name']}检测失败" for m in out["monitors"] if m["state"] == "down"]
        out.update(problems=problems, up=sum(h["up"] for h in hosts), total=len(hosts))
        out["alerts"] = alert_keys(out)
        return out
    if fresh:
        _cache.drop(s["prometheus"])
    return await _cache.get(s["prometheus"], fetch)


def alert_keys(data: dict) -> dict:
    """告警用：会让设备主动提醒的问题 -> 说的话（掉线、Ping 不通、检测失败；高温不算）。"""
    out = {f"host:{h['instance']}": f"{h['name']}掉线了" for h in data.get("hosts", []) if not h["up"]}
    out.update({f"probe:{p['name']}": f"Ping {p['name']} 不通了" for p in data.get("probes", []) if not p["up"]})
    out.update({f"mon:{m['name']}": f"{m['name']}检测失败了" for m in data.get("monitors", []) if m["state"] == "down"})
    return out


def spoken(data: dict, host: str = "") -> str:
    hosts = data.get("hosts", [])
    if host:
        want = host.lower().replace(" ", "")
        hit = [h for h in hosts if want in h["name"].lower().replace(" ", "") or want in h["instance"]]
        if not hit:
            return f"机柜里没找到「{host}」。有这些主机：" + "、".join(h["name"] for h in hosts) + "。"
        lines = []
        for h in hit:
            if not h["up"]:
                lines.append(f"{h['name']}掉线了（{h['down_minutes'] or 0} 分钟）")
                continue
            bits = [f"CPU {h['cpu']}%" if h["cpu"] is not None else "", f"内存 {h['mem']}%" if h["mem"] is not None else "",
                    f"{h['temp']}℃" if h["temp"] is not None else "", f"磁盘最满的用了 {h['disk']}%" if h["disk"] is not None else ""]
            lines.append(f"{h['name']}在线，" + "，".join(b for b in bits if b))
        return "；".join(lines) + "。"
    text = f"机柜一共 {data['total']} 台主机，{data['up']} 台在线。"
    if data["problems"]:
        text += "有这些问题：" + "；".join(data["problems"]) + "。"
    else:
        text += "没有告警。"
    temps = [f"{h['name']} {h['temp']}℃" for h in hosts if h["up"] and h["temp"] is not None]
    if temps:
        text += "温度：" + "，".join(temps) + "。"
    if data.get("monitor_error"):
        text += data["monitor_error"] + "。"
    return text
