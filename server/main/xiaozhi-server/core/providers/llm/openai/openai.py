import socket
import threading
import time

import httpx
import openai
from config.logger import setup_logging
from core.utils.util import check_model_key
from core.providers.llm.base import LLMProviderBase
from core import billing
from urllib.parse import urlparse

TAG = __name__
logger = setup_logging()

# 需要禁用思考模式的平台域名及其对应参数（默认关闭思考模式）
THINKING_DISABLED_DOMAINS = {
    "aliyuncs.com": {"enable_thinking": False},
    "deepseek.com": {"thinking": {"type": "disabled"}},
    "bigmodel.cn": {"thinking": {"type": "disabled"}},
    "moonshot.cn": {"thinking": {"type": "disabled"}},
    "volces.com": {"thinking": {"type": "disabled"}},
    # [xiaozhi-hub] Claude 5 默认开启思考：首字延迟 3~6 秒且可能耗尽 max_tokens。
    # 官方兼容接口会忽略 reasoning_effort，要用 thinking 参数关闭（经第三方代理时可在配置里写 extra_body）
    "anthropic.com": {"thinking": {"type": "disabled"}},
    # [xiaozhi-hub] 硅基流动上的 Qwen3 等混合推理模型默认会想很久，语音用不上
    "siliconflow.cn": {"enable_thinking": False},
}


# [xiaozhi-hub] 接口不认 stream_options（一些本地服务、旧代理）时记下，之后不再带
_NO_STREAM_USAGE = set()


def _chunk_usage(chunk):
    """[xiaozhi-hub] 这块数据带的用量：标准位置是 chunk.usage（最后一块，有的厂商和 choices 放在同一块），
    Kimi 放在 choices[0].usage。"""
    u = getattr(chunk, "usage", None)
    if u is None and getattr(chunk, "choices", None):
        u = getattr(chunk.choices[0], "usage", None)
    return u


