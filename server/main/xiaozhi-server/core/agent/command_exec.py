"""[xiaozhi-hub] 常用口令的执行（设计画布第 53 块）：core/agent/commands.py 认出来以后，直接调设备工具、回一句话。

在呼名、清除聊天记录、拍照问之后，意图识别和大模型之前。和拍照问一样：不能在消息循环里原地等设备回复，
所以接手以后放到后台任务里做；设备的工具清单还没到（刚连上）、工具不认识、或者要问清楚的（好几个倒计时取消哪个）
就照常交给大模型。回复照常念出来、记进对话，所以接着说「改成八点」大模型也知道是哪个闹钟。
"""
import asyncio
import json
from typing import TYPE_CHECKING

from core.agent import commands

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
_TASKS = set()
_muted = {}          # 设备 → 静音前的音量（「取消静音」调回去）
KIND_NAME = {"alarm": "闹钟", "timer": "倒计时", "reminder": "提醒"}


def _device(conn) -> str:
    return str(getattr(conn, "device_id", None) or (getattr(conn, "headers", None) or {}).get("device-id") or "")


def _content(text: str) -> str:
    """带说话人的 JSON（{"speaker", "content"}）只看正文。"""
    if text.lstrip().startswith("{"):
        try:
            d = json.loads(text)
            if isinstance(d, dict) and d.get("content"):
                return str(d["content"])
        except ValueError:
            pass
    return text


async def handle(conn: "ConnectionHandler", text: str):
    """返回 None 表示已经接手；否则返回要交给大模型的文字（可能加了口令里写的提示）。"""
    content = _content(text)
    cfg = commands.load()
    device = _device(conn)
    if not commands.active(cfg, device):
        return text
    plan, _ = commands.match(content, cfg)
    if not plan:
        commands.record(device, content, "llm")
        return text
    if plan.kind == "llm":
        commands.record(device, content, "llm", plan.rule)
        if not plan.hint:
            return text
        if content != text:     # JSON 包着：提示加在正文里
            d = json.loads(text)
            d["content"] = f"{content}（{plan.hint}）"
            return json.dumps(d, ensure_ascii=False)
        return f"{text}（{plan.hint}）"
    conn.logger.bind(tag=TAG).info(f"口令：{content} → {plan.rule} {plan.tool or plan.kind} {plan.args}")
    task = asyncio.create_task(_run(conn, content, plan, device))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return None


async def _to_llm(conn, heard, why):
    from core.handle.sendAudioHandle import send_stt_message

    conn.logger.bind(tag=TAG).info(f"口令：{why}，交给大模型：{heard}")
    commands.record(_device(conn), heard, "llm", why)
    await send_stt_message(conn, heard)
    conn.client_abort = False
    conn.executor.submit(conn.chat, heard)


async def _client(conn, tools):
    for _ in range(30):     # 刚连上时工具清单可能还没到：最多等 3 秒
        client = getattr(conn, "mcp_client", None)
        if client and all(client.has_tool(t.replace(".", "_")) for t in tools):
            return client
        await asyncio.sleep(0.1)
    return None


async def _call(conn, client, tool, args):
    from core.providers.tools.device_mcp.mcp_handler import call_mcp_tool

    raw = await call_mcp_tool(conn, client, tool.replace(".", "_"), json.dumps(args, ensure_ascii=False), timeout=10)
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        data = {"response": str(raw)}
    return data if isinstance(data, dict) else {"response": str(data)}


def _said(data: dict) -> str:
    if data.get("success") is False:
        return str(data.get("reason") or "没办成")
    return str(data.get("response") or data.get("note") or "")


