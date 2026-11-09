"""[xiaozhi-hub] 拍照问的快捷通道：「帮我看下这是什么」这类话直接让设备拍照，不交给模型判断要不要调用工具。

2026-11-10 实测：同一段对话里问过几次以后，DeepSeek 会只说一句「好，对准它，我数三下就拍」，却没调用
self.photo.ask（工具执行后的那句话被它学会了），屏幕上什么也没有。拍照这件事意图很明确，所以在呼名、
清除聊天记录之后、意图识别和大模型之前，按句式认出来就直接调设备工具 self.photo.ask，把它返回的那句话念出来；
照片拍好以后，设备照旧把「看图模型看到的是……」交给模型回答。设备没有这个工具时照常交给模型。

只认「这/这个/这是/它」指着眼前东西的说法，避免「看看天气」「帮我看下新闻」被拦下来。
"""

import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
TOOL = "self_photo_ask"
MAX_LEN = 30

_STRIP = re.compile(r"[\s，。！？、,.!?；;：:~～\"'“”‘’…]+")
_THIS = r"(?:这个|这是|这些|这道|这张|这种|这块|这本|这|它)"
_LOOK = rf"(?:帮我|给我|你|麻烦|请)?(?:看|瞧|认|识别|辨认)(?:一下|一看|下|看|看看)?{_THIS}"
_ASK = rf"{_THIS}(?:是|叫)?(?:什么|啥|哪种|哪个)(?:东西|药|字|题|菜|花|草|虫|鸟|鱼|牌子|品种|植物|动物|水果|零件)?"
_READ = rf"{_THIS}?(?:个)?字?(?:怎么读|怎么念|念什么|读什么)|{_THIS}(?:能吃吗|能不能吃|有毒吗|过期了吗|怎么用|怎么吃)"
_SHOOT = r"(?:再)?拍(?:一)?(?:张|个|下)?(?:照片?|一下)?(?:看看|看下|看一下|问问|认认)"
_PHOTO = re.compile(rf"{_LOOK}|{_ASK}|{_READ}|{_SHOOT}")
# 明显不是在问眼前东西的
_NOT = re.compile(r"天气|新闻|音乐|歌|电台|视频|电影|日历|时间|几点|闹钟|提醒|聊天记录|手机")


def is_photo_ask(text: str) -> bool:
    t = _STRIP.sub("", text or "")
    return 0 < len(t) <= MAX_LEN and bool(_PHOTO.search(t)) and not _NOT.search(t)


def question_of(text: str) -> str:
    t = _STRIP.sub("", text or "")
    t = re.sub(r"^(?:小麦|嗯|那个|麻烦|请)?(?:帮我|给我|你)?(?:再)?(?:拍(?:一)?(?:张|个|下)?(?:照片?)?)?(?:看|瞧)(?:一下|一看|下|看)?", "", t)
    return t or "这是什么"


async def handle(conn: "ConnectionHandler", text: str):
    """返回 None 表示已经处理（让设备拍照了）；否则返回原话，照常往下走。"""
    if not is_photo_ask(text):
        return text
    client = getattr(conn, "mcp_client", None)
    if not client or not client.has_tool(TOOL):
        return text
    from core.agent.call_names import _speak
    from core.providers.tools.device_mcp.mcp_handler import call_mcp_tool

    q = question_of(text)
    try:
        raw = await call_mcp_tool(conn, client, TOOL, json.dumps({"question": q}, ensure_ascii=False), timeout=15)
    except Exception as e:  # noqa: BLE001  拍不成就交给模型，它会自己处理
        conn.logger.bind(tag=TAG).warning(f"拍照快捷通道：设备工具出错，交给模型：{e}")
        return text
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        data = {}
    reply = (data or {}).get("response") or (data or {}).get("reason") or "好，拍张照片给我看看。"
    conn.logger.bind(tag=TAG).info(f"拍照快捷通道：{text} → 让设备拍照（问：{q}）")
    if await _speak(conn, text, reply):
        return None
    return text
