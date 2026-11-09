"""[xiaozhi-hub] agent_api：OpenAI 兼容接口的智能体（Hermes Agent 等），设计画布第 18 块。

配置条目（LLM.Agent_<名字>，后台「智能体」页建）：
  type: agent_api
  name: 显示和播报用的名字
  base_url: http://10.10.10.50:8642/v1
  api_key: 不用就不填
  model: 不填用 default（Hermes 不看这个字段）
  background: true   回答慢、设备不听了时转后台，做完汇报；false 一直等着

连接管理、填充语、进度提示、超时、转后台都继承 claude_cli 的 provider，只换会话（core/agent/api_agent_session.py）。
"""

import time

from config.logger import setup_logging
from core.agent.agent_session import Done, TextDelta
from core.agent.api_agent_session import ApiAgentConfig, ApiAgentSession, forget, thread_for, voice_for
from core.agent.voice_commands import match_resume
from core.providers.llm.claude_cli.claude_cli import VOICE_RULES, LLMProvider as AgentProvider

TAG = __name__
logger = setup_logging()


class _NoStore:
    """父类会把会话 ID 记进 agent_sessions.json 做接续；这里的记录在内存里（api_agent_session），不用它。"""

    def get(self, key):
        return None

    def touch(self, *a, **k):
        pass

    def forget(self, *a, **k):
        pass


class LLMProvider(AgentProvider):
    def __init__(self, config: dict):
        self.entry_config = config
        self.display = str(config.get("name") or "智能体").strip()
        self.AGENT_LABEL = self.display
        self.entry = str(config.get("entry") or "")
        self.REPORT_ID = "agent:" + (self.entry or self.display)
        self.BACKGROUND_TURNS = config.get("background", True) is not False
        self.model_name = f"agent:{self.display}"
        self._init_common(config, filler_default=f"交给{self.display}了")
        self.store = _NoStore()
        self._start_loop()
        logger.bind(tag=TAG).info(f"智能体「{self.display}」：{config.get('base_url')}，"
                                  f"{'慢了转后台' if self.BACKGROUND_TURNS else '一直等着'}")

    def _self_check(self) -> None:
        return

    def _voice_error(self, kind):
        return voice_for(kind, self.display)

    async def _ensure_agent(self, st, text: str, out, system_prompt: str) -> bool:
        """接续窗口内接着这台设备上次的记录；说「接着上次」也一样。"""
        if st.agent and st.agent.alive:
            return False
        remainder = match_resume(text, self.resume_keywords)
        history = thread_for(self.entry or self.display, st.key, self.resume_window * 60)
        cfg = ApiAgentConfig(name=self.display, base_url=str(self.entry_config.get("base_url") or ""),
                             api_key=str(self.entry_config.get("api_key") or ""),
                             model=str(self.entry_config.get("model") or ""),
                             system_prompt=(system_prompt or "") + "\n" + VOICE_RULES,
                             entry=self.entry or self.display, device=st.key)
        agent = ApiAgentSession(cfg, history=history, logger=logger.bind(tag=TAG))
        t0 = time.time()
        await agent.start()
        st.agent = agent
        logger.bind(tag=TAG).info(f"智能体「{self.display}」就绪（{time.time() - t0:.1f}s），设备 {st.key}，"
                                  f"{'接着 ' + str(len(history)) + ' 条记录' if history else '新对话'}")
        if remainder is not None and not remainder:
            out.put(TextDelta("😉好的，我们接着上次聊。" if history else "😶没有找到之前的对话，我们重新开始吧。"))
            out.put(Done(session_id=agent.session_id))
            return True
        return False

    def clear_history(self, device: str = None) -> None:
        forget(self.entry or self.display, device)
