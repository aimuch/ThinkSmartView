"""[xiaozhi-hub] 通用的「OpenAI 兼容接口」智能体会话（设计画布第 18 块）：Hermes Agent（hermes gateway 的 API server，
默认 http://127.0.0.1:8642/v1）这类自己带工具、记忆的智能体，对外是一个 /v1/chat/completions。

接口是无状态的：每次把这台设备和它聊过的话整段发过去。记录按（条目, 设备）存在内存里，接续窗口内接着用，
网关重启就从头开始（智能体自己通常还有记忆）。

和 Claude Code、DSH 一样走 claude_cli 的派活机制：填充语、进度提示、设备不听了就转后台、做完进待汇报箱。
智能体可能想很久才出第一个字：开头报一个 tools_running=1 的 Activity，看门狗不会把它当成卡住。
"""

import asyncio
import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import AsyncIterator, Dict, List, Optional, Tuple

from core.agent.agent_session import Activity, AgentEvent, AgentSession, AgentSessionError, Done, Error, ErrorKind, TextDelta

HISTORY_MAX = 40          # 最多带最近这么多条消息
FIRST_TOKEN_TIMEOUT = 900  # 智能体开工后最多等这么久出第一个字（长任务由上层的单轮超时和转后台管）

VOICE = {
    ErrorKind.AUTH: "{name} 的 Key 不对，请到网关后台「智能体」里检查。",
    ErrorKind.SERVER: "{name} 那边暂时有点问题，稍后再试吧。",
    ErrorKind.SEND_FAILED: "连不上 {name}，看看它的服务开着没有。",
    ErrorKind.RATE_LIMIT: "{name} 现在请求太多了，稍后再试吧。",
    ErrorKind.UNKNOWN: "{name} 出了点小问题，再说一次试试。",
}

_lock = threading.Lock()
_threads: Dict[Tuple[str, str], dict] = {}   # (条目, 设备) -> {"messages": [...], "last": 时间}


def thread_for(entry: str, device: str, window_seconds: float) -> List[dict]:
    """这台设备和这个智能体在接续窗口内的记录（复制一份）；超时就清掉重来。"""
    with _lock:
        t = _threads.get((entry, device))
        if not t or (window_seconds and time.time() - t["last"] > window_seconds):
            _threads.pop((entry, device), None)
            return []
        return [dict(m) for m in t["messages"]]


def save_thread(entry: str, device: str, messages: List[dict]) -> None:
    with _lock:
        _threads[(entry, device)] = {"messages": messages[-HISTORY_MAX:], "last": time.time()}


def forget(entry: str, device: str = None) -> None:
    with _lock:
        for k in [k for k in _threads if k[0] == entry and (device is None or k[1] == device)]:
            del _threads[k]


@dataclass
class ApiAgentConfig:
    name: str
    base_url: str
    api_key: str = ""
    model: str = ""
    system_prompt: str = ""
    entry: str = ""
    device: str = ""
    extra_headers: dict = field(default_factory=dict)


def voice_for(kind: ErrorKind, name: str) -> str:
    return (VOICE.get(kind) or VOICE[ErrorKind.UNKNOWN]).format(name=name)


class ApiAgentSession(AgentSession):
    def __init__(self, cfg: ApiAgentConfig, history: List[dict] = None, logger=None):
        self.cfg = cfg
        self.messages: List[dict] = list(history or [])
        self.logger = logger
        self.alive = False
        self._sid = f"{cfg.entry}:{cfg.device}:{uuid.uuid4().hex[:8]}"
        self._stream = None
        self._cancel = asyncio.Event()

    @property
    def session_id(self) -> Optional[str]:
        return self._sid

    async def start(self) -> None:
        if not self.cfg.base_url:
            raise AgentSessionError(ErrorKind.NOT_INSTALLED, f"{self.cfg.name} 没有填接口地址")
        self.alive = True

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json", **(self.cfg.extra_headers or {})}
        if self.cfg.api_key:
            h["Authorization"] = f"Bearer {self.cfg.api_key}"
        return h

    def _fail(self, kind: ErrorKind, detail: str) -> Error:
        if self.logger:
            self.logger.warning(f"智能体「{self.cfg.name}」出错（{kind.value}）：{detail[:200]}")
        return Error(kind, detail, voice_for(kind, self.cfg.name))

    async def send(self, text: str) -> AsyncIterator[AgentEvent]:
        """POST {base_url}/chat/completions（stream），按 SSE 一行一行读 delta.content。"""
        import aiohttp

        self._cancel.clear()
        sent = self.messages + [{"role": "user", "content": text}]
        request = ([{"role": "system", "content": self.cfg.system_prompt}] if self.cfg.system_prompt else []) + sent
        body = {"model": self.cfg.model or "default", "messages": request, "stream": True}
        url = self.cfg.base_url.rstrip("/") + "/chat/completions"
        yield Activity("init", tools_running=1)
        reply = []
        timeout = aiohttp.ClientTimeout(total=None, connect=15, sock_read=FIRST_TOKEN_TIMEOUT)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as http:
                async with http.post(url, json=body, headers=self._headers()) as resp:
                    self._stream = resp
                    if resp.status in (401, 403):
                        yield self._fail(ErrorKind.AUTH, f"HTTP {resp.status} {(await resp.text())[:200]}")
                        return
                    if resp.status == 429:
                        yield self._fail(ErrorKind.RATE_LIMIT, (await resp.text())[:200])
                        return
                    if resp.status >= 400:
                        yield self._fail(ErrorKind.SERVER, f"HTTP {resp.status} {(await resp.text())[:200]}")
                        return
                    yield Activity("working", tools_running=1)
                    if "text/event-stream" not in resp.headers.get("Content-Type", ""):
                        # 有的服务不管 stream，直接回整段
                        data = await resp.json(content_type=None)
                        piece = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
                        if piece:
                            reply.append(piece)
                            yield TextDelta(piece)
                    else:
                        async for raw in resp.content:
                            if self._cancel.is_set():
                                break
                            line = raw.decode("utf-8", "replace").strip()
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if data == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data)
                            except ValueError:
                                continue
                            if chunk.get("error"):
                                yield self._fail(ErrorKind.SERVER, str(chunk["error"])[:200])
                                return
                            choice = (chunk.get("choices") or [{}])[0]
                            piece = (choice.get("delta") or {}).get("content") or ""
                            if piece:
                                reply.append(piece)
                                yield TextDelta(piece)
                            else:
                                yield Activity("working", tools_running=1)   # 智能体在用工具：有动静，不算卡住
        except (aiohttp.ClientConnectionError, OSError) as e:
            if not self._cancel.is_set():
                yield self._fail(ErrorKind.SEND_FAILED, repr(e))
                return
        except asyncio.TimeoutError:
            yield self._fail(ErrorKind.SERVER, f"{FIRST_TOKEN_TIMEOUT} 秒没有任何回复")
            return
        finally:
            self._stream = None
        if self._cancel.is_set():
            yield Done(session_id=self._sid, interrupted=True)
            return
        answer = "".join(reply).strip()
        if answer:
            self.messages = (sent + [{"role": "assistant", "content": answer}])[-HISTORY_MAX:]
            if self.cfg.entry and self.cfg.device:
                save_thread(self.cfg.entry, self.cfg.device, self.messages)
        yield Done(session_id=self._sid)

    async def interrupt(self) -> None:
        self._cancel.set()
        resp = self._stream
        if resp is not None:
            resp.close()   # 读循环随之结束

    async def close(self) -> None:
        await self.interrupt()
        self.alive = False
