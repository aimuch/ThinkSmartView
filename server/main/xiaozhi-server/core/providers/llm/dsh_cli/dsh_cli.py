"""[xiaozhi-hub] dsh_cli：让小智设备通过网关上的 DeepSeek Harness（DSH）对话和干活（设计画布第 15 块 B）。

连接管理、空闲关闭、填充语、进度提示、超时、转后台都继承 claude_cli 的 provider，只换掉会话：
DSH 用 headless 模式，每句话起一个进程，用 --session-id 接着同一台设备上一轮的对话（见 core/agent/dsh_session.py）。

配置条目（LLM.DshCLI）：
  type: dsh_cli
  model: deepseek-flash | deepseek-v4-pro
  effort: low | high | max
  permission: read-only | workspace-write | danger-full-access
  workdir: ~/xiaozhi-workspace
  api_key: 留空就用「大模型」页 DeepSeek（key_from，默认 DeepSeekLLM）的 Key
  dsh_path / node_dir: 留空自动找（~/.local/dsh、~/.local/node24）
"""

import os
import time
from pathlib import Path

from config.config_loader import get_project_dir
from config.logger import setup_logging
from core.agent.agent_session import AgentSessionError, Done, ErrorKind, TextDelta
from core.agent.dsh_session import (PERMISSION_PRESETS, DshSession, DshSessionConfig, find_dsh,
                                    find_node_dir, voice_for, write_patch)
from core.agent.session_store import SessionStore, choose_resume
from core.agent.voice_commands import match_resume
from core.providers.llm.claude_cli.claude_cli import VOICE_RULES, LLMProvider as AgentProvider

TAG = __name__
logger = setup_logging()

DEFAULT_MODEL = "deepseek-flash"
PERSONA = ("你是家里智能音箱「小麦」里的智能体 DSH，用 {{model}} 模型，能在工作目录里查看文件、搜索、执行任务。"
           "你说的每个字都会被朗读出来。" + VOICE_RULES)


def _placeholder(value) -> bool:
    return value is None or str(value).strip() == "" or "你" in str(value)


class LLMProvider(AgentProvider):
    AGENT_LABEL = "DSH"
    REPORT_ID = "dsh_cli"

    def __init__(self, config: dict):
        self.entry_config = config
        self.model = str(config.get("model") or DEFAULT_MODEL)
        self.model_name = f"dsh:{self.model}"
        self.dsh_path = find_dsh(config.get("dsh_path") or None)
        self.node_dir = find_node_dir(config.get("node_dir") or None)
        permission = str(config.get("permission") or "read-only")
        self.permission = permission if permission in PERMISSION_PRESETS else "read-only"
        self.workdir = str(Path(os.path.expanduser(config.get("workdir") or "~/xiaozhi-workspace")))
        Path(self.workdir).mkdir(parents=True, exist_ok=True)
        data = Path(get_project_dir()) / "data" / "dsh"
        self.home = str(data / "home")
        self.patch_path = data / "voice.patch.yml"
        write_patch(self.patch_path, self.model, str(config.get("effort", "low") or ""), PERSONA)
        self._init_common(config, filler_default="好的，我想想")
        self.store = SessionStore(Path(get_project_dir()) / "data" / "agent_sessions.json", "dsh_cli")
        self._start_loop()
        self._self_check()

    def _api_key(self) -> str:
        """自己填的 Key；没填用「大模型」页 DeepSeek 的（每轮现读，那边换了 Key 这里跟着用新的）。"""
        own = self.entry_config.get("api_key")
        if not _placeholder(own):
            return str(own)
        try:
            from core.admin import config_store

            merged = config_store.load_merged()
            src = (merged.get("LLM") or {}).get(self.entry_config.get("key_from") or "DeepSeekLLM") or {}
            return "" if _placeholder(src.get("api_key")) else str(src["api_key"])
        except Exception as e:  # noqa: BLE001
            logger.bind(tag=TAG).warning(f"读 DeepSeek 的 Key 失败：{e}")
            return ""

    def _self_check(self) -> None:
        if not self.dsh_path:
            logger.bind(tag=TAG).error("未找到 dsh，请先安装 DeepSeek Harness（npm i @deepseek-ai/dsh），或在配置里填 dsh_path")
            return
        if not self.node_dir:
            logger.bind(tag=TAG).warning("没找到 Node 24（~/.local/node24/bin），用系统的 node；DSH 要 24.9 以上")
        logger.bind(tag=TAG).info(f"DSH: {self.dsh_path}，模型 {self.model}，权限 {self.permission}，工作目录 {self.workdir}")

    def _voice_error(self, kind: ErrorKind) -> str:
        return voice_for(kind)

    def _bill_usage(self, st):
        from core import billing

        def record(usage: dict):
            billing.record(self.model, {"prompt": int(usage.get("inputTokens") or 0) + int(usage.get("cacheReadTokens") or 0),
                                        "completion": int(usage.get("outputTokens") or 0),
                                        "cache_hit": int(usage.get("cacheReadTokens") or 0)},
                           purpose="chat", entry="DshCLI", device=st.key)
        return record

    def _new_session(self, st, resume_id, system_prompt):
        cfg = DshSessionConfig(dsh_path=self.dsh_path, node_dir=self.node_dir, workdir=self.workdir,
                               patch_path=str(self.patch_path), home=self.home, permission=self.permission,
                               api_key=self._api_key())
        agent = DshSession(cfg, resume_id=resume_id, logger=logger.bind(tag=TAG), prefix=system_prompt or "")
        agent.on_usage = self._bill_usage(st)
        return agent

    async def _ensure_agent(self, st, text: str, out, system_prompt: str) -> bool:
        """和 claude_cli 一样：接续窗口内（或说了接续指令）接着上一次的 DSH 会话，否则新开。"""
        if st.agent and st.agent.alive:
            return False
        remainder = match_resume(text, self.resume_keywords)
        keyword = remainder is not None
        resume_id, reason = choose_resume(self.store.get(st.key), keyword, self.resume_window)
        agent = self._new_session(st, resume_id, system_prompt)
        t0 = time.time()
        await agent.start()
        if st.closed:
            await agent.close()
            raise AgentSessionError(ErrorKind.PROCESS_EXITED, "连接已关闭")
        st.agent = agent
        logger.bind(tag=TAG).info(f"DSH 就绪（{time.time() - t0:.1f}s），设备 {st.key}，"
                                  f"{'接续会话 ' + resume_id if resume_id else '新会话'}（{reason}）")
        if keyword and not remainder:
            out.put(TextDelta("😉好的，我们接着上次聊。" if resume_id else "😶没有找到之前的对话，我们重新开始吧。"))
            out.put(Done(session_id=agent.session_id))
            return True
        return False
