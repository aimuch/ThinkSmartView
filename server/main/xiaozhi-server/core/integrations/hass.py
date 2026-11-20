"""[xiaozhi-hub] Home Assistant（设计画布第 84 块）：设备「家居」里的「Home Assistant」那一页和语音控制。

用 HA 的 REST API（长期访问令牌，请求头 Authorization: Bearer）：
  GET  /api/states                      所有实体的状态
  POST /api/template                    一次拿到每个实体在哪个区域（area_name）
  POST /api/services/<domain>/<service> 开关、调亮度、温度、窗帘……
设置和上游的 hass_* 插件共用 plugins.home_assistant（base_url、api_key）。令牌只在网关上。

只列能控制的（灯、开关插座、风扇、空调、窗帘、场景）和几类读数（温湿度、门窗、人体、门锁……），
别的（自动化、脚本、成百个诊断传感器）不列。门锁只读：语音和屏幕都不开锁。
"""

import re

from core.integrations.common import Cache, IntegrationError, request

_states = Cache(3)          # 几台设备同时看、语音刚改完：3 秒内只问一次
_areas = Cache(120)         # 区域很少变
AREA_TEMPLATE = "{% for s in states %}{{ s.entity_id }}\t{{ area_name(s.entity_id) or '' }}\n{% endfor %}"
OTHER = "其他"

CONTROL = ("light", "switch", "input_boolean", "fan", "climate", "cover")
READ_SENSOR = {"temperature": "℃", "humidity": "%", "pm25": "μg/m³", "power": "W", "illuminance": "lx", "co2": "ppm"}
READ_BINARY = {"door": ("开着", "关着"), "window": ("开着", "关着"), "garage_door": ("开着", "关着"),
               "motion": ("有人", "没人"), "occupancy": ("有人", "没人"), "presence": ("有人", "没人"),
               "moisture": ("漏水了", "干的"), "smoke": ("有烟", "正常"), "gas": ("有燃气", "正常")}
KIND = {"light": "light", "switch": "switch", "input_boolean": "switch", "fan": "fan", "climate": "climate",
        "cover": "cover", "lock": "lock", "sensor": "sensor", "binary_sensor": "binary"}
HVAC = {"off": "关", "heat": "制热", "cool": "制冷", "auto": "自动", "heat_cool": "自动", "dry": "除湿", "fan_only": "送风"}


def _ready(s: dict) -> dict:
    token = str(s.get("api_key") or "").strip()
    if not token or "你" in token:
        raise IntegrationError("网关后台还没填 Home Assistant 的长期访问令牌")
    return {"Authorization": f"Bearer {token}"}


def _url(s: dict) -> str:
    return str(s.get("base_url") or "").rstrip("/")


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fmt(v) -> str:
    return str(int(v)) if v is not None and float(v).is_integer() else f"{v:.1f}" if v is not None else "?"


