"""[xiaozhi-hub] DeepSeek Harness（DSH，github.com/deepseek-ai/deepseek-harness）的会话。

DSH 没有常驻的 SDK 进程，用它的 headless 模式：每句话起一个
  dsh --profile headless --json --permission-mode <档位> --patch <覆盖文件> [--session-id <上一轮的>] -
从标准输入读这句话，标准输出是一行一个 JSON 事件（session、status、thinking、text、tool_call、tool_result、final）。
同一台设备接着聊就带上上一轮的 sessionId，对话记录存在 DSH_HOME 下（DSH 自己管）。

- 模型、思考档位、人设用 --patch 覆盖（agent-default-model、system-prompt 两行），每次启动前写好；
- Key 走环境变量 DEEPSEEK_API_KEY（headless 不读 DSH 图形界面保存的登录）；
- 打断：结束这个进程（先 SIGINT，2 秒没退再 SIGKILL）；DSH 把做到一半的这一轮记成 aborted，下一句照样能接着；
- 文字在每一步提交时整段给出（不是逐字），所以第一句话比 Claude Code 晚一点；启动进程本身约 2 秒。
"""

import asyncio
import json
import os
import shutil
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Optional

from core.agent.agent_session import Activity, AgentEvent, AgentSession, AgentSessionError, Done, Error, ErrorKind, TextDelta

# DSH 的三档权限预设（dsh --permission-mode 认的名字）
PERMISSION_PRESETS = ("read-only", "workspace-write", "danger-full-access")
COMMON_DSH_PATHS = ["~/.local/dsh/node_modules/.bin/dsh", "~/.local/bin/dsh", "/usr/local/bin/dsh", "/opt/homebrew/bin/dsh"]
COMMON_NODE_DIRS = ["~/.local/node24/bin", "~/.local/node/bin"]   # DSH 要 Node 24.9 以上，系统的 Node 常常太旧

VOICE_ERRORS = {
    ErrorKind.NOT_INSTALLED: "网关上没有找到 DSH，请先在网关上安装 DeepSeek Harness。",
    ErrorKind.AUTH: "DeepSeek 的 Key 不对或者没填，请到后台大模型页检查。",
    ErrorKind.RATE_LIMIT: "DeepSeek 现在请求太多了，稍后再试吧。",
    ErrorKind.BILLING: "DeepSeek 的账户余额好像不够了。",
    ErrorKind.SERVER: "DeepSeek 那边暂时有点问题，稍后再试吧。",
    ErrorKind.SESSION_MISSING: "没有找到之前的对话，我们重新开始吧。",
    ErrorKind.PROCESS_EXITED: "DSH 意外退出了，再说一次试试。",
    ErrorKind.UNKNOWN: "DSH 出了点小问题，再说一次试试。",
}


def voice_for(kind: ErrorKind) -> str:
    return VOICE_ERRORS.get(kind) or Error(kind).voice_message


def find_dsh(configured: Optional[str] = None) -> Optional[str]:
    for p in ([configured] if configured else []) + COMMON_DSH_PATHS:
        path = os.path.expanduser(p)
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return shutil.which("dsh")


def find_node_dir(configured: Optional[str] = None) -> Optional[str]:
    for p in ([configured] if configured else []) + COMMON_NODE_DIRS:
        d = os.path.expanduser(p)
        if os.path.isfile(os.path.join(d, "node")):
            return d
    return None


def dsh_version(dsh: str, node_dir: Optional[str] = None, timeout: float = 20) -> str:
    import subprocess

    env = dict(os.environ)
    if node_dir:
        env["PATH"] = node_dir + os.pathsep + env.get("PATH", "")
    out = subprocess.run([dsh, "--version"], capture_output=True, text=True, timeout=timeout, env=env)
    return (out.stdout or out.stderr).strip().splitlines()[0] if (out.stdout or out.stderr).strip() else ""


def _yaml_str(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)   # JSON 字符串也是合法的 YAML 标量


def write_patch(path: Path, model: str, effort: str, persona: str) -> None:
    rows = ["- id: agent-default-model", "  config:", "    provider: deepseek-official", f"    model: {_yaml_str(model)}"]
    if effort:
        rows.append(f"    reasoningEffort: {_yaml_str(effort)}")
    rows += ["- id: system-prompt", "  config:", f"    personaPrefix: {_yaml_str(persona)}"]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    tmp.write_text("\n".join(rows) + "\n", encoding="utf-8")
    os.replace(tmp, path)


@dataclass
class DshSessionConfig:
    dsh_path: Optional[str]
    node_dir: Optional[str]
    workdir: str
    patch_path: str
    home: str
    permission: str = "read-only"
    api_key: str = ""
    extra_env: dict = field(default_factory=dict)


def _classify(code: str, message: str) -> ErrorKind:
    text = f"{code} {message}".lower()
    if "credential" in text or "401" in text or "api key" in text or "unauthor" in text:
        return ErrorKind.AUTH
    if "429" in text or "rate" in text:
        return ErrorKind.RATE_LIMIT
    if "402" in text or "balance" in text or "insufficient" in text:
        return ErrorKind.BILLING
    if "5" in code[:1] or "server" in text or "overload" in text:
        return ErrorKind.SERVER
    if "session" in text and ("unknown" in text or "not found" in text or "no stored" in text):
        return ErrorKind.SESSION_MISSING
    return ErrorKind.UNKNOWN