async def _run(conn, heard, plan, device):
    from core.agent.call_names import _speak

    try:
        if plan.kind == "answer":
            reply = plan.reply
            if plan.note == "lunar":
                from core.admin.admin_handler import _lunar_today
                lu = await asyncio.to_thread(_lunar_today)
                if not lu.get("text"):
                    return await _to_llm(conn, heard, "农历算不出来")
                reply = f"今天是农历{lu['text']}" + (f"，{lu['term']}" if lu.get("term") else "") + (f"，{lu['festival']}" if lu.get("festival") else "")
        elif plan.kind == "say":
            reply = plan.reply or "好的"
        else:
            tools = [plan.tool] if plan.kind == "tool" else STEP_TOOLS[plan.steps]
            client = await _client(conn, tools)
            if client is None:
                return await _to_llm(conn, heard, "设备没有这个工具")
            if plan.kind == "tool":
                data = await _call(conn, client, plan.tool, plan.args)
                if data.get("success") is False:
                    reply = _said(data)
                else:
                    reply = commands.fill(plan.reply, {**plan.values, "结果": _said(data)}) if plan.reply else (_said(data) or "好的")
            else:
                reply = await STEPS[plan.steps](conn, client, plan, device)
                if reply is None:
                    return await _to_llm(conn, heard, "要问清楚")
    except Exception as e:  # noqa: BLE001
        conn.logger.bind(tag=TAG).warning(f"口令执行出错（{e}），交给大模型")
        return await _to_llm(conn, heard, "执行出错")
    commands.record(device, heard, "fast", plan.rule)
    await _speak(conn, heard, str(reply or "好的"))


# ---- 要先读再做的几种

async def _volume(conn, client, plan, device):
    cur = (await _call(conn, client, "self.audio_speaker.get_volume", {})).get("volume")
    if not isinstance(cur, (int, float)):
        return None
    v = max(5, min(100, int(cur) + int(plan.args.get("change", 0))))
    await _call(conn, client, "self.audio_speaker.set_volume", {"volume": v})
    return f"音量 {v}"


async def _mute(conn, client, plan, device):
    cur = (await _call(conn, client, "self.audio_speaker.get_volume", {})).get("volume")
    if isinstance(cur, (int, float)) and cur > 0:
        _muted[device] = int(cur)
    await _call(conn, client, "self.audio_speaker.set_volume", {"volume": 0})
    return "已静音，说「取消静音」就恢复"


async def _unmute(conn, client, plan, device):
    v = _muted.pop(device, 50)
    await _call(conn, client, "self.audio_speaker.set_volume", {"volume": v})
    return f"声音回来了，音量 {v}"


async def _cancel_one(conn, client, plan, device):
    kind = plan.args.get("kind")
    items = (await _call(conn, client, "self.alarm.list", {"kind": kind})).get("items") or []
    name = KIND_NAME.get(kind, "闹钟")
    if not items:
        return f"现在没有{name}"
    if len(items) > 1:
        return None                       # 好几个：交给大模型问清楚取消哪个
    one = items[0]
    data = await _call(conn, client, "self.alarm.cancel", {"id": one["id"]})
    if data.get("success") is False:
        return _said(data)
    return f"好的，取消了{one.get('when') or ''}的{name}"


async def _list(conn, client, plan, device):
    kind = plan.args.get("kind")
    items = (await _call(conn, client, "self.alarm.list", {"kind": kind} if kind else {})).get("items") or []
    name = KIND_NAME.get(kind, "闹钟和提醒")
    if not items:
        return f"现在没有{name}"
    if kind == "timer" and len(items) == 1:
        return f"倒计时还剩 {items[0].get('in') or ''}"
    parts = [f"{KIND_NAME.get(i.get('type'), i.get('type') or '')} {i.get('when') or ''}{('，' + i['label']) if i.get('label') else ''}" for i in items[:3]]
    more = f"，还有 {len(items) - 3} 个" if len(items) > 3 else ""
    return f"有 {len(items)} 个：" + "；".join(parts) + more


STEPS = {"volume": _volume, "mute": _mute, "unmute": _unmute, "cancel_one": _cancel_one, "list": _list}
STEP_TOOLS = {"volume": ["self.audio_speaker.get_volume", "self.audio_speaker.set_volume"],
              "mute": ["self.audio_speaker.get_volume", "self.audio_speaker.set_volume"],
              "unmute": ["self.audio_speaker.set_volume"],
              "cancel_one": ["self.alarm.list", "self.alarm.cancel"], "list": ["self.alarm.list"]}