def card(st: dict, area: str = "") -> dict:
    """一个实体 -> 屏幕上的一块（不列的返回 None）。"""
    eid = st.get("entity_id") or ""
    domain = eid.split(".", 1)[0]
    a = st.get("attributes") or {}
    state = st.get("state")
    name = a.get("friendly_name") or eid
    online = state not in ("unavailable", "unknown")
    c = {"id": eid, "name": name, "area": area or OTHER, "kind": KIND.get(domain), "online": online, "on": None,
         "summary": "", "controls": {}}
    if domain in ("light", "switch", "input_boolean", "fan"):
        c["on"] = state == "on"
        c["controls"]["power"] = True
        bits = ["开" if c["on"] else "关"]
        if domain == "light":
            modes = a.get("supported_color_modes") or []
            if any(m != "onoff" for m in modes):
                c["controls"]["brightness"] = True
                if c["on"] and a.get("brightness") is not None:
                    c["brightness"] = round(_num(a["brightness"]) / 255 * 100)
                    bits.append(f"亮度 {c['brightness']}%")
            if "color_temp" in modes:
                c["controls"]["cct"] = {"min": a.get("min_color_temp_kelvin") or 2700, "max": a.get("max_color_temp_kelvin") or 6500}
        if domain == "fan" and a.get("percentage") is not None:
            c["controls"]["percentage"] = True
            if c["on"]:
                bits.append(f"{a['percentage']}%")
        c["summary"] = " · ".join(bits)
    elif domain == "climate":
        c["on"] = state not in ("off", "unavailable", "unknown")
        c["controls"] = {"power": True, "target": {"min": a.get("min_temp") or 16, "max": a.get("max_temp") or 30,
                                                  "step": a.get("target_temp_step") or 1},
                         "modes": [m for m in a.get("hvac_modes") or [] if m in HVAC]}
        c["target"], c["current"], c["mode"] = _num(a.get("temperature")), _num(a.get("current_temperature")), state
        bits = [HVAC.get(state, state)]
        if c["on"] and c["target"] is not None:
            bits.append(f"设 {_fmt(c['target'])}℃")
        if c["current"] is not None:
            bits.append(f"室内 {_fmt(c['current'])}℃")
        c["summary"] = " · ".join(bits)
    elif domain == "cover":
        pos = a.get("current_position")
        c["on"] = state in ("open", "opening")
        c["controls"] = {"open": True, "position": pos is not None}
        c["position"] = pos
        c["summary"] = {"open": "开着", "closed": "关着", "opening": "正在开", "closing": "正在关"}.get(state, state)
        if pos is not None and state not in ("closed",):
            c["summary"] = f"开了 {pos}%"
    elif domain == "lock":
        c["summary"] = {"locked": "锁着", "unlocked": "没锁", "locking": "正在锁", "unlocking": "正在开"}.get(state, state)
        c["readonly"] = True
    elif domain == "sensor":
        unit = READ_SENSOR.get(a.get("device_class"))
        if not unit:
            return None
        v = _num(state)
        c["value"] = v
        c["summary"] = f"{_fmt(v)}{a.get('unit_of_measurement') or unit}" if v is not None else "没读数"
    elif domain == "binary_sensor":
        words = READ_BINARY.get(a.get("device_class"))
        if not words:
            return None
        c["on"] = state == "on"
        c["summary"] = words[0] if c["on"] else words[1]
    else:
        return None
    if not online:
        c["summary"] = "离线"
    return c


async def _area_map(s: dict) -> dict:
    async def fetch():
        try:
            text = await request("POST", _url(s) + "/api/template", headers=_ready(s), json={"template": AREA_TEMPLATE}, timeout=8, raw=True)
        except IntegrationError:
            return {}             # 老版本、模板被禁用：都放「其他」
        out = {}
        for line in str(text or "").splitlines():
            eid, _, area = line.partition("\t")
            if eid.strip():
                out[eid.strip()] = area.strip()
        return out
    return await _areas.get(_url(s), fetch)


async def overview(s: dict, fresh: bool = False) -> dict:
    """{'areas': [{'name', 'cards': [...]}], 'scenes': [{'id', 'name'}], 'count'}，按区域排，「其他」放最后。"""
    headers = _ready(s)
    if fresh:
        _states.drop(_url(s))

    async def fetch():
        states = await request("GET", _url(s) + "/api/states", headers=headers, timeout=8)
        if not isinstance(states, list):
            raise IntegrationError("Home Assistant 回的不是实体列表")
        return states
    states = await _states.get(_url(s), fetch)
    areas = await _area_map(s)
    groups, scenes = {}, []
    for st in states:
        eid = st.get("entity_id") or ""
        if eid.startswith("scene."):
            scenes.append({"id": eid, "name": (st.get("attributes") or {}).get("friendly_name") or eid})
            continue
        c = card(st, areas.get(eid, ""))
        if c:
            groups.setdefault(c["area"], []).append(c)
    order = sorted(groups, key=lambda n: (n == OTHER, n))
    rank = {"light": 0, "climate": 1, "fan": 2, "cover": 3, "switch": 4, "lock": 5, "binary": 6, "sensor": 7}
    out = [{"name": n, "cards": sorted(groups[n], key=lambda c: (rank.get(c["kind"], 9), c["name"]))} for n in order]
    return {"areas": out, "scenes": sorted(scenes, key=lambda x: x["name"]), "count": sum(len(a["cards"]) for a in out)}


def _all_cards(view: dict) -> list:
    return [c for a in view["areas"] for c in a["cards"]]


def _norm(text: str) -> str:
    return re.sub(r"[\s的个台盏]", "", str(text or "")).lower()