class DshSession(AgentSession):
    def __init__(self, cfg: DshSessionConfig, resume_id: Optional[str] = None, logger=None, prefix: str = ""):
        self.cfg = cfg
        self._session_id = resume_id
        self.logger = logger
        self.prefix = prefix          # 新会话的第一句话前面加上（网关的系统提示词：记忆、住宅地址等）
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.alive = False
        self.on_usage = None          # 每一步的 token 用量，给费用统计

    @property
    def session_id(self) -> Optional[str]:
        return self._session_id

    async def start(self) -> None:
        if not self.cfg.dsh_path:
            raise AgentSessionError(ErrorKind.NOT_INSTALLED, "没有找到 dsh")
        if not self.cfg.api_key:
            raise AgentSessionError(ErrorKind.AUTH, "没有 DeepSeek 的 Key")
        self.alive = True

    def _env(self) -> dict:
        env = dict(os.environ)
        if self.cfg.node_dir:
            env["PATH"] = self.cfg.node_dir + os.pathsep + env.get("PATH", "")
        env.update({"DSH_HOME": self.cfg.home, "DSH_TELEMETRY_DISABLED": "1", "DEEPSEEK_API_KEY": self.cfg.api_key})
        env.update(self.cfg.extra_env)
        return env

    async def send(self, text: str) -> AsyncIterator[AgentEvent]:
        """接续的会话在 DSH 那边已经没有了（DSH_HOME 被清过）：不报错，自动新开一个再发一次。"""
        resuming = self._session_id is not None
        async for ev in self._send_once(text):
            if resuming and isinstance(ev, Error) and ev.kind == ErrorKind.SESSION_MISSING:
                if self.logger:
                    self.logger.warning(f"DSH 会话 {self._session_id} 接不上了，新开一个")
                self._session_id = None
                async for ev2 in self._send_once(text):
                    yield ev2
                return
            yield ev

    async def _send_once(self, text: str) -> AsyncIterator[AgentEvent]:
        args = [self.cfg.dsh_path, "--profile", "headless", "--json", "--permission-mode", self.cfg.permission,
                "--patch", self.cfg.patch_path]
        first = self._session_id is None
        if not first:
            args += ["--session-id", self._session_id]
        args.append("-")
        task = (self.prefix.strip() + "\n\n用户说：" + text) if first and self.prefix.strip() else text
        os.makedirs(self.cfg.workdir, exist_ok=True)
        try:
            self.proc = await asyncio.create_subprocess_exec(
                *args, cwd=self.cfg.workdir, env=self._env(), stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=1 << 20,
                start_new_session=True)
        except (FileNotFoundError, PermissionError) as e:
            yield Error(ErrorKind.NOT_INSTALLED, str(e), voice_for(ErrorKind.NOT_INSTALLED))
            return
        proc = self.proc
        proc.stdin.write(task.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()
        running = 0
        final = None
        error_event = None
        spoke = False
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            kind = ev.get("type")
            if kind == "session":
                self._session_id = ev.get("sessionId") or self._session_id
                yield Activity("init", tools_running=1)   # 有了会话 ID 再报：上层这时把它记下来，下一句接着用
            elif kind == "text":
                t = ev.get("text") or ""
                if t.strip():
                    spoke = True
                    yield TextDelta(t if t.endswith(("\n", "。", "！", "？", ".", "!", "?")) else t + "\n")
            elif kind == "thinking":
                yield Activity("thinking", tools_running=running)
            elif kind == "tool_call":
                running += 1
                yield Activity(str(ev.get("tool") or "tool"), tools_running=running)
            elif kind == "tool_result":
                running = max(0, running - 1)
                yield Activity("tool_result", tools_running=running)
            elif kind == "status":
                if ev.get("phase") == "step_end" and ev.get("usage") and self.on_usage:
                    try:
                        self.on_usage(ev["usage"])
                    except Exception:  # noqa: BLE001 —— 记费用出错不影响对话
                        pass
                yield Activity(str(ev.get("phase") or "status"), tools_running=max(running, 0) or -1)
            elif kind == "final":
                final = ev
            elif kind == "error":
                error_event = ev
        err = (await proc.stderr.read()).decode("utf-8", "replace")
        code = await proc.wait()
        self.proc = None
        reason = (final or {}).get("reason") or {}
        if final and final.get("sessionId"):
            self._session_id = final["sessionId"]
        if reason.get("kind") == "completed":
            if not spoke and (final.get("text") or "").strip():
                yield TextDelta(final["text"])
            yield Done(session_id=self._session_id)
            return
        if reason.get("kind") == "aborted" or code in (-signal.SIGINT, -signal.SIGKILL, 130):
            yield Done(session_id=self._session_id, interrupted=True)
            return
        e = (reason.get("error") or {}) if reason else {}
        if error_event and not e:
            e = {"message": error_event.get("message") or "", "code": error_event.get("code") or ""}
        tail = err.strip().splitlines()
        message = str(e.get("message") or (tail[-1] if tail else "") or f"dsh 退出码 {code}")
        kind = _classify(str(e.get("code") or ""), message)
        if self.logger:
            self.logger.warning(f"DSH 这一轮没有完成（{kind.value}）：{message[:300]}")
        yield Error(kind, message, voice_for(kind))

    async def interrupt(self) -> None:
        proc = self.proc
        if proc is None or proc.returncode is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGINT)
        except (ProcessLookupError, PermissionError):
            return
        try:
            await asyncio.wait_for(proc.wait(), 2)
        except asyncio.TimeoutError:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

    async def close(self) -> None:
        await self.interrupt()
        self.alive = False
