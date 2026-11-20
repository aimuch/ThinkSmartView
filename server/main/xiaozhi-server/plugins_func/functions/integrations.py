"""[xiaozhi-hub] 接入家里别的服务的语音命令（core/integrations，设计画布第 38 块）：
smart_hub（智能中枢的蓝牙灯）、rack_status（机柜）、repo_status（仓库同步）、desk_hub（Mac 上的 DeskHub）、
home_assistant（Home Assistant，设计画布第 84 块）。

查询类交给大模型组织语言（REQLLM）；控制成功直接念结果（RESPONSE），快一些。
"""

from config.logger import setup_logging
from core.integrations import common, deskhub, hass, rack, repos, smarthub
from plugins_func.register import Action, ActionResponse, ToolType, register_function

TAG = __name__
logger = setup_logging()


def _fn(name: str, desc: str, props: dict, required=()) -> dict:
    return {"type": "function", "function": {"name": name, "description": desc,
                                             "parameters": {"type": "object", "properties": props, "required": list(required)}}}


SMART_HUB_DESC = _fn(
    "smart_hub",
    "控制和查询智能中枢里的蓝牙设备（amaran 补光灯、台灯等蓝牙灯，瑞登可调电源）。用户说开灯、关灯、调亮、调暗、"
    "调色温（暖一点、冷一点、4300K）、或问这些灯和电源的状态时使用。不是米家设备，也不是本机屏幕亮度。",
    {"action": {"type": "string", "enum": ["status", "on", "off", "toggle", "brightness", "brighter", "dimmer", "cct", "warmer", "cooler"],
                "description": "status 查状态；brightness 设亮度（value 1-100）；brighter/dimmer 调亮/调暗一档；cct 设色温（value 开尔文，如 4300）；warmer/cooler 暖一点/冷一点"},
     "device": {"type": "string", "description": "设备名，例如「补光灯」「台灯」；说「全部灯」「所有灯」时填「全部灯」；查全部状态时可以不填"},
     "value": {"type": "number", "description": "亮度百分比或色温（开尔文）"}},
    ["action"])

RACK_DESC = _fn(
    "rack_status",
    "查家里机柜和服务器的状态：哪台主机掉线、CPU、内存、温度、磁盘，Ping 和网站检测有没有失败。用户问服务器、NAS、"
    "路由器、PVE、机柜正不正常、多少度时使用。",
    {"host": {"type": "string", "description": "只问某一台时填它的名字或 IP，例如「PVE」「NAS」；问整体时不填"}})

REPO_DESC = _fn(
    "repo_status",
    "查 repo-manager 的 git 仓库同步情况：多少仓库、失败几个、有没有疑似删库、哪些需要处理。用户问仓库同步、"
    "代码仓库备份时使用。", {})

DESK_DESC = _fn(
    "desk_hub",
    "书房电脑桌（Mac 上的 DeskHub）：把显示器切到某个输入源（例如切到 Windows、切回 Mac）；查 AI 额度和今天的用量"
    "（Claude、Magpie）；查电脑「提醒事项」里的待办，或把一条待办标成完成。不是小麦自己的待办清单。",
    {"action": {"type": "string", "enum": ["switch", "inputs", "usage", "todos", "done"],
                "description": "switch 切显示器输入源；inputs 查有哪些输入源、现在在哪个；usage 查 AI 用量；todos 查电脑上的待办；done 完成一条待办"},
     "name": {"type": "string", "description": "switch 时是输入源名字（如 Windows、HDMI 1、Mac）；done 时是待办的标题"}},
    ["action"])


HASS_DESC = _fn(
    "home_assistant",
    "控制和查询 Home Assistant 里的设备：灯、开关插座、风扇、空调、窗帘、场景，以及温湿度、门窗、人体传感器和门锁的状态。"
    "用户说开关某个房间的灯或电器、空调调温度或模式、窗帘开关、启动场景、问某个房间多少度时使用。"
    "米家设备用米家的工具，智能中枢的蓝牙灯用 smart_hub；不知道是哪边的先用 status 查。门锁只查不开。",
    {"action": {"type": "string", "enum": ["status", "on", "off", "toggle", "brightness", "temperature", "mode", "open", "close", "stop", "position", "scene"],
                "description": "status 查状态；brightness 设灯亮度（value 1-100）；temperature 设空调温度（value ℃）；mode 设空调模式（value: cool/heat/auto/dry/fan_only/off）；"
                               "open/close/stop 窗帘；position 窗帘开到多少（value 0-100）；scene 启动场景（device 填场景名）"},
     "device": {"type": "string", "description": "设备名，可以带房间，例如「客厅的灯」「卧室空调」「窗帘」；场景时填场景名；查全部状态时可以不填"},
     "value": {"description": "亮度、温度、位置的数，或空调模式"}},
    ["action"])