def find(view: dict, text: str):
    """语音说的名字 -> (那一块, 候选名字)。认全名、「区域 + 名字」、名字里含着说的词。"""
    want = _norm(text)
    if not want:
        return None, []
    cards = [c for c in _all_cards(view) if c["kind"] not in ("sensor", "binary")] or _all_cards(view)
    exact = [c for c in cards if want in (_norm(c["name"]), _norm(c["area"] + c["name"]))]
    if len(exact) == 1:
        return exact[0], []
    part = exact or [c for c in cards if _norm(c["name"]) in want or want in _norm(c["name"]) or want in _norm(c["area"] + c["name"])]
    if len(part) == 1:
        return part[0], []
    return None, [f"{c['area']}的{c['name']}" for c in part[:6]]


SERVICES = {
    ("light", "on"): ("light", "turn_on"), ("light", "off"): ("light", "turn_off"), ("light", "toggle"): ("light", "toggle"),
    ("fan", "on"): ("fan", "turn_on"), ("fan", "off"): ("fan", "turn_off"), ("fan", "toggle"): ("fan", "toggle"),
    ("climate", "on"): ("climate", "turn_on"), ("climate", "off"): ("climate", "turn_off"),
    ("cover", "open"): ("cover", "open_cover"), ("cover", "close"): ("cover", "close_cover"), ("cover", "stop"): ("cover", "stop_cover"),
}


async def act(s: dict, view: dict, entity: str, op: str, value=None) -> dict:
    """做一件事，返回改完的那一块。op：on、off、toggle、brightness（%）、cct（K）、percentage、target（℃）、mode、
    open、close、stop、position（%）、scene。"""
    headers = _ready(s)
    if op == "scene":
        if not entity.startswith("scene."):
            raise IntegrationError("这不是场景")
        await request("POST", _url(s) + "/api/services/scene/turn_on", headers=headers, json={"entity_id": entity})
        return {"id": entity, "name": entity, "summary": "已启动"}
    c = next((x for x in _all_cards(view) if x["id"] == entity), None)
    if c is None:
        raise IntegrationError("Home Assistant 里没有这个设备")
    domain = entity.split(".", 1)[0]
    if c.get("readonly"):
        raise IntegrationError(f"{c['name']}只能看，不能在这里开关")
    if c["kind"] in ("sensor", "binary"):
        raise IntegrationError(f"{c['name']}是读数，不能控制")
    data = {"entity_id": entity}
    if op in ("on", "off", "toggle") and domain in ("switch", "input_boolean"):
        svc = (domain, {"on": "turn_on", "off": "turn_off", "toggle": "toggle"}[op])
    elif op == "toggle" and domain == "climate":
        svc = ("climate", "turn_off" if c["on"] else "turn_on")
    elif op == "toggle" and domain == "cover":
        svc = ("cover", "close_cover" if c["on"] else "open_cover")
    elif (domain, op) in SERVICES:
        svc = SERVICES[(domain, op)]
    elif op == "brightness" and domain == "light":
        svc, data["brightness_pct"] = ("light", "turn_on"), max(1, min(100, int(_num(value) or 0)))
    elif op == "cct" and domain == "light":
        svc, data["color_temp_kelvin"] = ("light", "turn_on"), int(_num(value) or 4000)
    elif op == "percentage" and domain == "fan":
        svc, data["percentage"] = ("fan", "set_percentage"), max(0, min(100, int(_num(value) or 0)))
    elif op == "target" and domain == "climate":
        lim = c["controls"].get("target") or {}
        t = _num(value)
        if t is None:
            raise IntegrationError("要给出温度")
        svc, data["temperature"] = ("climate", "set_temperature"), max(lim.get("min", 16), min(lim.get("max", 30), t))
    elif op == "mode" and domain == "climate":
        if value not in (c["controls"].get("modes") or []):
            raise IntegrationError("这台空调没有这个模式")
        svc, data["hvac_mode"] = ("climate", "set_hvac_mode"), value
    elif op == "position" and domain == "cover":
        svc, data["position"] = ("cover", "set_cover_position"), max(0, min(100, int(_num(value) or 0)))
    else:
        raise IntegrationError("这个设备不能这样操作")
    await request("POST", _url(s) + f"/api/services/{svc[0]}/{svc[1]}", headers=headers, json=data)
    _states.drop(_url(s))
    try:
        st = await request("GET", _url(s) + f"/api/states/{entity}", headers=headers, timeout=5)
        return card(st, c["area"]) or c
    except IntegrationError:
        return c


def spoken_list(cards: list) -> str:
    return "；".join(f"{c['area']}的{c['name']}：{c['summary']}" for c in cards[:30]) or "没有设备"
