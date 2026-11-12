"""智能中枢（SmartHub 的 btrouter）：读蓝牙设备，发开关、亮度、色温。

接口见 SmartHub 的 docs/api.md：GET /api/devices，POST /api/devices/{id}/{action}，
请求头 Authorization: Bearer <api.token>。灯的 meta.kind 是 light，可调电源是 psu。
"""

import re

from core.integrations.common import Cache, IntegrationError, request

_cache = Cache(5)
ACTIONS = {"on", "off", "toggle", "brightness", "cct", "set"}


def _headers(s: dict) -> dict:
    return {"Authorization": f"Bearer {s['token']}"} if s.get("token") else {}


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def tidy(d: dict) -> dict:
    """接口里的一台设备 -> 屏幕和语音用的样子。"""
    meta, state = d.get("meta") or {}, d.get("state") or {}
    kind = meta.get("kind") or ("light" if "Light" in str(d.get("type")) else "other")
    link = d.get("link") or {}
    route = link.get("route") or {}
    via = str(route.get("radio") or (link.get("trying") or {}).get("radio") or "")
    out = {"id": str(d.get("id")), "name": str(d.get("name") or d.get("id")), "kind": kind,
           "group": bool(meta.get("group")), "actions": sorted(a for a in (d.get("actions") or {}) if a in ACTIONS),
           "via": via.replace("node:", "") if via else "", "connecting": bool(link.get("trying"))}
    if kind == "light":
        out.update(on=state.get("on"), brightness=_num(state.get("brightness")), kelvin=_num(state.get("kelvin")),
                   mode=state.get("mode"), cct_min=_num(meta.get("cct_min")) or 2700, cct_max=_num(meta.get("cct_max")) or 6500)
    elif kind == "psu":
        out.update(on=state.get("output"), online=state.get("online"), v_set=_num(state.get("v_set")), i_set=_num(state.get("i_set")),
                   v_out=_num(state.get("v_out")), i_out=_num(state.get("i_out")), p_out=_num(state.get("p_out")),
                   error=state.get("error"), model=meta.get("model"), v_max=_num(meta.get("v_max")), i_max=_num(meta.get("i_max")))
    else:
        out["on"] = state.get("on")
    out["summary"] = summary(out)
    return out


def summary(d: dict) -> str:
    if d["kind"] == "light":
        if d.get("on") is False:
            return "关"
        if d.get("on") is None:
            return "还不知道"
        parts = ["开"] if d.get("brightness") is None else [f"{round(d['brightness'])}%"]
        if d.get("mode") == "cct" and d.get("kelvin"):
            parts.append(f"{round(d['kelvin'])}K")
        elif d.get("mode") == "hsi":
            parts.append("彩色")
        return " · ".join(parts)
    if d["kind"] == "psu":
        if d.get("online") is False:
            return "连不上" + (f"：{d['error']}" if d.get("error") else "")
        if d.get("on") is False:
            return f"输出关 · 设 {d['v_set']:.2f} V" if d.get("v_set") is not None else "输出关"
        bits = [f"{d[k]:.2f} {u}" for k, u in (("v_out", "V"), ("i_out", "A")) if d.get(k) is not None]
        if d.get("p_out") is not None:
            bits.append(f"{d['p_out']:.1f} W")
        return " · ".join(bits) or "输出开"
    return {True: "开", False: "关"}.get(d.get("on"), "")


async def devices(s: dict, fresh: bool = False) -> list:
    async def fetch():
        data = await request("GET", f"{s['url']}/api/devices", headers=_headers(s), timeout=6)
        if not isinstance(data, list):
            raise IntegrationError("智能中枢回的不是设备列表")
        return [tidy(d) for d in data if isinstance(d, dict) and d.get("id")]
    if fresh:
        _cache.drop(s["url"])
    return await _cache.get(s["url"], fetch)


def _params(device: dict, action: str, value) -> dict:
    if action in ("on", "off", "toggle"):
        return {}
    if action == "brightness":
        if isinstance(value, dict) and "delta" in value:
            return {"delta": max(-100, min(100, int(value["delta"])))}
        return {"value": max(1, min(100, int(round(float(value)))))}
    if action == "cct":
        if isinstance(value, dict) and "delta" in value:
            return {"delta": int(value["delta"])}
        lo, hi = device.get("cct_min") or 2700, device.get("cct_max") or 6500
        return {"kelvin": max(lo, min(hi, int(round(float(value) / 100) * 100)))}
    if action == "set":
        out = {}
        for k, key in (("v", "voltage"), ("i", "current")):
            if isinstance(value, dict) and value.get(k) is not None:
                out[key] = float(value[k])
        if not out:
            raise IntegrationError("要给出电压或电流")
        return out
    raise IntegrationError(f"不支持的动作：{action}")


async def act(s: dict, device_id: str, action: str, value=None) -> dict:
    """-> 动作后的设备（tidy 过的）。"""
    devs = await devices(s)
    device = next((d for d in devs if d["id"] == device_id), None)
    if device is None:
        raise IntegrationError("智能中枢里没有这个设备")
    if action not in device["actions"]:
        raise IntegrationError(f"{device['name']}不支持这个操作")
    try:
        params = _params(device, action, value)
    except (TypeError, ValueError):
        raise IntegrationError("数值不对")
    data = await request("POST", f"{s['url']}/api/devices/{device_id}/{action}", headers=_headers(s), json=params, timeout=15)
    _cache.drop(s["url"])
    if isinstance(data, dict) and data.get("ok") is False:
        raise IntegrationError(str(data.get("error") or "智能中枢说没做成"))
    state = (data or {}).get("state") if isinstance(data, dict) else None
    if isinstance(state, dict):
        raw = {"id": device_id, "name": device["name"], "type": "", "actions": {a: "" for a in device["actions"]},
               "state": state, "meta": {"kind": device["kind"], "group": device["group"],
                                        "cct_min": device.get("cct_min"), "cct_max": device.get("cct_max")}}
        return tidy(raw)
    return device


def _norm(text: str) -> str:
    return re.sub(r"[\s#·._\-]+", "", str(text or "").lower())


def find(devs: list, name: str):
    """说的名字 -> (设备, 候选)：唯一命中返回设备；含糊时设备为 None、候选是几个名字。"""
    want = _norm(name)
    if not want:
        return None, []
    if want in ("全部灯", "所有灯", "全部", "所有的灯", "灯", "全部的灯"):
        group = [d for d in devs if d["group"] and d["kind"] == "light"]
        if group:
            return group[0], []
    exact = [d for d in devs if _norm(d["name"]) == want or d["id"].lower() == want]
    if len(exact) == 1:
        return exact[0], []
    hits = [d for d in devs if want in _norm(d["name"]) or (_norm(d["name"]) and _norm(d["name"]) in want)]
    if len(hits) == 1:
        return hits[0], []
    return None, [d["name"] for d in (hits or devs)][:6]


def spoken_list(devs: list) -> str:
    if not devs:
        return "智能中枢里没有设备。"
    return "；".join(f"{d['name']}：{d['summary'] or '不知道'}" for d in devs) + "。"