def _not_ready(conn, name: str):
    s = common.settings(conn.config, name)
    if not common.ready(conn.config, name):
        return ActionResponse(Action.REQLLM, f"{common.LABELS[name]}还没接入：请用一句话告诉用户，要在网关后台的插件里点「设置」填好。", None), s
    return None, s


@register_function("smart_hub", SMART_HUB_DESC, ToolType.SYSTEM_CTL)
async def smart_hub(conn, action: str = "status", device: str = "", value=None):
    bad, s = _not_ready(conn, "smart_hub")
    if bad:
        return bad
    try:
        devs = await smarthub.devices(s, fresh=action == "status")
        if action == "status":
            pick = devs
            if device:
                hit, cands = smarthub.find(devs, device)
                pick = [hit] if hit else [d for d in devs if d["name"] in cands]
            return ActionResponse(Action.REQLLM, "智能中枢的设备：" + smarthub.spoken_list(pick), None)
        hit, cands = smarthub.find(devs, device or "全部灯")
        if not hit:
            return ActionResponse(Action.REQLLM, f"没确定是哪个设备，可选：{'、'.join(cands)}。请问用户要哪一个。", None)
        step = {"brighter": ("brightness", {"delta": 20}), "dimmer": ("brightness", {"delta": -20}),
                "warmer": ("cct", {"delta": -500}), "cooler": ("cct", {"delta": 500})}
        act, arg = step.get(action, (action, value))
        if act in ("brightness", "cct") and arg is None:
            return ActionResponse(Action.REQLLM, "要给出亮度百分比或色温。请问用户要调到多少。", None)
        if act in ("on", "off") and hit["kind"] == "psu":
            return ActionResponse(Action.REQLLM, "可调电源的输出要在屏幕上的家居页里点开确认，语音不直接开关。请这样告诉用户。", None)
        done = await smarthub.act(s, hit["id"], act, arg)
        word = {"on": "已打开", "off": "已关掉", "toggle": "已切换"}.get(act, "已调好")
        tail = f"，现在 {done['summary']}" if act not in ("on", "off") and done.get("summary") else ""
        return ActionResponse(Action.RESPONSE, None, f"{word}{done['name']}{tail}。")
    except common.IntegrationError as e:
        logger.bind(tag=TAG).warning(f"智能中枢: {e}")
        return ActionResponse(Action.REQLLM, f"智能中枢没做成：{e}。请用一句话如实告诉用户。", None)


@register_function("rack_status", RACK_DESC, ToolType.SYSTEM_CTL)
async def rack_status(conn, host: str = ""):
    _, s = _not_ready(conn, "rack_status")   # 不填也能用默认的本机 Prometheus
    try:
        data = await rack.summary(s)
    except common.IntegrationError as e:
        return ActionResponse(Action.REQLLM, f"读不到机柜状态（Prometheus {e}）。请如实告诉用户。", None)
    return ActionResponse(Action.REQLLM, rack.spoken(data, host or ""), None)


@register_function("repo_status", REPO_DESC, ToolType.SYSTEM_CTL)
async def repo_status(conn):
    _, s = _not_ready(conn, "repo_status")
    try:
        data = await repos.summary(s)
    except common.IntegrationError as e:
        return ActionResponse(Action.REQLLM, f"读不到仓库同步情况（repo-manager {e}）。请如实告诉用户。", None)
    return ActionResponse(Action.REQLLM, repos.spoken(data), None)