class LLMProvider(LLMProviderBase):
    def __init__(self, config):
        self.model_name = config.get("model_name")
        self.api_key = config.get("api_key")
        if "base_url" in config:
            self.base_url = config.get("base_url")
        else:
            self.base_url = config.get("url")
        self.extra_body = config.get("extra_body")  # [xiaozhi-hub]

        timeout_config = config.get("timeout")
        if isinstance(timeout_config, dict):
            # 细粒度超时配置
            custom_timeout = httpx.Timeout(
                pool=timeout_config.get("pool", 2.0),
                connect=timeout_config.get("connect", 3.0),
                write=timeout_config.get("write", 5.0),
                read=timeout_config.get("read", 60.0)
            )
        elif isinstance(timeout_config, (int, float)) and timeout_config > 0:
            # 兼容旧的单一超时配置（整数或浮点数）
            custom_timeout = httpx.Timeout(timeout_config)
        else:
            # 未配置或配置无效，使用默认值
            custom_timeout = httpx.Timeout(300)

        param_defaults = {
            "max_tokens": int,
            "temperature": lambda x: round(float(x), 1),
            "top_p": lambda x: round(float(x), 1),
            "frequency_penalty": lambda x: round(float(x), 1),
        }

        for param, converter in param_defaults.items():
            value = config.get(param)
            try:
                setattr(
                    self,
                    param,
                    converter(value) if value not in (None, "") else None,
                )
            except (ValueError, TypeError):
                setattr(self, param, None)

        logger.debug(
            f"意图识别参数初始化: {self.temperature}, {self.max_tokens}, {self.top_p}, {self.frequency_penalty}"
        )

        model_key_msg = check_model_key("LLM", self.api_key)
        if model_key_msg:
            logger.bind(tag=TAG).error(model_key_msg)
        self.client = openai.OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=custom_timeout)
        # [xiaozhi-hub] 每个连接正在读的流式响应，打断时断开（abort）
        self._streams = {}
        self._streams_lock = threading.Lock()

    def _track(self, session_id, stream, on=True):
        with self._streams_lock:
            streams = self._streams.setdefault(session_id, set())
            (streams.add if on else streams.discard)(stream)
            if not streams:
                del self._streams[session_id]

    def abort(self, conn) -> None:
        """[xiaozhi-hub] 设备打断：立刻断开这个连接正在读的流式响应，模型不再接着生成。
        读流的线程卡在收数据上，从这边关连接对象叫不醒它（Linux 上 close 不会打断另一个线程的 recv），
        要对底层 socket 做 shutdown，读的那边马上报错退出（chat 里被打断时不播报错误）。
        还在等响应头的请求断不了，等它回来后第一块数据就会因为这一轮已被打断而丢掉。"""
        with self._streams_lock:
            streams = list(self._streams.get(conn.session_id, ()))
        for stream in streams:
            try:
                network = stream.response.extensions.get("network_stream")
                sock = network.get_extra_info("socket") if network else None
                if sock is not None:
                    sock.shutdown(socket.SHUT_RDWR)
                    logger.bind(tag=TAG).info("打断：已断开模型的流式响应")
            except Exception as e:
                logger.bind(tag=TAG).debug(f"断开流式响应失败: {e}")

    def _create(self, request_params):
        """[xiaozhi-hub] 流式请求，带上 stream_options.include_usage 让最后一块带用量（费用统计）。
        接口不认这个参数时去掉重试一次，并记住这个地址。"""
        if self.base_url not in _NO_STREAM_USAGE:
            try:
                return self.client.chat.completions.create(**request_params, stream_options={"include_usage": True})
            except openai.BadRequestError as e:
                if "stream_options" not in str(e) and "include_usage" not in str(e):
                    raise
                _NO_STREAM_USAGE.add(self.base_url)
                logger.bind(tag=TAG).info(f"{self.base_url} 不支持 stream_options，之后不再带（用量改为估算）")
        return self.client.chat.completions.create(**request_params)

    def _bill(self, session_id, dialogue, usage, text, t0=None, first=None):
        # [xiaozhi-hub] 首字、总耗时一起记（费用页的延迟）
        now = time.monotonic()
        times = {"first_ms": (first - t0) * 1000 if t0 and first else None, "total_ms": (now - t0) * 1000 if t0 else None}
        if usage is not None:
            billing.record(self.model_name, billing.usage_of(usage), session_id, **times)
        else:   # 被打断或接口不给用量：按字数估算
            billing.record(self.model_name, billing.estimate(dialogue, text), session_id, estimated=True, **times)

    def _create_billed(self, session_id, request_params):
        """[xiaozhi-hub] 发请求；连不上、报错时记一条失败（不花钱），再照常抛出。返回 (流, 开始时间)。"""
        t0 = time.monotonic()
        try:
            return self._create(request_params), t0
        except Exception as e:
            billing.record_failure(self.model_name, f"{type(e).__name__}: {e}", session_id,
                                   total_ms=(time.monotonic() - t0) * 1000)
            raise

    @staticmethod
    def normalize_dialogue(dialogue):
        """自动修复 dialogue 中缺失 content 的消息"""
        for msg in dialogue:
            if "role" in msg and "content" not in msg:
                msg["content"] = ""
        return dialogue

    def _apply_thinking_disabled(self, request_params: dict):
        """根据域名自动禁用思考模式"""
        parsed_url = urlparse(self.base_url)
        domain = parsed_url.netloc
        for disabled_domain, params in THINKING_DISABLED_DOMAINS.items():
            if disabled_domain in domain:
                request_params.setdefault("extra_body", {}).update(params)
                logger.bind(tag=TAG).info(f"为域名 {domain} 禁用思考模式，参数: {params}")
                break
        # [xiaozhi-hub] 配置里的 extra_body 原样附加到请求体，例如经代理访问 Claude 时设置 reasoning_effort: none
        if isinstance(self.extra_body, dict) and self.extra_body:
            request_params.setdefault("extra_body", {}).update(self.extra_body)

    def response(self, session_id, dialogue, **kwargs):
        dialogue = self.normalize_dialogue(dialogue)

        request_params = {
            "model": self.model_name,
            "messages": dialogue,
            "stream": True,
        }

        # 添加可选参数,只有当参数不为None时才添加
        optional_params = {
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "temperature": kwargs.get("temperature", self.temperature),
            "top_p": kwargs.get("top_p", self.top_p),
            "frequency_penalty": kwargs.get("frequency_penalty", self.frequency_penalty),
        }

        for key, value in optional_params.items():
            if value is not None:
                request_params[key] = value

        # 禁用思考模式
        self._apply_thinking_disabled(request_params)

        responses, t0 = self._create_billed(session_id, request_params)
        first = None
        self._track(session_id, responses)

        is_active = True
        usage, out = None, []
        try:
            for chunk in responses:
                usage = _chunk_usage(chunk) or usage
                if first is None and getattr(chunk, "choices", None):
                    first = time.monotonic()   # [xiaozhi-hub] 首字耗时
                try:
                    delta = chunk.choices[0].delta if getattr(chunk, "choices", None) else None
                    content = getattr(delta, "content", "") if delta else ""
                except IndexError:
                    content = ""
                if content:
                    if "<think>" in content:
                        is_active = False
                        content = content.split("<think>")[0]
                    if "</think>" in content:
                        is_active = True
                        content = content.split("</think>")[-1]
                    if is_active:
                        out.append(content)
                        yield content
        finally:
            self._track(session_id, responses, False)
            responses.close()
            self._bill(session_id, dialogue, usage, "".join(out), t0, first)

    def response_with_functions(self, session_id, dialogue, functions=None, **kwargs):
        dialogue = self.normalize_dialogue(dialogue)

        request_params = {
            "model": self.model_name,
            "messages": dialogue,
            "stream": True,
            "tools": functions,
        }

        optional_params = {
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "temperature": kwargs.get("temperature", self.temperature),
            "top_p": kwargs.get("top_p", self.top_p),
            "frequency_penalty": kwargs.get("frequency_penalty", self.frequency_penalty),
        }

        for key, value in optional_params.items():
            if value is not None:
                request_params[key] = value

        # 禁用思考模式
        self._apply_thinking_disabled(request_params)

        stream, t0 = self._create_billed(session_id, request_params)
        first = None
        self._track(session_id, stream)

        usage, out = None, []
        try:
            for chunk in stream:
                if first is None and getattr(chunk, "choices", None):
                    first = time.monotonic()   # [xiaozhi-hub] 首字耗时
                usage = _chunk_usage(chunk) or usage
                if getattr(chunk, "choices", None):
                    delta = chunk.choices[0].delta
                    content = getattr(delta, "content", "")
                    tool_calls = getattr(delta, "tool_calls", None)
                    if content:
                        out.append(content)
                    yield content, tool_calls
        finally:
            self._track(session_id, stream, False)
            stream.close()
            self._bill(session_id, dialogue, usage, "".join(out), t0, first)