@register_function("desk_hub", DESK_DESC, ToolType.SYSTEM_CTL)
async def desk_hub(conn, action: str = "inputs", name: str = ""):
    bad, _ = _not_ready(conn, "desk_hub")
    if bad:
        return bad
    v = deskhub.hub.view()
    if action in ("usage", "todos", "inputs"):
        return ActionResponse(Action.REQLLM, deskhub.spoken(v, action), None)
    if not v.get("online"):
        return ActionResponse(Action.RESPONSE, None, "Mac 上的 DeskHub 没连上网关，没法操作。")
    if action == "switch":
        hit, cands = deskhub.find(v["sources"], name, "name")
        if not hit:
            return ActionResponse(Action.REQLLM, f"没确定切到哪个输入源，可选：{'、'.join(cands) or '没有'}。请问用户。", None)
        if hit["id"] == v.get("current"):
            return ActionResponse(Action.RESPONSE, None, f"显示器已经在{hit['name']}了。")
        out = await deskhub.hub.command("switch", hit["id"])
        return ActionResponse(Action.RESPONSE, None, out["text"] + "。")
    if action == "done":
        hit, cands = deskhub.find(v["todos"], name, "title")
        if not hit:
            return ActionResponse(Action.REQLLM, f"没找到这条待办，电脑上有：{'、'.join(cands) or '没有'}。请问用户是哪一条。", None)
        out = await deskhub.hub.command("todo_done", hit["id"])
        return ActionResponse(Action.RESPONSE, None, out["text"] + "。")
    return ActionResponse(Action.REQLLM, "不支持的操作。", None)


@register_function("home_assistant", HASS_DESC, ToolType.SYSTEM_CTL)
async def home_assistant(conn, action: str = "status", device: str = "", value=None):
    """[xiaozhi-hub] Home Assistant（设计画布第 84 块，core/integrations/hass.py）。"""
    bad, s = _not_ready(conn, "home_assistant")
    if bad:
        return bad
    try:
        view = await hass.overview(s, fresh=action == "status")
        if action == "scene":
            want = hass._norm(device)
            hit = [x for x in view["scenes"] if want and (want in hass._norm(x["name"]) or hass._norm(x["name"]) in want)]
            if len(hit) != 1:
                names = "、".join(x["name"] for x in (hit or view["scenes"])[:8]) or "没有场景"
                return ActionResponse(Action.REQLLM, f"没确定是哪个场景，可选：{names}。请问用户要哪一个。", None)
            await hass.act(s, view, hit[0]["id"], "scene")
            return ActionResponse(Action.RESPONSE, None, f"已启动{hit[0]['name']}。")
        if action == "status":
            cards = [c for a in view["areas"] for c in a["cards"]]
            if device:
                hit, cands = hass.find(view, device)
                cards = [hit] if hit else [c for c in cards if f"{c['area']}的{c['name']}" in cands] or \
                    [c for c in cards if hass._norm(device) in hass._norm(c["area"])]
            return ActionResponse(Action.REQLLM, "Home Assistant 的设备：" + hass.spoken_list(cards), None)
        hit, cands = hass.find(view, device)
        if not hit:
            more = f"可选：{'、'.join(cands)}。" if cands else "没找到这个设备。"
            return ActionResponse(Action.REQLLM, f"{more}请问用户要哪一个。", None)
        op = {"temperature": "target"}.get(action, action)
        if op in ("brightness", "target", "position", "mode") and value is None:
            return ActionResponse(Action.REQLLM, "要给出数值（亮度、温度或位置）或模式。请问用户。", None)
        card = await hass.act(s, view, hit["id"], op, value)
        word = {"on": "已打开", "off": "已关掉", "toggle": "已切换", "open": "已打开", "close": "已关上", "stop": "已停下"}.get(op, "已调好")
        tail = f"，现在{card['summary']}" if op not in ("on", "off") and card.get("summary") else ""
        where = f"{hit['area']}的" if hit["area"] != hass.OTHER else ""
        return ActionResponse(Action.RESPONSE, None, f"{word}{where}{hit['name']}{tail}。")
    except common.IntegrationError as e:
        logger.bind(tag=TAG).warning(f"Home Assistant: {e}")
        return ActionResponse(Action.REQLLM, f"Home Assistant 没做成：{e}。请用一句话如实告诉用户。", None)
