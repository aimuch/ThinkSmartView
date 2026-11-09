"""管理后台的 HTTP 接口。

安全约定：
- 首次打开后台时设置密码，从哪台设备访问都可以（部署到云端服务器时也能直接设置）。
- 登录后用 HttpOnly + SameSite=Strict 的 cookie 保持会话；写操作只接受 JSON 请求。
- API Key 只写不读：接口永远不返回完整的 Key。

页面使用的接口（/admin/api/llm、asr、tts、intent 等）只暴露后台要管理的内容，并做取值校验。

本机设备接口（/kiosk/*）给和网关装在同一台机器上的设备客户端用（例如带屏幕的语音终端），不用登录，
只接受本机直接发来的请求，见 _from_this_machine。
"""

import asyncio
import copy
import functools
import io
import json
import os
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from aiohttp import web

from config.logger import setup_logging
from core.admin import auth, brand, catalog, config_store, connections, pages, pipeline, plugin_settings, presence, updater
from core.admin.grokbot import GrokBotAdmin
from core.admin.status import _claude_version, collect_status
from core import billing
from core.agent import chat_history, llm_router, photo_ask, reports
from core.utils import local_voiceprint  # noqa: F401  导入时会初始化日志（asyncio.run），不能放到请求里再导入
from core.agent.agent_session import ErrorKind
from core.agent.grokbot_client import GrokBotError
from core.utils.modules_initialize import initialize_modules
from core.utils.textUtils import EMOJI_MAP

TAG = __name__

# 后台的同步操作（读写配置、测试连接、查 Claude 版本）用自己的线程：默认线程池和语音识别等共用，
# 被卡住的操作（如连不上的测试连接）占满它时，保存要排队十几秒，识别也跟着变慢
_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="admin")

# 测试连接最多等这么久：连不上（地址被墙时连接不报错、一直没回应）就尽快告诉用户，也不占着线程。
# connect 只管建立连接（TCP + TLS，走代理、连国外也就一两秒）；模型想多久算在 read 里，
# 它是两段数据之间最多等多久。测试用流式请求、收到第一个字就停，推理型模型（如 deepseek-reasoner）
# 先想十几秒再出字也等得到
TEST_TIMEOUT = {"connect": 10.0, "read": 60.0, "write": 5.0, "pool": 2.0}
logger = setup_logging()
COOKIE_NAME = "xz_admin"
STATIC_DIR = Path(__file__).parent / "static"
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}
EMOTION_EMOJI = {name: emoji for emoji, name in EMOJI_MAP.items()}
KIOSK_ASR_MAX_BYTES = 16000 * 2 * 10  # /kiosk/asr 最多 10 秒音频
KIOSK_TTS_MAX_CHARS = 200  # /kiosk/tts 一次最多合成这么多字（提醒的内容，一两句话）
KIOSK_TTS_RATE = 24000     # /kiosk/tts 返回的采样率：Edge TTS 原本就是 24 kHz


def _wav(pcm: bytes, rate: int) -> bytes:
    """16 位单声道 PCM 加上 WAV 文件头。"""
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return out.getvalue()


class AdminHandler:
    def __init__(self, config: dict, ws_server=None, config_path: Path = None, grokbot_credentials: Path = None,
                 gateway_updater: "updater.Updater" = None):
        self.config = config
        self.ws_server = ws_server
        self.config_path = config_path
        billing.configure(config)   # [xiaozhi-hub] 费用统计读单价、额度用的配置
        # 后台点了更新网关、网关重启后，接着用重启前的登录（文件在 data/ 下，和配置放在一起）
        data_dir = Path(config_path).parent if config_path else updater.SERVER_DIR / "data"
        self.data_dir = data_dir
        self.started_at = time.time()
        self.sessions = auth.SessionStore(kept_path=data_dir / ".admin-sessions-kept.json")
        self.sessions_handover = data_dir / ".admin-sessions.json"
        if self.sessions.restore(self.sessions_handover):
            logger.bind(tag=TAG).info("更新网关后沿用了更新前的后台登录")
        self.updater = gateway_updater or updater.Updater()
        self._emotion_runs = {}  # session_id -> {"task", "index", "total", "emotion"}
        self._voice_samples = {}  # 音色 -> mp3 字节（试听缓存）
        self._today_cache = {}    # 设备首页的天气（/kiosk/today）：地点 -> (时间, 数据)
        self.logger = logger
        self.grokbot = GrokBotAdmin(grokbot_credentials, logger=logger.bind(tag=TAG))

    def register(self, app: web.Application) -> None:
        app.add_routes(
            [
                web.get("/admin", self.handle_index),
                web.get("/admin/", self.handle_index),
                web.get("/admin/api/state", self.handle_state),
                web.post("/admin/api/setup", self.handle_setup),
                web.post("/admin/api/login", self.handle_login),
                web.post("/admin/api/logout", self.handle_logout),
                web.post("/admin/api/password", self.handle_password),
                web.get("/admin/favicon", self.handle_favicon),
                web.get("/admin/api/brand", self.handle_brand),
                web.post("/admin/api/brand", self.handle_brand_save),
                web.get("/admin/api/status", self.handle_status),
                web.get("/admin/api/update", self.handle_update),
                web.post("/admin/api/update/check", self.handle_update_check),
                web.post("/admin/api/update/start", self.handle_update_start),
                web.get("/admin/api/devices", self.handle_devices),
                web.get("/admin/api/memory", self.handle_memory),
                web.get("/admin/api/billing", self.handle_billing),
                web.post("/admin/api/asr/provider", self.handle_asr_provider_save),
                web.post("/admin/api/asr/xasr", self.handle_asr_xasr),
                web.post("/admin/api/asr/download", self.handle_asr_download),
                web.post("/admin/api/billing", self.handle_billing_save),
                web.post("/admin/api/billing/clear", self.handle_billing_clear),
                web.get("/kiosk/billing", self.handle_kiosk_billing),
                web.post("/admin/api/memory/save", self.handle_memory_save),
                web.post("/admin/api/devices/{session_id}/emotion", self.handle_emotion_one),
                web.post("/admin/api/devices/{session_id}/emotion-test", self.handle_emotion_test),
                web.post("/admin/api/devices/{session_id}/emotion-test/stop", self.handle_emotion_stop),
                web.get("/admin/api/llm", self.handle_llm),
                web.post("/admin/api/llm/claude", self.handle_llm_claude),
                web.post("/admin/api/llm/dsh", self.handle_llm_dsh),
                web.post("/admin/api/llm/vendors/{vendor}", self.handle_llm_vendor),
                web.post("/admin/api/llm/vendors/{vendor}/test", self.handle_llm_vendor_test),
                web.post("/admin/api/llm/use", self.handle_llm_use),
                web.get("/admin/api/pipeline", self.handle_pipeline),
                web.get("/admin/api/asr", self.handle_asr),
                web.post("/admin/api/vad", self.handle_vad_save),
                web.post("/admin/api/asr/use", self.handle_asr_use),
                web.post("/admin/api/asr/doubao", self.handle_doubao_save),
                web.post("/admin/api/asr/doubao/test", self.handle_doubao_test),
                web.post("/admin/api/asr/hotwords", self.handle_hotwords_save),
                web.post("/admin/api/asr/corrections", self.handle_corrections_save),
                web.get("/admin/api/tts", self.handle_tts),
                web.post("/admin/api/tts", self.handle_tts_save),
                web.get("/admin/api/tts/preview", self.handle_tts_preview),
                web.get("/admin/api/intent", self.handle_intent),
                web.post("/admin/api/intent", self.handle_intent_save),
                web.get("/admin/api/plugin-settings/{id}", self.handle_plugin_settings),
                web.post("/admin/api/plugin-settings/{id}", self.handle_plugin_settings_save),
                web.post("/admin/api/plugin-settings/{id}/test", self.handle_plugin_settings_test),
                web.get("/admin/api/call-names", self.handle_call_names),
                web.post("/admin/api/call-names", self.handle_call_names_save),
                web.post("/admin/api/call-names/learn", self.handle_call_names_learn),
                web.post("/admin/api/call-names/learn/stop", self.handle_call_names_learn_stop),
                web.get("/admin/api/grokbot", self.handle_grokbot),
                web.post("/admin/api/grokbot/login", self.handle_grokbot_login),
                web.post("/admin/api/grokbot/login/cancel", self.handle_grokbot_login_cancel),
                web.post("/admin/api/grokbot/logout", self.handle_grokbot_logout),
                web.post("/admin/api/grokbot/bots/refresh", self.handle_grokbot_refresh),
                web.post("/admin/api/grokbot/bots", self.handle_grokbot_bots),
                web.get("/kiosk/llm", self.handle_kiosk_llm),
                web.post("/kiosk/llm/use", self.handle_kiosk_llm_use),
                web.post("/kiosk/reports", self.handle_kiosk_reports),
                web.post("/kiosk/asr", self.handle_kiosk_asr),
                web.post("/kiosk/tts", self.handle_kiosk_tts),
                web.get("/kiosk/chat/history", self.handle_kiosk_chat_history),
                web.post("/kiosk/chat/clear", self.handle_kiosk_chat_clear),
                web.get("/kiosk/geocode", self.handle_kiosk_geocode),
                web.post("/kiosk/home", self.handle_kiosk_home),
                web.get("/kiosk/today", self.handle_kiosk_today),
                web.post("/kiosk/prefs", self.handle_kiosk_prefs),
                web.get("/kiosk/voiceprint", self.handle_kiosk_voiceprint),
                web.post("/kiosk/voiceprint/enroll", self.handle_kiosk_voiceprint_enroll),
                web.post("/kiosk/voiceprint/remove", self.handle_kiosk_voiceprint_remove),
                web.get("/kiosk/lists", self.handle_kiosk_lists),
                web.post("/kiosk/lists", self.handle_kiosk_lists_change),
                web.get("/lists/{secret}", self.handle_lists_page),
                web.get("/lists/{secret}/data", self.handle_lists_data),
                web.post("/lists/{secret}/data", self.handle_lists_data_change),
                web.get("/admin/api/memory/history", self.handle_memory_history),
                web.post("/kiosk/photo/start", self.handle_kiosk_photo_start),
                web.get("/kiosk/photo/{token}", self.handle_kiosk_photo),
                web.get("/kiosk/photo/{token}/image", self.handle_kiosk_photo_image),
                web.post("/kiosk/photo/{token}/cancel", self.handle_kiosk_photo_cancel),
                web.post("/kiosk/photo/{token}/avatar", self.handle_kiosk_avatar_generate),
                web.get("/kiosk/photo/{token}/avatar", self.handle_kiosk_avatar_image),
                web.get("/kiosk/photo/{token}/album/{index}", self.handle_kiosk_album_image),
                # 手机上的拍照页：局域网里谁都能打开，令牌（一次性、5 分钟）就是凭证
                web.get("/photo/{token}", self.handle_photo_page),
                web.get("/photo/{token}/status", self.handle_photo_status),
                web.post("/photo/{token}", self.handle_photo_upload),
                web.get("/admin/api/vision", self.handle_vision),
                web.post("/admin/api/vision", self.handle_vision_save),
                web.post("/admin/api/vision/test", self.handle_vision_test),
                web.post("/admin/api/models/list", self.handle_models_list),
            ]
        )

    # ---------- 工具方法 ----------

    @staticmethod
    def _json(data, status=200):
        return web.json_response(data, status=status, headers=SECURITY_HEADERS)

    def _error(self, message, status):
        return self._json({"ok": False, "error": message}, status)

    @staticmethod
    async def _blocking(func, *args, **kwargs):
        """在后台自己的线程里执行同步操作（配置文件读写、密码哈希），不阻塞负责音频下发的主事件循环。"""
        return await asyncio.get_running_loop().run_in_executor(_POOL, functools.partial(func, *args, **kwargs))

    def _logged_in(self, request: web.Request) -> bool:
        return self.sessions.valid(request.cookies.get(COOKIE_NAME))

    @staticmethod
    async def _read_json(request: web.Request):
        if request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType(text="需要 application/json")
        try:
            body = await request.json()
        except ValueError:
            raise web.HTTPBadRequest(text="JSON 格式错误")
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="JSON 格式错误")
        return body

    def _login_response(self, request: web.Request, data: dict, remember: bool = False):
        """remember（勾了「保持登录」）：30 天，网关重启后也有效；不勾：关掉浏览器就失效（服务端最多 12 小时）。"""
        response = self._json(data)
        response.set_cookie(
            COOKIE_NAME,
            self.sessions.create(remember),
            httponly=True,
            samesite="Strict",
            path="/admin",
            max_age=auth.REMEMBER_SECONDS if remember else None,
        )
        return response

    def _require_login(self, request: web.Request):
        if not self._logged_in(request):
            raise web.HTTPUnauthorized(
                text='{"ok": false, "error": "未登录"}', content_type="application/json"
            )

    # ---------- 页面与登录 ----------

    async def handle_index(self, request: web.Request):
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        return web.Response(text=html, content_type="text/html", headers=SECURITY_HEADERS)

    def _connected_ids(self):
        return [d["device_id"] for d in connections.snapshot() if d.get("device_id")]

    async def handle_state(self, request: web.Request):
        """登录前也能看的：要不要先设密码、名称图标、几台设备在线（登录页底部那行）、地址。"""
        token = request.cookies.get(COOKIE_NAME)
        expiry = self.sessions.expiry(token)
        return self._json(
            {
                "needs_setup": not config_store.get_password_hash(self.config_path),
                "logged_in": expiry is not None,
                "session_expires": int(expiry) if expiry else None,
                "brand": await self._blocking(brand.load, self.data_dir),
                "online": presence.online_count(self._connected_ids()),
                "host": request.host,
            }
        )

    async def handle_favicon(self, request: web.Request):
        body, ctype = await self._blocking(brand.favicon, self.data_dir)
        return web.Response(body=body, content_type=ctype, headers={"Cache-Control": "no-cache"})

    async def handle_brand(self, request: web.Request):
        return self._json({"ok": True, **await self._blocking(brand.load, self.data_dir)})

    async def handle_brand_save(self, request: web.Request):
        self._require_login(request)
        body = await self._read_json(request)
        try:
            saved = await self._blocking(brand.save, self.data_dir, body)
        except ValueError as e:
            return self._error(str(e), 400)
        return self._json({"ok": True, **saved})

    async def handle_password(self, request: web.Request):
        """{"old", "new"}：改密码。其他浏览器的登录全部失效，改密码的这个留着。"""
        self._require_login(request)
        body = await self._read_json(request)
        stored = await self._blocking(config_store.get_password_hash, self.config_path)
        if not await self._blocking(auth.verify_password, str(body.get("old") or ""), stored or ""):
            await asyncio.sleep(1)
            return self._error("原密码不对", 400)
        new = str(body.get("new") or "")
        if len(new) < auth.MIN_PASSWORD_LENGTH:
            return self._error(f"新密码至少 {auth.MIN_PASSWORD_LENGTH} 位", 400)
        password_hash = await self._blocking(auth.hash_password, new)
        await self._blocking(config_store.set_password_hash, password_hash, self.config_path)
        self.sessions.revoke_all(keep=request.cookies.get(COOKIE_NAME))
        self.logger.bind(tag=TAG).info("管理后台密码已修改，其他登录已失效")
        return self._json({"ok": True})

    async def handle_setup(self, request: web.Request):
        if config_store.get_password_hash(self.config_path):
            return self._error("密码已设置，请直接登录", 409)
        body = await self._read_json(request)
        password = str(body.get("password") or "")
        if len(password) < auth.MIN_PASSWORD_LENGTH:
            return self._error(f"密码至少 {auth.MIN_PASSWORD_LENGTH} 位", 400)
        password_hash = await self._blocking(auth.hash_password, password)
        await self._blocking(config_store.set_password_hash, password_hash, self.config_path)
        self.logger.bind(tag=TAG).info("管理后台密码已设置")
        return self._login_response(request, {"ok": True}, remember=bool(body.get("remember")))

    async def handle_login(self, request: web.Request):
        body = await self._read_json(request)
        stored = await self._blocking(config_store.get_password_hash, self.config_path)
        if not stored:
            return self._error("尚未设置密码", 409)
        if not await self._blocking(auth.verify_password, str(body.get("password") or ""), stored):
            await asyncio.sleep(1)  # 减缓暴力尝试
            self.logger.bind(tag=TAG).warning(f"管理后台登录失败，来源 {request.remote}")
            return self._error("密码错误", 401)
        return self._login_response(request, {"ok": True}, remember=bool(body.get("remember")))

    async def handle_logout(self, request: web.Request):
        self.sessions.revoke(request.cookies.get(COOKIE_NAME))
        response = self._json({"ok": True})
        response.del_cookie(COOKIE_NAME, path="/admin")
        return response

    # ---------- 配置读取、切换与热加载 ----------

    def _merged(self) -> dict:
        if self.config_path:
            return config_store.load_merged(custom_path=self.config_path)
        return config_store.load_merged()

    def _running(self, section: str):
        return (self.config.get("selected_module") or {}).get(section)

    async def _apply_if_selected(self, section: str, name: str, changed: list) -> dict:
        """保存条目后：改的是正在使用的条目才热加载，否则只保存。

        不是默认的条目也要同步进运行中的配置（同 /kiosk/llm/use）：呼名切模型照运行中的配置判断是否配好、
        创建实例，不同步的话新填好 Key 的厂商叫不动，改过的设置要重启才生效。"""
        if not changed:
            return {"applied": True, "note": "没有修改"}
        merged = await self._blocking(self._merged)
        if (merged.get("selected_module") or {}).get(section) != name:
            if name in (merged.get(section) or {}):
                self.config.setdefault(section, {})[name] = copy.deepcopy(merged[section][name])
            return {"applied": True, "note": "已保存"}
        return await self._apply(section)

    async def _switch(self, section: str, name: str) -> dict:
        """切换模块：先写配置再热加载；加载失败时把配置恢复成原来的选择，避免下次启动出问题。

        返回 {"ok": bool, "applied": bool, "note": str}。
        """
        merged = await self._blocking(self._merged)
        if name not in (merged.get(section) or {}):
            raise KeyError(name)
        old = (merged.get("selected_module") or {}).get(section)
        if old == name and self._running(section) == name and \
                (self.config.get(section) or {}).get(name) == merged[section][name]:
            return {"ok": True, "applied": True, "note": "已经在使用"}
        await self._blocking(config_store.select_entry, section, name, merged, self.config_path)
        result = await self._apply(section)
        if result.get("failed"):
            if old and old != name:
                await self._blocking(config_store.select_entry, section, old, merged, self.config_path)
            result["note"] = f"切换失败：{result['error']}。已恢复为原来的 {old or '配置'}"
            self.logger.bind(tag=TAG).warning(f"管理后台切换 {section} 为 {name} 失败，已恢复为 {old}")
            return {"ok": False, **result}
        self.logger.bind(tag=TAG).info(f"管理后台切换 {section} 为: {name}")
        return {"ok": True, **result}

    async def _apply(self, section: str) -> dict:
        """把磁盘上的配置热加载到运行中的服务。

        直接修改共享的 config 字典，再按模块重建实例：
        - LLM、Intent：立即重建；
        - ASR：后台重新加载（本地模型需要几秒）；
        - TTS：每个连接各自创建，不用重建。
        已建立的设备连接继续使用旧实例，设备重新连接后才会用上新配置。
        """
        merged = await self._blocking(self._merged)
        new_section = merged.get(section, {})
        new_selected = merged["selected_module"][section]

        def commit():
            self.config[section] = new_section
            self.config.setdefault("selected_module", {})[section] = new_selected

        if section == "TTS" or self.ws_server is None:
            commit()
            if section == "TTS":
                return {"applied": True, "note": "已保存，设备重新连接后生效"}
            return {"applied": False, "note": "已保存，重启服务后生效"}
        flags = {"LLM": "init_llm", "Intent": "init_intent", "ASR": "init_asr"}
        attr = {"LLM": "_llm", "Intent": "_intent", "ASR": "_asr"}[section]
        key = {"LLM": "llm", "Intent": "intent", "ASR": "asr"}[section]
        # 先用"新配置"创建实例；成功后才写入运行中的配置并替换实例，失败时服务保持原来的状态
        candidate = {**self.config, section: new_section,
                     "selected_module": {**self.config.get("selected_module", {}), section: new_selected}}
        try:
            async with self.ws_server.config_lock:
                modules = await self._blocking(initialize_modules, self.logger, candidate, **{flags[section]: True})
                commit()
                old = getattr(self.ws_server, attr, None)
                setattr(self.ws_server, attr, modules[key])
            self._retire(old, modules[key])
            return {"applied": True, "note": "已生效，设备重新连接后使用新配置"}
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"热加载 {section} 失败: {e}")
            error = str(e).strip().rstrip("。.") or type(e).__name__
            return {"applied": False, "failed": True, "error": error,
                    "note": f"已保存，但加载失败：{error}。服务仍在使用原来的配置，请检查后重试"}

    def _retire(self, old, new) -> None:
        """通知被替换的旧实例退役（如 claude_cli 在连接都关闭后结束后台线程）。
        已连接的设备仍在用旧实例，所以不能直接关掉；出错也不影响这次切换。"""
        if old is None or old is new or not callable(getattr(old, "shutdown", None)):
            return
        try:
            old.shutdown()
        except Exception as e:
            self.logger.bind(tag=TAG).warning(f"旧实例退役出错: {e}")

    # ---------- 呼名 ----------

    async def handle_call_names(self, request: web.Request):
        self._require_login(request)
        from core.agent.call_names import learner

        merged = await self._blocking(self._merged)
        return self._json({"ok": True, **pages.describe_call_names(merged), "learning": learner.status(),
                           "devices": connections.snapshot()})

    async def handle_call_names_save(self, request: web.Request):
        self._require_login(request)
        body = await self._read_json(request)
        try:
            merged = await self._blocking(self._merged)
            changed = await self._blocking(pages.save_call_names, body, merged, self.config_path)
        except ValueError as e:
            return self._error(str(e), 400)
        note = "已保存，马上生效" if changed else "没有修改"
        if changed:
            new_merged = await self._blocking(self._merged)
            self._push_config("call_names", new_merged.get("call_names") or {})
            self.logger.bind(tag=TAG).info(f"管理后台更新了呼名设置: {', '.join(changed)}")
            # 呼名里模型的名字会自动并入热词，模型或开关变了要重新加载语音识别（只改回复语不用）
            if {"models", "enabled"} & set(changed) and pages.describe_hotwords(new_merged)["supported"] \
                    and self._running("ASR") == \
                    (new_merged.get("selected_module") or {}).get("ASR"):
                result = await self._apply("ASR")
                if result.get("failed"):
                    note += f"；热词重新加载失败：{result['error']}"
        return self._json({"ok": True, "changed": changed, "note": note})

    def _push_config(self, key: str, value) -> None:
        """设置马上生效：服务的配置和每个在线连接各自的配置副本都要更新。"""
        self.config[key] = copy.deepcopy(value)
        for item in connections.snapshot():
            conn = connections.get(item["session_id"])
            if conn is not None:
                conn.config[key] = copy.deepcopy(value)

    # ---------- Grok Bot ----------

    async def handle_grokbot(self, request: web.Request):
        self._require_login(request)
        merged = await self._blocking(self._merged)
        return self._json({"ok": True, **pages.describe_grok_bot(merged), **self.grokbot.status()})

    async def handle_grokbot_login(self, request: web.Request):
        self._require_login(request)
        url = await self.grokbot.start_login()
        return self._json({"ok": True, "url": url, **self.grokbot.status()})

    async def handle_grokbot_login_cancel(self, request: web.Request):
        self._require_login(request)
        await self.grokbot.cancel_login()
        return self._json({"ok": True, **self.grokbot.status()})

    async def handle_grokbot_logout(self, request: web.Request):
        self._require_login(request)
        await self.grokbot.logout()
        return self._json({"ok": True, **self.grokbot.status()})

    async def handle_grokbot_refresh(self, request: web.Request):
        self._require_login(request)
        try:
            bots = await self.grokbot.fetch_bots()
        except GrokBotError as e:
            return self._error(f"读取 Bot 列表失败：{e.detail}", 400 if e.kind == ErrorKind.AUTH else 502)
        return self._json({"ok": True, "bots": bots, **self.grokbot.status()})

    async def handle_grokbot_bots(self, request: web.Request):
        """同步勾选的 Bot：每个 Bot 一个 LLM 条目，呼名页据此列出可配称呼的模型。"""
        self._require_login(request)
        body = await self._read_json(request)
        try:
            merged = await self._blocking(self._merged)
            changed = await self._blocking(pages.save_grok_bots, body, merged, self.config_path)
        except ValueError as e:
            return self._error(str(e), 400)
        note = "已保存，马上生效" if changed else "没有修改"
        if changed:
            new_merged = await self._blocking(self._merged)
            # 新条目马上可用（呼名时按条目名创建实例），不用重启；被取消的 Bot 的称呼也一并去掉
            self._push_config("LLM", new_merged.get("LLM") or {})
            self._push_config("call_names", new_merged.get("call_names") or {})
            self.logger.bind(tag=TAG).info(f"管理后台更新了 Grok Bot 条目: {', '.join(changed)}")
            # Bot 的名字会自动并入热词（和呼名页保存时一样），要重新加载语音识别才生效
            if pages.describe_hotwords(new_merged)["supported"] and self._running("ASR") == \
                    (new_merged.get("selected_module") or {}).get("ASR"):
                result = await self._apply("ASR")
                if result.get("failed"):
                    note += f"；热词重新加载失败：{result['error']}"
        if changed and any(c not in (new_merged.get("LLM") or {}) for c in changed):
            note += "；取消的 Bot 对正连着的设备要等它重新连接才生效"
        return self._json({"ok": True, "changed": changed, "note": note})

    async def handle_call_names_learn(self, request: web.Request):
        self._require_login(request)
        from core.agent.call_names import learner

        body = await self._read_json(request)
        session_id = str(body.get("session_id") or "") or None
        online = [d["session_id"] for d in connections.snapshot()]
        if not online:
            return self._error("没有设备在线，请先唤醒设备", 400)
        if session_id and session_id not in online:
            return self._error("这台设备已经离线", 400)
        target = str(body.get("target") or "") or None
        learner.start(session_id or (online[0] if len(online) == 1 else None), target=target)
        self.logger.bind(tag=TAG).info("管理后台开始录入呼名")
        return self._json({"ok": True, "learning": learner.status()})

    async def handle_call_names_learn_stop(self, request: web.Request):
        self._require_login(request)
        from core.agent.call_names import learner

        learner.stop()
        return self._json({"ok": True, "learning": learner.status()})

    # ---------- 状态 ----------

    async def handle_status(self, request: web.Request):
        self._require_login(request)
        merged = await self._blocking(self._merged)
        status = await self._blocking(collect_status, self.config, merged)
        return self._json({"ok": True, **status, **await self._blocking(self._activity, merged)})

    def _activity(self, merged: dict) -> dict:
        """状态页顶部：运行时长、最近 24 小时每小时说了几句、今天几句、今天和本月的费用。"""
        now = time.time()
        since = (int(now // 3600) - 23) * 3600
        try:
            hours = chat_history.db().hourly_turns(since)
        except Exception:  # noqa: BLE001 —— 聊天记录关着或库坏了，不影响状态页
            hours = []
        midnight = time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))
        try:
            today = chat_history.db().hourly_turns(midnight, 24)
        except Exception:  # noqa: BLE001
            today = []
        try:
            cost = billing.brief(merged)
        except Exception:  # noqa: BLE001
            cost = {}
        try:
            stages = pipeline.describe_stages(merged)
        except Exception:  # noqa: BLE001
            stages = []
        return {"stages": stages, "uptime": int(now - self.started_at), "hours": hours, "hours_since": since,
                "turns_today": sum(today), "cost": {k: cost.get(k) for k in ("today", "month", "currencies")}}

    # ---------- 更新网关 ----------

    async def handle_update(self, request: web.Request):
        """当前版本、最近一次检查的结果、最近一次更新的进度（页面在更新期间轮询它）。"""
        self._require_login(request)
        version = await self._blocking(self.updater.version)
        status = await self._blocking(self.updater.status)
        # 检查之后代码变了（更新过）的话，那次的结果就不算数了，页面会重新检查
        check = self.updater.last_check
        if check and check.get("current", {}).get("commit") != version["current"].get("commit"):
            check = None
        return self._json({"ok": True, **version, "check": check, "update": status})

    async def handle_update_check(self, request: web.Request):
        """和远端比较有没有新版本，只取不改；连不上时 check.state 为 error，message 说明原因。"""
        self._require_login(request)
        await self._read_json(request)
        result = await self._blocking(self.updater.check)
        return self._json({"ok": True, "check": result})

    async def handle_update_start(self, request: web.Request):
        """在后台运行 scripts/update.sh：快进代码、按需补装依赖、重启网关；失败时退回原来的版本。"""
        self._require_login(request)
        await self._read_json(request)
        # 开始前把登录交接出去：网关重启后页面不用重新登录
        await self._blocking(self.sessions.save, self.sessions_handover)
        try:
            status = await self._blocking(self.updater.start)
        except updater.UpdateError as e:
            self.sessions_handover.unlink(missing_ok=True)
            return self._error(str(e), 409)
        except OSError as e:
            self.sessions_handover.unlink(missing_ok=True)
            return self._error(f"启动更新失败：{e}", 500)
        self.logger.bind(tag=TAG).info(f"管理后台开始更新网关（来源 {request.remote}）")
        return self._json({"ok": True, "update": status})

    # ---------- 大模型 ----------

    def _claude_install(self, merged: dict, version: bool = True) -> dict:
        """version=False 只看装没装：查版本要运行一次 claude --version（设备上约半秒）。"""
        from core.providers.llm.claude_cli.claude_cli import find_claude_cli

        entry = (merged.get("LLM") or {}).get(pages.CLAUDE_CLI_ENTRY) or {}
        cli = find_claude_cli(entry.get("cli_path") or None)
        return {"installed": bool(cli), "path": cli or "", "version": _claude_version(cli) if cli and version else ""}

    _dsh_version_cache = {}

    def _dsh_install(self, merged: dict, version: bool = True) -> dict:
        """网关上有没有 dsh 和 Node 24；版本要启动一次 node（约 1 秒），按路径缓存。"""
        from core.agent.dsh_session import dsh_version, find_dsh, find_node_dir

        entry = (merged.get("LLM") or {}).get(pages.DSH_ENTRY) or {}
        dsh, node = find_dsh(entry.get("dsh_path") or None), find_node_dir(entry.get("node_dir") or None)
        ver = ""
        if dsh and version:
            ver = self._dsh_version_cache.get(dsh)
            if ver is None:
                try:
                    ver = dsh_version(dsh, node)
                except Exception:  # noqa: BLE001
                    ver = ""
                self._dsh_version_cache[dsh] = ver
        return {"installed": bool(dsh), "path": dsh or "", "node": node or "", "version": ver}

    async def handle_llm_dsh(self, request: web.Request):
        self._require_login(request)
        body = await self._read_json(request)
        try:
            merged = await self._blocking(self._merged)
            changed = await self._blocking(pages.save_dsh, body, merged, self.config_path)
        except ValueError as e:
            return self._error(str(e), 400)
        self.logger.bind(tag=TAG).info(f"管理后台更新了 DSH 设置: {', '.join(changed) or '无变化'}")
        return self._json({"ok": True, "changed": changed, **await self._apply_if_selected("LLM", pages.DSH_ENTRY, changed)})

    async def handle_llm(self, request: web.Request):
        self._require_login(request)
        merged = await self._blocking(self._merged)
        data = pages.describe_llm(merged)
        data["claude"]["install"] = await self._blocking(self._claude_install, merged)
        data["dsh"]["install"] = await self._blocking(self._dsh_install, merged)
        data["running"] = self._running("LLM")
        return self._json({"ok": True, **data})

    async def handle_llm_claude(self, request: web.Request):
        self._require_login(request)
        body = await self._read_json(request)
        try:
            merged = await self._blocking(self._merged)
            changed = await self._blocking(pages.save_claude_cli, body, merged, self.config_path)
        except KeyError:
            return self._error("配置里没有 ClaudeCLI 条目", 404)
        except ValueError as e:
            return self._error(str(e), 400)
        self.logger.bind(tag=TAG).info(f"管理后台更新了 Claude CLI 设置: {', '.join(changed) or '无变化'}")
        return self._json({"ok": True, "changed": changed,
                           **await self._apply_if_selected("LLM", pages.CLAUDE_CLI_ENTRY, changed)})

    async def handle_llm_vendor(self, request: web.Request):
        self._require_login(request)
        vendor = request.match_info["vendor"]
        body = await self._read_json(request)
        started = time.monotonic()
        try:
            merged = await self._blocking(self._merged)
            changed = await self._blocking(pages.save_vendor, vendor, body, merged, self.config_path)
        except KeyError:
            return self._error("不支持这个厂商", 404)
        except ValueError as e:
            return self._error(str(e), 400)
        entry = catalog.VENDOR_BY_ID[vendor]["entry"]
        applied = await self._apply_if_selected("LLM", entry, changed)
        # 用时记进日志：保存曾经要等十几秒，原因没能复现
        self.logger.bind(tag=TAG).info(f"管理后台更新了 {vendor} 的 API 设置: {', '.join(changed) or '无变化'}"
                                       f"（用时 {time.monotonic() - started:.1f} 秒）")
        return self._json({"ok": True, "changed": changed, **applied})

    @staticmethod
    def _test_llm(config: dict) -> dict:
        with billing.unbilled():   # [xiaozhi-hub] 测试连接只问一个字，不记费用
            return AdminHandler._test_llm_once(config)

    @staticmethod
    def _test_llm_once(config: dict) -> dict:
        import httpx
        import openai
        from core.providers.llm.openai.openai import LLMProvider

        started = time.time()
        reply = ""
        try:
            provider = LLMProvider({**config, "timeout": TEST_TIMEOUT})
            provider.client.max_retries = 0   # 默认失败后再试 2 次
            parts = provider.response("", [{"role": "system", "content": "你是语音助手，回答尽量简短。"},
                                           {"role": "user", "content": "只回复一个字：好"}])
            try:
                for part in parts:   # 收到第一个字就够了：地址、Key、模型都没问题
                    if part.strip():
                        reply = part
                        break
            finally:
                parts.close()   # 断开没读完的流
        except openai.AuthenticationError:
            return {"ok": False, "error": "API Key 不对或已失效"}
        except openai.NotFoundError:
            return {"ok": False, "error": "找不到这个模型或接口地址，请检查模型名称和接口地址"}
        except openai.RateLimitError:
            return {"ok": False, "error": "请求太频繁或额度用完了"}
        except (openai.APIConnectionError, httpx.TransportError) as e:
            # 读超时：连接建立了、请求也发出去了，是模型那边迟迟不出字，不是网络不通
            if isinstance(e, httpx.ReadTimeout) or isinstance(e.__cause__, httpx.ReadTimeout):
                return {"ok": False, "error": f"接口连上了，但模型 {TEST_TIMEOUT['read']:.0f} 秒都没有回应。"
                                              "可能是模型想得太久或服务太忙，稍后再试；一直这样就换个模型"}
            return {"ok": False, "error": "连不上接口地址，请检查地址和网络；国外的服务在国内可能要走代理，或者换国内的服务"}
        except openai.APIStatusError as e:
            return {"ok": False, "error": f"接口返回错误 {e.status_code}：{str(e.message)[:200]}"}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}
        return {"ok": True, "seconds": round(time.time() - started, 1), "reply": reply.strip()[:60]}

    async def handle_llm_vendor_test(self, request: web.Request):
        self._require_login(request)
        vendor = request.match_info["vendor"]
        body = await self._read_json(request)
        try:
            merged = await self._blocking(self._merged)
            config = pages.vendor_test_config(vendor, body, merged)
        except KeyError:
            return self._error("不支持这个厂商", 404)
        except ValueError as e:
            return self._error(str(e), 400)
        result = await self._blocking(self._test_llm, config)
        return self._json(result) if result["ok"] else self._error(result["error"], 400)

    async def handle_llm_use(self, request: web.Request):
        """切换到本地 CLI 或某个 API 厂商；意图识别自动跟着切。"""
        self._require_login(request)
        body = await self._read_json(request)
        name, result = await self._use_llm(body)
        if name is None:
            return result
        return self._json(result)

    async def _use_llm(self, body: dict):
        """把默认模型切到 body 指定的（{"kind": "cli"} 或 {"kind": "api", "vendor": ...}）。

        成功返回 (条目名, 结果)；失败返回 (None, 错误响应)。
        """
        merged = await self._blocking(self._merged)
        if body.get("kind") == "cli":
            name = pages.CLAUDE_CLI_ENTRY
        elif body.get("kind") == "dsh":
            name = pages.DSH_ENTRY
            if name not in (merged.get("LLM") or {}):
                return None, self._error("先保存一次 DSH 的设置", 400)
        else:
            vendor = catalog.VENDOR_BY_ID.get(str(body.get("vendor") or ""))
            if vendor is None:
                return None, self._error("不支持这个厂商", 404)
            name = vendor["entry"]
            if not pages.describe_vendor(vendor, merged)["configured"]:
                return None, self._error(
                    f"{vendor['label']} 还没配置好，请先填写接口地址、模型和 API Key 并保存", 400)
        try:
            result = await self._switch("LLM", name)
        except KeyError:
            return None, self._error(f"配置里没有 {name}", 404)
        if not result["ok"]:
            return None, self._error(result["note"], 400)

        merged = await self._blocking(self._merged)
        intent = await self._blocking(pages.desired_intent, merged, name, self.config_path)
        if config_store.entry_type(merged, "Intent") != intent:
            try:
                intent_result = await self._switch("Intent", pages.intent_entry_name(merged, intent))
                note = "意图识别已自动改为" + ("“不用插件”" if intent == "nointent" else "“用插件”")
                result["note"] += "；" + (note if intent_result["ok"] else intent_result["note"])
            except KeyError:
                result["note"] += f"；配置里没有 {intent}，请检查意图识别"
        return name, result

    # ---------- 本机设备接口 ----------

    @staticmethod
    def _from_this_machine(request: web.Request) -> bool:
        """请求直接来自本机。经过反向代理转发来的（带 X-Forwarded-For / Forwarded）不算：
        代理和网关在同一台机器上时来源地址也是 127.0.0.1，但发起请求的可能是任何人。"""
        if "X-Forwarded-For" in request.headers or "Forwarded" in request.headers:
            return False
        return request.remote in ("127.0.0.1", "::1")

    async def handle_kiosk_llm(self, request: web.Request):
        """可选的模型（装了 Claude Code 的话排第一个，然后是配好的 API 厂商、勾选的 Grok Bot）和当前选中的。

        带 ?device=<device-id> 时，selected 是这台设备实际会用的模型：它上次切到的（呼名或在这里切换），
        还可用的话；default 始终是后台默认模型。
        """
        if not self._from_this_machine(request):
            return self._error("仅限本机", 403)
        merged = await self._blocking(self._merged)
        return self._json({"ok": True, **await self._kiosk_models(merged, request.query.get("device"))})

    async def _kiosk_models(self, merged: dict, device: str = None) -> dict:
        data = pages.describe_llm(merged)
        options = [
            {"kind": "api", "vendor": v["id"], "label": v["label"], "entry": v["entry"], "model": v["model"]}
            for v in data["vendors"] if v.get("configured")
        ]
        install = await self._blocking(self._claude_install, merged, version=False)
        if install.get("installed"):
            options.insert(0, {"kind": "cli", "vendor": "claude-code", "label": "Claude Code",
                               "entry": pages.CLAUDE_CLI_ENTRY, "model": data["claude"]["values"].get("model", "")})
        if data["dsh"]["exists"] and (await self._blocking(self._dsh_install, merged, False))["installed"]:
            options.insert(1 if install.get("installed") else 0,
                           {"kind": "dsh", "vendor": "dsh", "label": "DSH", "entry": pages.DSH_ENTRY,
                            "model": data["dsh"]["values"].get("model", "")})
        options += [{"kind": "grok", "vendor": "grok_bot", "label": b["bot_name"], "entry": b["entry"], "model": "Grok Bot"}
                    for b in data["grok"]["bots"] if llm_router.model_ready(merged, b["entry"])]
        selected = data["selected"]
        if device:
            name = (llm_router.store().get(device) or {}).get("model")
            if name and llm_router.model_ready(merged, name) and any(o["entry"] == name for o in options):
                selected = name
        return {"selected": selected, "default": data["selected"], "kind": data["kind"], "vendor": data["vendor"],
                "options": options}

    async def handle_kiosk_llm_use(self, request: web.Request):
        """把这台设备切到 body 指定的模型（{"kind": "cli"}、{"kind": "api", "vendor": …}、{"kind": "grok", "entry": …}，
        都带 "device"），同呼名切换：只这台设备切过去并记住，在线的连接马上跟着切（新模型从新对话开始），
        意图识别跟着这台设备的模型走（llm_router.intent_mode）。后台默认模型和全局意图识别不变，
        不写配置、不重建默认实例；默认模型只在管理后台改。

        返回切换后的模型列表（同 GET /kiosk/llm?device=…），设备不用再查一次。
        """
        if not self._from_this_machine(request):
            return self._error("仅限本机", 403)
        body = await self._read_json(request)
        device = str(body.get("device") or "")
        if not device:
            return self._error("缺少 device", 400)
        merged = await self._blocking(self._merged)
        kind = body.get("kind")
        if kind == "grok":
            name = str(body.get("entry") or "")
            if name not in {b["entry"] for b in pages.grok_entries(merged)} or not llm_router.model_ready(merged, name):
                return self._error("没有这个 Grok Bot，请先在管理后台的 Grok Bot 页勾选", 404)
        elif kind == "dsh":
            name = pages.DSH_ENTRY
            if name not in (merged.get("LLM") or {}):
                return self._error("还没在管理后台设置 DSH", 404)
        elif kind == "cli":
            name = pages.CLAUDE_CLI_ENTRY
            if name not in (merged.get("LLM") or {}):
                return self._error(f"配置里没有 {name}", 404)
        else:
            vendor = catalog.VENDOR_BY_ID.get(str(body.get("vendor") or ""))
            if vendor is None:
                return self._error("不支持这个厂商", 404)
            name = vendor["entry"]
            if not pages.describe_vendor(vendor, merged)["configured"]:
                return self._error(f"{vendor['label']} 还没配置好，请先填写接口地址、模型和 API Key 并保存", 400)
        # 后台保存过、但没设成默认的条目，运行中的配置里还是旧的（或没有）：补进去，切换时照它创建实例
        running = self.config.setdefault("LLM", {})
        if running.get(name) != merged["LLM"][name]:
            running[name] = copy.deepcopy(merged["LLM"][name])
        before = llm_router.store().get(device)
        await self._blocking(llm_router.store().put, device, {"model": name})
        for item in connections.snapshot():
            conn = connections.get(item["session_id"])
            if conn is None or item["device_id"] != device:
                continue
            try:
                ok = await self._blocking(llm_router.switch, conn, name, remember=False)
                error = "" if ok is not False else "模型没有配置好"
            except Exception as e:
                ok, error = False, str(e).strip() or type(e).__name__
            if ok is False:
                self.logger.bind(tag=TAG).error(f"在线连接 {item['session_id']} 切换模型 {name} 失败: {error}")
                if before:   # 恢复成原来记住的
                    await self._blocking(llm_router.store().put, device,
                                         {k: v for k, v in before.items() if k != "last_active"})
                else:
                    await self._blocking(llm_router.store().forget, device)
                return self._error(f"切换失败：{error}", 500)
        self.logger.bind(tag=TAG).info(f"设备 {device} 切换模型为 {name}（只这台设备）")
        return self._json({"ok": True, "applied": True, "note": "已切换，这台设备之后都用它",
                           **await self._kiosk_models(merged, device)})

    async def handle_kiosk_reports(self, request: web.Request):
        """{"device", "quiet", "play"}：返回这台设备有几条待汇报。

        quiet：设备是否在睡眠时段（期间不主动念）。每次查询都带上，网关重启后不会丢。
        play：用户点了"有一条待汇报"，马上在设备的连接上念（睡眠时段也念）；playing 表示已安排，设备没连着时为 false。
        """
        if not self._from_this_machine(request):
            return self._error("仅限本机", 403)
        body = await self._read_json(request)
        device = str(body.get("device") or "")
        if not device:
            return self._error("缺少 device", 400)
        reports.set_quiet(device, bool(body.get("quiet")))
        presence.beat(device, str(body.get("version") or ""))   # 设备每 10 秒查一次：顺便当心跳（状态页的在线/待机）
        playing = bool(body.get("play")) and reports.deliver_now(device)
        return self._json({"ok": True, "count": reports.count(device), "playing": playing})

    async def handle_kiosk_asr(self, request: web.Request):
        """只识别、不对话：body 是 10 秒以内的 16kHz 单声道 16 位 PCM，返回 {"text", "ms"}。

        用服务已经加载、所有连接共用的本机识别模型（设备客户端用它复核唤醒词）。
        当前识别不是本机模型（如豆包流式）时返回 501，客户端据此跳过复核。
        """
        if not self._from_this_machine(request):
            return self._error("仅限本机", 403)
        asr = getattr(self.ws_server, "_asr", None)
        if not callable(getattr(asr, "recognize_pcm", None)):
            return self._error("当前语音识别不是本机模型", 501)
        pcm = await request.read()
        if len(pcm) < 2 or len(pcm) > KIOSK_ASR_MAX_BYTES:
            return self._error("要 10 秒以内的 16kHz 单声道 16 位 PCM", 400)
        started = time.monotonic()
        text = await self._blocking(asr.recognize_pcm, pcm)
        return self._json({"ok": True, "text": text, "ms": round((time.monotonic() - started) * 1000)})

    async def handle_kiosk_tts(self, request: web.Request):
        """{"text"}：只合成、不对话，返回 16 位单声道 WAV（24 kHz）。

        用运行中配置选的语音合成和音色（和设备听到的回复一样）。设备客户端设提醒时用它把要念的话预先合成好
        存在本机，到点直接播，不再依赖网关。流式合成（没有整句合成）返回 501，客户端到点只响铃、屏幕显示文字。"""
        if not self._from_this_machine(request):
            return self._error("仅限本机", 403)
        body = await self._read_json(request)
        text = str(body.get("text") or "").strip()
        if not text or len(text) > KIOSK_TTS_MAX_CHARS:
            return self._error(f"text 要 1 到 {KIOSK_TTS_MAX_CHARS} 个字", 400)
        started = time.monotonic()
        try:
            pcm = await self._blocking(self._synthesize, text)
        except NotImplementedError:
            return self._error("当前语音合成不支持整句合成", 501)
        except Exception as e:
            self.logger.bind(tag=TAG).warning(f"/kiosk/tts 合成失败: {e}")
            pcm = None
        if not pcm:
            return self._error("合成失败，请检查语音合成服务能否访问", 502)
        self.logger.bind(tag=TAG).info(f"/kiosk/tts 合成 {len(text)} 字，用时 {time.monotonic() - started:.1f}s")
        return web.Response(body=_wav(pcm, KIOSK_TTS_RATE), content_type="audio/wav", headers=SECURITY_HEADERS)

    def _synthesize(self, text: str) -> bytes:
        from core.providers.tts.dto.dto import InterfaceType
        from core.utils.modules_initialize import initialize_tts

        tts = initialize_tts(self.config)
        if tts.interface_type != InterfaceType.NON_STREAM:
            raise NotImplementedError
        return tts.synthesize_pcm(text, KIOSK_TTS_RATE)

    async def handle_kiosk_chat_history(self, request: web.Request):
        """?device=<device-id>[&limit=N]：这台设备存下来的聊天记录，给屏幕显示。

        返回 {"models": {条目名: [{id, role: user|bot, text, time, interrupted}]}}，每个模型最近 limit 条
        （默认 chat_history.display_limit）。"""
        if not self._from_this_machine(request):
            return self._error("仅限本机", 403)
        device = request.query.get("device") or ""
        if not device:
            return self._error("缺少 device", 400)
        cfg = chat_history.settings(await self._blocking(self._merged))
        try:
            limit = max(1, min(int(request.query.get("limit") or cfg["display_limit"]), 500))
        except ValueError:
            return self._error("limit 要是数字", 400)
        models = await self._blocking(chat_history.history, device, limit)
        return self._json({"ok": True, "models": models})

    async def handle_kiosk_geocode(self, request: web.Request):
        """?q=地名：查到的地方 {"name", "latitude", "longitude"}（设备设住宅地址时先确认）。查不到返回 ok:false 和原因。"""
        if not self._from_this_machine(request):
            return self._error("仅限本机", 403)
        from core.utils import open_meteo

        q = (request.query.get("q") or "").strip()
        if not q or len(q) > 40:
            return self._error("q 要 1 到 40 个字", 400)
        try:
            place = await open_meteo.lookup(q)
        except open_meteo.WeatherError as e:
            return self._json({"ok": False, "error": str(e)})
        return self._json({"ok": True, "place": place})

    async def handle_kiosk_home(self, request: web.Request):
        """{"device", "place": {"name", "latitude", "longitude"} | null}：这台设备的住宅地址（core/utils/device_homes.py）。
        问天气没说地点、系统提示里的设备所在地都用它；place 为 null 时删掉，改用后台的设备所在地。"""
        if not self._from_this_machine(request):
            return self._error("仅限本机", 403)
        from core.utils import device_homes

        body = await self._read_json(request)
        device = str(body.get("device") or "").strip()
        place = body.get("place")
        if not device:
            return self._error("缺少 device", 400)
        if place is not None:
            try:
                place = {"name": str(place["name"]).strip(), "latitude": float(place["latitude"]),
                         "longitude": float(place["longitude"])}
            except (KeyError, TypeError, ValueError):
                return self._error("place 要有 name、latitude、longitude", 400)
            if not place["name"] or not (-90 <= place["latitude"] <= 90 and -180 <= place["longitude"] <= 180):
                return self._error("place 不对", 400)
        await self._blocking(device_homes.put, device, place)
        return self._json({"ok": True})

    # ---------- 清单（core/utils/device_lists.py） ----------

    def _lists_url(self, secret: str) -> str:
        return self._photo_url("x").rsplit("/photo/", 1)[0] + f"/lists/{secret}"

    async def handle_kiosk_lists(self, request: web.Request):
        if not self._from_this_machine(request):
            return self._error("仅限本机", 403)
        from core.utils import device_lists

        try:
            v = await self._blocking(device_lists.view, request.query.get("device") or "")
        except ValueError as e:
            return self._error(str(e), 400)
        return self._json({"ok": True, **v, "url": self._lists_url(v["secret"])})

    async def handle_kiosk_lists_change(self, request: web.Request):
        """{"device", "op": add|remove|check|clear_done|delete_list, "name", "items", "done"}"""
        if not self._from_this_machine(request):
            return self._error("仅限本机", 403)
        from core.utils import device_lists

        b = await self._read_json(request)
        try:
            v = await self._blocking(device_lists.change, str(b.get("device") or ""), str(b.get("op") or ""),
                                     b.get("name"), b.get("items") or [], b.get("done"))
        except ValueError as e:
            return self._json({"ok": False, "error": str(e)})
        return self._json({"ok": True, **v, "url": self._lists_url(v["secret"])})

    async def handle_lists_page(self, request: web.Request):
        html = (STATIC_DIR / "lists.html").read_text(encoding="utf-8")
        return web.Response(text=html, content_type="text/html",
                            headers={**SECURITY_HEADERS, "Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})

    async def handle_lists_data(self, request: web.Request):
        from core.utils import device_lists

        device = await self._blocking(device_lists.by_secret, request.match_info["secret"])
        if not device:
            return self._json({"ok": False, "error": "这个链接无效，在小麦的设置页重新扫码"})
        v = await self._blocking(device_lists.view, device)
        return self._json({"ok": True, "version": v["version"], "lists": v["lists"]})

    async def handle_lists_data_change(self, request: web.Request):
        from core.utils import device_lists

        device = await self._blocking(device_lists.by_secret, request.match_info["secret"])
        if not device:
            return self._json({"ok": False, "error": "这个链接无效，在小麦的设置页重新扫码"})
        b = await self._read_json(request)
        if b.get("op") not in ("add", "remove", "check", "clear_done"):
            return self._json({"ok": False, "error": "不支持的操作"})
        try:
            v = await self._blocking(device_lists.change, device, b["op"], b.get("name"), b.get("items") or [], b.get("done"))
        except ValueError as e:
            return self._json({"ok": False, "error": str(e)})
        return self._json({"ok": True, "version": v["version"], "lists": v["lists"]})

    # ---------- 家人声音（core/utils/local_voiceprint.py） ----------

    async def handle_kiosk_voiceprint(self, request: web.Request):
        if not self._from_this_machine(request):
            return self._error("仅限本机", 403)
        from core.utils import local_voiceprint as lv

        return self._json({"ok": True, "available": lv.available(),
                           "people": await self._blocking(lv.people, request.query.get("device") or "")})

    async def handle_kiosk_voiceprint_enroll(self, request: web.Request):
        """?device=&name=&child=1，请求体是 16 kHz 16 位单声道 PCM（4～8 秒）：加一段这个人的声音。"""
        if not self._from_this_machine(request):
            return self._error("仅限本机", 403)
        from core.utils import local_voiceprint as lv

        if not lv.available():
            return self._json({"ok": False, "error": "网关上还没有声纹模型（models/voiceprint/）"})
        pcm = await request.read()
        if len(pcm) > KIOSK_ASR_MAX_BYTES:
            return self._error("音频太长", 413)
        try:
            r = await self._blocking(lv.enroll, request.query.get("device") or "", request.query.get("name") or "",
                                     pcm, request.query.get("child") == "1")
        except (ValueError, RuntimeError) as e:
            return self._json({"ok": False, "error": str(e)})
        return self._json({"ok": True, **r})

    async def handle_kiosk_voiceprint_remove(self, request: web.Request):
        if not self._from_this_machine(request):
            return self._error("仅限本机", 403)
        from core.utils import local_voiceprint as lv

        b = await self._read_json(request)
        try:
            await self._blocking(lv.remove, str(b.get("device") or ""), str(b.get("name") or ""))
        except ValueError as e:
            return self._json({"ok": False, "error": str(e)})
        return self._json({"ok": True})

    async def handle_kiosk_prefs(self, request: web.Request):
        """{"device", "kids": {"enabled", "age", "voice"}}：设备在设置页改的、网关要知道的偏好（儿童模式）。
        下一次连接生效（系统提示和童声在连接建立时定）。"""
        if not self._from_this_machine(request):
            return self._error("仅限本机", 403)
        from core.utils import device_prefs

        body = await self._read_json(request)
        device = str(body.get("device") or "")
        try:
            if "kids" in body:
                await self._blocking(device_prefs.put_kids, device, body.get("kids") or {})
            if "translate" in body:
                await self._blocking(device_prefs.put_translate, device, body.get("translate") or {})
        except (ValueError, TypeError) as e:
            return self._error(str(e), 400)
        return self._json({"ok": True})

    async def handle_memory_history(self, request: web.Request):
        """?device=：这台设备最近的对话（家长在「记忆」页看孩子问了什么），每个模型最近 50 条。"""
        self._require_login(request)
        device = request.query.get("device") or ""
        if not device:
            return self._error("缺少 device", 400)
        return self._json({"ok": True, "models": await self._blocking(chat_history.history, device, 50)})

    async def handle_kiosk_today(self, request: web.Request):
        """?device=：设备首页用的今天：农历（含节气、节日）和住宅地址的天气（20 分钟缓存）。
        没设住宅地址时用后台「查天气」里的设备所在地；天气查不到时 weather 为 null、带 weather_error。"""
        if not self._from_this_machine(request):
            return self._error("仅限本机", 403)
        from core.utils import device_homes, open_meteo

        device = request.query.get("device") or ""
        out = {"ok": True, "lunar": await self._blocking(_lunar_today), "weather": None}
        home = device_homes.get(device)
        if not home:
            merged = await self._blocking(self._merged)
            name = str(((merged.get("plugins") or {}).get("get_weather") or {}).get("home_location") or "").strip()
            if not name:
                out["weather_error"] = "还没设住宅地址"
                return self._json(out)
        key = (home and (home["latitude"], home["longitude"])) or name
        hit = self._today_cache.get(key)
        if hit and time.time() - hit[0] < 1200:
            out["weather"] = hit[1]
            return self._json(out)
        try:
            out["weather"] = open_meteo.brief(*await open_meteo.fetch(home, None if home else name))
            self._today_cache[key] = (time.time(), out["weather"])
        except open_meteo.WeatherError as e:
            out["weather_error"] = str(e)
        return self._json(out)

    # ---------- 拍照问答（core/agent/photo_ask.py） ----------

    def _photo_url(self, token: str) -> str:
        """手机要打开的地址：网关的局域网 IP + HTTP 端口（可用 server.photo_base_url 改）。"""
        from core.utils.util import get_local_ip

        server = self.config.get("server") or {}
        base = str(server.get("photo_base_url") or "").rstrip("/")
        if not base:
            base = f"http://{get_local_ip()}:{int(server.get('http_port') or 8003)}"
        return f"{base}/photo/{token}"

    async def handle_kiosk_photo_start(self, request: web.Request):
        """{"device", "question"}：开一个拍照问答，返回一次性上传地址（给屏幕上的二维码）。"""
        if not self._from_this_machine(request):
            return self._error("仅限本机", 403)
        body = await self._read_json(request)
        try:
            s = await self._blocking(photo_ask.start, str(body.get("device") or ""), body.get("question"),
                                     str(body.get("purpose") or "ask"))
        except ValueError as e:
            return self._error(str(e), 400)
        merged = await self._blocking(self._merged)
        vision = photo_ask.vision_settings(merged)
        return self._json({"ok": True, **s, "url": self._photo_url(s["token"]), "vision_ready": vision["ready"],
                           "avatar_ready": bool(photo_ask.dashscope_key(merged)),
                           "styles": [{"id": k, "name": v} for k, v in photo_ask.AVATAR_STYLES.items()]})

    async def handle_kiosk_photo(self, request: web.Request):
        if not self._from_this_machine(request):
            return self._error("仅限本机", 403)
        v = photo_ask.view(request.match_info["token"])
        return self._json({"ok": True, **v}) if v else self._error("没有这个拍照问答", 404)

    async def handle_kiosk_photo_image(self, request: web.Request):
        if not self._from_this_machine(request):
            return self._error("仅限本机", 403)
        path = photo_ask.photo_path(request.match_info["token"])
        if not path or not os.path.isfile(path):
            return self._error("还没有照片", 404)
        return web.FileResponse(path, headers={"Content-Type": "image/jpeg", "Cache-Control": "no-store"})

    async def handle_kiosk_photo_cancel(self, request: web.Request):
        if not self._from_this_machine(request):
            return self._error("仅限本机", 403)
        return self._json({"ok": True, "cancelled": photo_ask.cancel(request.match_info["token"])})

    async def handle_kiosk_avatar_generate(self, request: web.Request):
        """{"style"}：把收到的照片画成这个画风的卡通形象（后台跑，设备轮询 /kiosk/photo/<令牌>）。"""
        if not self._from_this_machine(request):
            return self._error("仅限本机", 403)
        body = await self._read_json(request)
        token = request.match_info["token"]
        try:
            photo_ask.begin_avatar(token, int(body.get("style") or 0))
        except (ValueError, TypeError) as e:
            return self._json({"ok": False, "error": str(e)})
        except LookupError as e:
            return self._error(str(e), 404)
        merged = await self._blocking(self._merged)
        asyncio.get_running_loop().run_in_executor(None, photo_ask.generate_avatar, token, int(body["style"]), merged)
        return self._json({"ok": True})

    async def handle_kiosk_avatar_image(self, request: web.Request):
        if not self._from_this_machine(request):
            return self._error("仅限本机", 403)
        path = photo_ask.avatar_path(request.match_info["token"])
        if not path or not os.path.isfile(path):
            return self._error("还没画好", 404)
        return web.FileResponse(path, headers={"Content-Type": "application/octet-stream", "Cache-Control": "no-store"})

    async def handle_kiosk_album_image(self, request: web.Request):
        if not self._from_this_machine(request):
            return self._error("仅限本机", 403)
        try:
            index = int(request.match_info["index"])
        except ValueError:
            return self._error("index 要是数字", 400)
        path = photo_ask.album_path(request.match_info["token"], index)
        if not path or not os.path.isfile(path):
            return self._error("没有这张", 404)
        return web.FileResponse(path, headers={"Content-Type": "image/jpeg", "Cache-Control": "no-store"})

    async def handle_photo_page(self, request: web.Request):
        html = (STATIC_DIR / "photo.html").read_text(encoding="utf-8")
        return web.Response(text=html, content_type="text/html",
                            headers={**SECURITY_HEADERS, "Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})

    async def handle_photo_status(self, request: web.Request):
        v = photo_ask.view(request.match_info["token"])
        if not v:
            return self._json({"ok": False, "error": "这个链接无效或过期了，在小麦上重新说一次"})
        return self._json({"ok": True, "state": v["state"], "question": v["question"], "answer": v["answer"],
                           "error": v["error"], "purpose": v["purpose"]})

    async def handle_photo_upload(self, request: web.Request):
        """手机传照片（multipart：question、photo）。存下来后在后台问看图模型，马上返回。"""
        token = request.match_info["token"]
        if not photo_ask.get(token):
            return self._json({"ok": False, "error": "这个链接无效或过期了，在小麦上重新说一次"})
        question, data = None, b""
        try:
            reader = await request.multipart()
            while True:
                part = await reader.next()
                if part is None:
                    break
                if part.name == "question":
                    question = (await part.text())[:200]
                elif part.name == "photo":
                    while True:
                        chunk = await part.read_chunk(65536)
                        if not chunk:
                            break
                        data += chunk
                        if len(data) > photo_ask.MAX_BYTES:
                            return self._json({"ok": False, "error": "照片太大了"})
        except Exception:
            return self._json({"ok": False, "error": "上传的格式不对"})
        try:
            await self._blocking(photo_ask.receive, token, data, question)
        except (ValueError, LookupError) as e:
            return self._json({"ok": False, "error": str(e)})
        merged = await self._blocking(self._merged)
        asyncio.get_running_loop().run_in_executor(None, photo_ask.answer, token, merged)  # 生成形象的不问
        return self._json({"ok": True})

    async def handle_vision(self, request: web.Request):
        self._require_login(request)
        return self._json({"ok": True, **photo_ask.vision_settings(await self._blocking(self._merged))})

    async def handle_vision_save(self, request: web.Request):
        """{"provider", "api_key"?, "model"?, "url"?}：选用看图模型（Key 留空表示不改，或用「大模型」页同一家的）。"""
        self._require_login(request)
        body = await self._read_json(request)
        try:
            changed = await self._blocking(photo_ask.save_vision, str(body.get("provider") or ""),
                                           body.get("api_key"), self.config_path, body.get("model"), body.get("url"))
        except KeyError:
            return self._error("没有这个看图模型", 404)
        except ValueError as e:
            return self._error(str(e), 400)
        self.logger.bind(tag=TAG).info(f"管理后台更新了看图模型 {body.get('provider')}: {', '.join(changed) or '无变化'}")
        return self._json({"ok": True, "changed": changed, **photo_ask.vision_settings(await self._blocking(self._merged))})

    async def handle_vision_test(self, request: web.Request):
        """用一张程序画的测试图问一次，确认 Key 能用（不记费用）。"""
        self._require_login(request)
        from core import billing
        merged = await self._blocking(self._merged)

        def run():
            with billing.unbilled():
                return photo_ask.ask_vision(merged, "图里是什么颜色、什么形状", _test_image())
        try:
            text = await self._blocking(run)
        except photo_ask.PhotoError as e:
            return self._json({"ok": False, "error": str(e)})
        except Exception as e:
            return self._json({"ok": False, "error": f"连不上看图模型：{e}"})
        return self._json({"ok": True, "message": text})

    async def handle_models_list(self, request: web.Request):
        """{"url", "api_key"?, "vendor"?}：问一个 OpenAI 兼容服务（Ollama、LM Studio 等）有哪些模型。

        先问 {url}/models（OpenAI 协议）；Ollama 再问 /api/tags 拿到大小和能不能看图。vendor 给了而 api_key 没给时，
        用「大模型」页这一家已保存的 Key。"""
        self._require_login(request)
        body = await self._read_json(request)
        url = str(body.get("url") or "").strip().rstrip("/")
        from urllib.parse import urlparse
        if urlparse(url).scheme not in ("http", "https") or not urlparse(url).netloc:
            return self._error("接口地址要以 http:// 或 https:// 开头", 400)
        key = str(body.get("api_key") or "").strip()
        if not key and body.get("vendor") in catalog.VENDOR_BY_ID:
            merged = await self._blocking(self._merged)
            saved = ((merged.get("LLM") or {}).get(catalog.VENDOR_BY_ID[body["vendor"]]["entry"]) or {}).get("api_key")
            key = "" if config_store.is_placeholder(saved) else str(saved)
        import aiohttp
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        models = []
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as s:
                async with s.get(url + "/models", headers=headers) as r:
                    if r.status == 401:
                        return self._json({"ok": False, "error": "这个服务要 Key（401），先填上 Key 再读取"})
                    if r.status == 200:
                        data = await r.json(content_type=None)
                        models = [{"id": str(m.get("id"))} for m in (data.get("data") or []) if isinstance(m, dict) and m.get("id")]
                root = url[:-3] if url.endswith("/v1") else url
                try:
                    async with s.get(root + "/api/tags") as r:
                        if r.status == 200:
                            tags = (await r.json(content_type=None)).get("models") or []
                            info = {t.get("name"): t for t in tags if isinstance(t, dict)}
                            if not models:
                                models = [{"id": n} for n in info]
                            for m in models:
                                t = info.get(m["id"]) or {}
                                if t.get("size"):
                                    m["size"] = f"{t['size'] / 1e9:.1f} GB"
                                fam = " ".join((t.get("details") or {}).get("families") or []).lower()
                                if any(w in (m["id"] + " " + fam) for w in ("vl", "vision", "llava", "gemma3", "clip", "mllama")):
                                    m["vision"] = True
                except aiohttp.ClientError:
                    pass
        except asyncio.TimeoutError:
            return self._json({"ok": False, "error": "8 秒没回应：地址对吗？服务开着吗？"})
        except aiohttp.ClientError as e:
            hint = "（Ollama 默认只听 127.0.0.1，局域网访问要设 OLLAMA_HOST=0.0.0.0）" if "11434" in url else ""
            return self._json({"ok": False, "error": f"连不上 {url}：{e.__class__.__name__}{hint}"})
        if not models:
            return self._json({"ok": False, "error": "服务没有返回模型列表"})
        return self._json({"ok": True, "models": models[:200]})

    async def handle_kiosk_chat_clear(self, request: web.Request):
        """{"device", "memory_only"?}：清除这台设备的聊天记录（所有模型），和语音"清除聊天记录"同一套。

        只清本机：库里的记录和记忆、在线连接里网关这边的对话；Claude Code、Grok Bot 自己的会话和记忆不碰。
        memory_only：只清记忆，记录留着。返回 {"deleted", "agents": 聊过的 Claude Code / Grok Bot}。"""
        if not self._from_this_machine(request):
            return self._error("仅限本机", 403)
        body = await self._read_json(request)
        device = str(body.get("device") or "")
        if not device:
            return self._error("缺少 device", 400)
        online = [c for c in (connections.get(i["session_id"]) for i in connections.snapshot()
                              if i["device_id"] == device) if c is not None]
        merged = await self._blocking(self._merged)
        result = await self._blocking(chat_history.clear, device, merged, bool(body.get("memory_only")), online)
        if not body.get("memory_only"):  # 拍照问答的照片和聊天记录一起删
                await self._blocking(photo_ask.clear, device)
        return self._json({"ok": True, **result})

    # ---------- 工作原理 ----------

    async def handle_pipeline(self, request: web.Request):
        self._require_login(request)
        merged = await self._blocking(self._merged)
        return self._json({"ok": True, **await self._blocking(pipeline.describe, merged)})

    # ---------- 语音识别、语音合成 ----------

    async def handle_asr(self, request: web.Request):
        self._require_login(request)
        merged = await self._blocking(self._merged)
        return self._json({"ok": True, **await self._blocking(pages.describe_asr, merged),
                           "running": self._running("ASR")})

    async def handle_asr_use(self, request: web.Request):
        """切换到本地 X-ASR 或豆包。"""
        self._require_login(request)
        body = await self._read_json(request)
        choice = str(body.get("choice") or "")
        merged = await self._blocking(self._merged)
        d = await self._blocking(pages.describe_asr, merged)
        if choice == "local":
            name = catalog.LOCAL_ASR_ENTRY
            if not d["local"]["exists"]:
                return self._error(f"配置里没有 {name} 条目，请重新运行安装脚本生成配置", 404)
            if not (d["local"]["model"] or {}).get("ok"):
                return self._error(f"X-ASR 模型文件不完整（{(d['local']['model'] or {}).get('detail', '')}），"
                                   "请重新运行安装脚本下载", 400)
        elif choice == "doubao":
            name = catalog.DOUBAO_ASR_ENTRY
            if not d["doubao"]["configured"]:
                return self._error("豆包还没配置好，请先填写 API Key（或 APP ID 和 Access Token）并保存", 400)
        elif choice in catalog.ASR_PROVIDER_BY_ID:
            p = next(x for x in d["providers"] if x["id"] == choice)
            name = p["entry"]
            if p["missing"]:
                return self._error(f"{p['label']} 需要的 Python 包还没装（{', '.join(p['missing'])}），"
                                   "请在服务器上运行 bash scripts/update.sh 或重新运行安装脚本", 400)
            if p["model"] is not None and not p["model"]["ok"]:
                return self._error(f"{p['label']} 的模型文件还没下载好（{p['model']['detail']}）", 400)
            if not p["configured"]:
                return self._error(f"{p['label']} 还没配置好，请先填好并保存", 400)
            if not p["exists"]:   # a local model: write its entry (catalog defaults) before switching to it
                try:
                    await self._blocking(pages.save_asr_provider, choice, {}, merged, self.config_path)
                except ValueError as e:
                    return self._error(str(e), 400)
        else:
            return self._error("不支持这个语音识别", 400)
        try:
            result = await self._switch("ASR", name)
        except KeyError:
            return self._error(f"配置里没有 {name}", 404)
        return self._json(result) if result["ok"] else self._error(result["note"], 400)

    async def handle_asr_provider_save(self, request: web.Request):
        """{"id", "values"}：保存某个识别服务（catalog.ASR_PROVIDERS）；正在用它时重新加载。"""
        self._require_login(request)
        body = await self._read_json(request)
        pid = str(body.get("id") or "")
        try:
            merged = await self._blocking(self._merged)
            changed = await self._blocking(pages.save_asr_provider, pid, body.get("values"), merged, self.config_path)
        except KeyError:
            return self._error("没有这个语音识别服务", 404)
        except ValueError as e:
            return self._error(str(e), 400)
        entry = catalog.ASR_PROVIDER_BY_ID[pid]["entry"]
        self.logger.bind(tag=TAG).info(f"管理后台更新了语音识别 {entry}: {', '.join(changed) or '无变化'}")
        return self._json({"ok": True, "changed": changed, **await self._apply_if_selected("ASR", entry, changed)})

    async def handle_asr_xasr(self, request: web.Request):
        """{"variant": "fast" | "full"}：本地 X-ASR 用快速版还是精度版的模型文件。"""
        self._require_login(request)
        body = await self._read_json(request)
        try:
            merged = await self._blocking(self._merged)
            changed = await self._blocking(pages.save_x_asr_variant, str(body.get("variant") or ""), merged, self.config_path)
        except ValueError as e:
            return self._error(str(e), 400)
        return self._json({"ok": True, "changed": changed,
                           **await self._apply_if_selected("ASR", catalog.LOCAL_ASR_ENTRY, changed)})

    async def handle_asr_download(self, request: web.Request):
        """{"id": <识别服务 id> | "xasr:fast" | "xasr:full"}：在后台下载本地模型。"""
        self._require_login(request)
        from core.admin import model_download
        body = await self._read_json(request)
        try:
            model_dir, repo, files = pages.download_target(str(body.get("id") or ""))
        except KeyError:
            return self._error("这个识别服务没有可下载的模型", 404)
        self.logger.bind(tag=TAG).info(f"管理后台开始下载模型 {repo} → {model_dir}")
        return self._json({"ok": True, "download": model_download.start(model_dir, repo, files)})

    async def handle_doubao_save(self, request: web.Request):
        self._require_login(request)
        body = await self._read_json(request)
        try:
            merged = await self._blocking(self._merged)
            changed = await self._blocking(pages.save_doubao, body, merged, self.config_path)
        except ValueError as e:
            return self._error(str(e), 400)
        self.logger.bind(tag=TAG).info(f"管理后台更新了豆包语音识别设置: {', '.join(changed) or '无变化'}")
        return self._json({"ok": True, "changed": changed,
                           **await self._apply_if_selected("ASR", catalog.DOUBAO_ASR_ENTRY, changed)})

    async def handle_doubao_test(self, request: web.Request):
        self._require_login(request)
        from core.providers.asr.doubao_stream import check_connection

        body = await self._read_json(request)
        try:
            merged = await self._blocking(self._merged)
            config = pages.doubao_test_config(body, merged)
        except ValueError as e:
            return self._error(str(e), 400)
        result = await check_connection(config)
        return self._json(result) if result["ok"] else self._error(result["error"], 400)

    async def handle_hotwords_save(self, request: web.Request):
        """保存热词后重新加载语音识别（X-ASR 约 1 秒，豆包马上）：新连接马上用上，在线设备重连后生效。"""
        self._require_login(request)
        body = await self._read_json(request)
        try:
            merged = await self._blocking(self._merged)
            changed = await self._blocking(pages.save_hotwords, body.get("words"), merged, self.config_path)
        except ValueError as e:
            return self._error(str(e), 400)
        if not changed:
            return self._json({"ok": True, "changed": [], "note": "没有修改"})
        self.logger.bind(tag=TAG).info("管理后台更新了热词")
        # 热词在配置顶层，先放进运行中的配置，重新加载语音识别时才用得上
        self._push_config("asr_hotwords", (await self._blocking(self._merged)).get("asr_hotwords") or [])
        name = merged["selected_module"]["ASR"]
        result = await self._apply_if_selected("ASR", name, changed)
        if result.get("failed"):
            return self._json({"ok": True, "changed": changed, **result})
        note = "已保存并重新加载语音识别；新连接马上生效，在线设备重新连接后生效" if result.get("applied") else result["note"]
        return self._json({"ok": True, "changed": changed, "applied": result.get("applied"), "note": note})

    async def handle_corrections_save(self, request: web.Request):
        self._require_login(request)
        body = await self._read_json(request)
        try:
            merged = await self._blocking(self._merged)
            changed = await self._blocking(pages.save_corrections, body.get("items"), merged, self.config_path)
        except ValueError as e:
            return self._error(str(e), 400)
        if changed:
            self._push_config("asr_corrections", (await self._blocking(self._merged)).get("asr_corrections") or {})
            self.logger.bind(tag=TAG).info("管理后台更新了识别纠错表")
        return self._json({"ok": True, "changed": changed, "note": "已保存，马上生效" if changed else "没有修改"})

    async def handle_vad_save(self, request: web.Request):
        """保存说完停顿时长。VAD 实例是所有连接共用的，直接改它的阈值，在线设备的下一句话就用上。"""
        self._require_login(request)
        body = await self._read_json(request)
        try:
            merged = await self._blocking(self._merged)
            value = await self._blocking(pages.save_vad_silence, body.get("silence_ms"), merged, self.config_path)
        except ValueError as e:
            return self._error(str(e), 400)
        if value is None:
            return self._json({"ok": True, "changed": [], "note": "没有修改"})
        name = merged["selected_module"]["VAD"]
        self.logger.bind(tag=TAG).info(f"管理后台更新了说完停顿时长: {value}ms")
        if self._running("VAD") != name:
            return self._json({"ok": True, "changed": ["silence_ms"], "note": "已保存，重启服务后生效"})
        self.config.setdefault("VAD", {}).setdefault(name, {})[pages.VAD_SILENCE_FIELD] = value
        instances = [getattr(self.ws_server, "_vad", None)]
        for item in connections.snapshot():
            conn = connections.get(item["session_id"])
            if conn is None:
                continue
            instances.append(getattr(conn, "vad", None))
            vad_cfg = ((getattr(conn, "config", None) or {}).get("VAD") or {}).get(name)
            if isinstance(vad_cfg, dict):
                vad_cfg[pages.VAD_SILENCE_FIELD] = value
        for vad in instances:
            if hasattr(vad, "silence_threshold_ms"):
                vad.silence_threshold_ms = value
        # 豆包每句话开始时才把判停时长发给识别服务，改正在用的实例，下一句话就用上
        for item in connections.snapshot():
            asr = getattr(connections.get(item["session_id"]), "asr", None)
            if getattr(asr, "interface_type", None) is not None and hasattr(asr, "end_window_size"):
                asr.end_window_size = max(200, value)
        return self._json({"ok": True, "changed": ["silence_ms"], "note": "已保存，马上生效"})

    async def handle_tts(self, request: web.Request):
        self._require_login(request)
        merged = await self._blocking(self._merged)
        return self._json({"ok": True, **pages.describe_tts(merged), "running": self._running("TTS")})

    async def handle_tts_save(self, request: web.Request):
        """{"engine": "edge" | "qwen3", "voice", "api_key"?}：保存这个引擎的音色（和 Key），并改用它。

        不带 engine 是旧接口：只改 EdgeTTS 的音色。"""
        self._require_login(request)
        body = await self._read_json(request)
        engine = str(body.get("engine") or "")
        try:
            merged = await self._blocking(self._merged)
            if engine:
                changed = await self._blocking(pages.save_tts, engine, {k: body.get(k) for k in (
                    "voice", "api_key", "api_url", "model", "format")}, merged, self.config_path)
                name = catalog.TTS_ENGINE_BY_ID[engine]["entry"]
            else:
                changed = await self._blocking(pages.save_tts_voice, str(body.get("voice") or ""), merged, self.config_path)
                name = (merged.get("selected_module") or {}).get("TTS")
        except KeyError:
            return self._error("没有这个语音合成引擎", 404)
        except ValueError as e:
            return self._error(str(e), 400)
        if "api_key" in changed:  # 换了 Key，之前没合成成功的试听重新来
            for k in [k for k in self._voice_samples if k.startswith("qwen3:")]:
                self._voice_samples.pop(k, None)
        self.logger.bind(tag=TAG).info(f"管理后台更新了语音合成 {name}: {', '.join(changed) or '无变化'}，音色 {body.get('voice')}")
        if engine and (merged.get("selected_module") or {}).get("TTS") != name:
            result = await self._switch("TTS", name)
            return self._json({"changed": changed, **result}) if result["ok"] else self._error(result["note"], 400)
        return self._json({"ok": True, "changed": changed, **await self._apply_if_selected("TTS", name, changed)})

    async def handle_tts_preview(self, request: web.Request):
        """用 EdgeTTS 合成一句示例，返回 mp3 给浏览器播放；engine=qwen3 时用已保存的百炼 Key 合成 WAV。"""
        self._require_login(request)
        voice = request.query.get("voice", "")
        if request.query.get("engine") == "qwen3":
            return await self._qwen3_preview(voice)
        if request.query.get("engine") == "lan":
            return await self._lan_tts_preview()
        info = next((v for v in catalog.EDGE_VOICES if v["value"] == voice), None)
        if info is None:
            return self._error("不支持这个音色", 400)
        audio = self._voice_samples.get(voice)
        if audio is None:
            import edge_tts

            chunks = []
            try:
                async def synth():
                    async for chunk in edge_tts.Communicate(catalog.voice_sample(info["group"]), voice).stream():
                        if chunk["type"] == "audio":
                            chunks.append(chunk["data"])
                await asyncio.wait_for(synth(), timeout=20)
            except Exception as e:
                self.logger.bind(tag=TAG).warning(f"试听合成失败 {voice}: {e}")
                return self._error("试听合成失败，请检查电脑能否访问微软的语音服务", 502)
            audio = b"".join(chunks)
            self._voice_samples[voice] = audio
        return web.Response(body=audio, content_type="audio/mpeg",
                            headers={**SECURITY_HEADERS, "Cache-Control": "private, max-age=86400"})

    async def _lan_tts_preview(self):
        """局域网合成服务：用已保存的地址、模型、音色合成一句，原样返回。"""
        import aiohttp

        merged = await self._blocking(self._merged)
        entry = pages._entry(merged, "TTS", catalog.LAN_TTS_ENTRY)
        if entry.get("type") != "openai":
            return self._error("先保存一次局域网合成服务的设置，才能试听", 400)
        fmt = str(entry.get("format") or "wav")
        key = str(entry.get("api_key") or "none")
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25)) as s:
                async with s.post(str(entry.get("api_url")), headers={"Authorization": f"Bearer {key}"},
                                  json={"model": entry.get("model"), "input": catalog.voice_sample("普通话"),
                                        "voice": entry.get("voice"), "response_format": fmt}) as r:
                    body = await r.read()
                    if r.status != 200:
                        return self._error(f"合成服务返回 {r.status}：{body[:200].decode('utf-8', 'replace')}", 502)
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            return self._error(f"连不上合成服务：{e.__class__.__name__}", 502)
        ctype = {"mp3": "audio/mpeg", "opus": "audio/ogg", "flac": "audio/flac"}.get(fmt, "audio/wav")
        return web.Response(body=body, content_type=ctype, headers=SECURITY_HEADERS)

    async def _qwen3_preview(self, voice: str):
        from core.utils import qwen3_tts_api

        info = next((v for v in catalog.QWEN3_TTS_VOICES if v["value"] == voice), None)
        if info is None:
            return self._error("不支持这个音色", 400)
        cache_key = f"qwen3:{voice}"
        audio = self._voice_samples.get(cache_key)
        if audio is None:
            merged = await self._blocking(self._merged)
            entry = pages._entry(merged, "TTS", catalog.QWEN3_TTS_ENTRY)
            if entry.get("type") != "qwen3_tts" or config_store.is_placeholder(entry.get("api_key")):
                return self._error("先填写百炼的 API Key 并保存，才能试听千问 TTS", 400)
            try:
                pcm = await asyncio.wait_for(qwen3_tts_api.synthesize(
                    catalog.voice_sample(info["group"]), str(entry.get("api_key") or "").strip(), voice,
                    str(entry.get("model") or "qwen3-tts-flash"), str(entry.get("language_type") or "Chinese"),
                    str(entry.get("url") or qwen3_tts_api.DEFAULT_URL), 20), timeout=25)
            except Exception as e:
                self.logger.bind(tag=TAG).warning(f"千问 TTS 试听失败 {voice}: {e}")
                return self._error(f"试听合成失败：{e}", 502)
            if not pcm:
                return self._error("试听合成失败：没有返回音频", 502)
            audio = qwen3_tts_api.pcm_to_wav(pcm)
            self._voice_samples[cache_key] = audio
        return web.Response(body=audio, content_type="audio/wav",
                            headers={**SECURITY_HEADERS, "Cache-Control": "private, max-age=86400"})

    # ---------- 意图识别 ----------

    async def handle_intent(self, request: web.Request):
        self._require_login(request)
        merged = await self._blocking(self._merged)
        return self._json({"ok": True, **pages.describe_intent(merged), "running": self._running("Intent")})

    async def handle_intent_save(self, request: web.Request):
        self._require_login(request)
        body = await self._read_json(request)
        mode = str(body.get("mode") or "")
        if mode not in catalog.INTENT_MODES:
            return self._error("意图识别只能选“不用插件”或“用插件”", 400)
        merged = await self._blocking(self._merged)
        if pages.llm_kind(merged) == "cli" and mode != "nointent":
            return self._error("使用 Claude Code 时只能选“不用插件”", 400)
        notes = []
        try:
            if "functions" in body:
                changed = await self._blocking(pages.save_intent_plugins, body["functions"], merged, self.config_path)
                if changed and mode == "function_call":
                    merged = await self._blocking(self._merged)
                    name = pages.intent_entry_name(merged, "function_call")
                    if (merged.get("selected_module") or {}).get("Intent") == name:
                        notes.append((await self._apply("Intent"))["note"])
            if pages.llm_kind(merged) != "cli":
                await self._blocking(pages.remember_api_intent, mode, self.config_path)
                # 设备切到非默认的 API 模型时按它选意图识别（llm_router.intent_mode），要马上用上新的选择
                self.config.setdefault("admin", {})["api_intent"] = mode
            result = await self._switch("Intent", pages.intent_entry_name(merged, mode))
        except KeyError:
            return self._error(f"配置里没有 {mode}", 404)
        except ValueError as e:
            return self._error(str(e), 400)
        if not result["ok"]:
            return self._error(result["note"], 400)
        if result["note"] == "已经在使用" and notes:
            result["note"] = notes[-1]
        return self._json(result)

    # ---------- 插件设置 ----------

    async def handle_plugin_settings(self, request: web.Request):
        self._require_login(request)
        merged = await self._blocking(self._merged)
        try:
            return self._json({"ok": True, **plugin_settings.describe(merged, request.match_info["id"])})
        except KeyError:
            return self._error("没有这个插件设置", 404)

    async def handle_plugin_settings_save(self, request: web.Request):
        """保存后马上同步进运行中的配置和每个在线连接：插件每次调用都读 conn.config，不用重连。"""
        self._require_login(request)
        settings_id = request.match_info["id"]
        body = await self._read_json(request)
        try:
            merged = await self._blocking(self._merged)
            changed = await self._blocking(plugin_settings.save, settings_id, body, merged, self.config_path)
            key = plugin_settings.config_key(settings_id)
        except KeyError:
            return self._error("没有这个插件设置", 404)
        except ValueError as e:
            return self._error(str(e), 400)
        if not changed:
            return self._json({"ok": True, "changed": [], "note": "没有修改",
                               "configured": plugin_settings.configured(merged, settings_id)})
        merged = await self._blocking(self._merged)
        fresh = (merged.get("plugins") or {}).get(key) or {}
        self.config.setdefault("plugins", {})[key] = copy.deepcopy(fresh)
        for conn in connections.all_connections():
            if isinstance(getattr(conn, "config", None), dict):
                conn.config.setdefault("plugins", {})[key] = copy.deepcopy(fresh)
        self.logger.bind(tag=TAG).info(f"管理后台更新了插件设置 {settings_id}: {', '.join(changed)}")
        return self._json({"ok": True, "changed": changed, "note": "已保存，马上生效",
                           "configured": plugin_settings.configured(merged, settings_id)})

    async def handle_plugin_settings_test(self, request: web.Request):
        self._require_login(request)
        body = await self._read_json(request)
        try:
            merged = await self._blocking(self._merged)
            result = await plugin_settings.run_test(request.match_info["id"], body, merged)
        except KeyError:
            return self._error("这个插件没有测试", 404)
        except ValueError as e:
            return self._error(str(e), 400)
        return self._json({"ok": True, **result})

    # ---------- 在线设备与表情测试 ----------

    def _emotion_progress(self, session_id: str):
        run = self._emotion_runs.get(session_id)
        if not run:
            return None
        task = run["task"]
        return {"running": not task.done(), "stopped": task.cancelled(), "index": run["index"], "total": run["total"],
                "emotion": run["emotion"], "emoji": EMOTION_EMOJI.get(run["emotion"], "")}

    # ---------- 费用（core/billing） ----------

    async def handle_billing(self, request: web.Request):
        """今日、本月、累计的费用和 token，近 30 天，按模型、设备、用途，单价表和额度。"""
        self._require_login(request)
        merged = await self._blocking(self._merged)
        data = await self._blocking(billing.summary, merged)
        data["entries"] = [{"entry": n, "label": llm_router.model_label(n)}
                           for n, c in (merged.get("LLM") or {}).items()
                           if isinstance(c, dict) and llm_router.model_ready(merged, n)]
        return self._json({"ok": True, **data})

    async def handle_billing_save(self, request: web.Request):
        """{"prices"?: [...], "quota"?: {...}, "settings"?: {...}}：保存后马上生效。"""
        self._require_login(request)
        body = await self._read_json(request)
        try:
            update = _billing_update(body)
        except ValueError as e:
            return self._error(str(e), 400)

        def save():
            with config_store.edit_custom(self.config_path) as data:
                cur = dict(data.get("billing") or {})
                cur.update(update)
                data["billing"] = cur
            return dict((self._merged().get("billing") or {}))
        value = await self._blocking(save)
        self._push_config("billing", value)
        billing.configure(self.config)
        return self._json({"ok": True})

    async def handle_billing_clear(self, request: web.Request):
        self._require_login(request)
        await self._blocking(billing.db().clear)
        logger.bind(tag=TAG).info("费用记录已在管理后台清空")
        return self._json({"ok": True})

    async def handle_kiosk_billing(self, request: web.Request):
        """本机设备接口：设备设置页显示的今日、本月、累计费用和额度。"""
        if not self._from_this_machine(request):
            return self._error("仅限本机", 403)
        merged = await self._blocking(self._merged)
        return self._json({"ok": True, **await self._blocking(billing.brief, merged)})

    # ---------- 记忆（聊天记录总结） ----------

    async def handle_memory(self, request: web.Request):
        """每台设备的记忆全文、更新时间、存了哪些模型的记录。"""
        self._require_login(request)
        merged = await self._blocking(self._merged)
        cfg = chat_history.settings(merged)
        online = {d["device_id"] for d in connections.snapshot() if d.get("device_id")}
        devices = await self._blocking(chat_history.describe_memories, merged)
        for d in devices:
            d["online"] = d["device"] in online
        return self._json({"ok": True, "devices": devices,
                           "settings": {k: cfg[k] for k in ("enabled", "summary", "max_messages", "max_tokens",
                                                            "summary_min", "summary_chars")}})

    async def handle_memory_save(self, request: web.Request):
        """{"device", "summary", "version"?}：保存手改的记忆（summary 为空就是清空），下一次请求就用新的。
        带 version 时，打开编辑后自动总结更新过就不保存，返回 409 和现在的记忆。"""
        self._require_login(request)
        body = await self._read_json(request)
        device = str(body.get("device") or "")
        summary = body.get("summary")
        if not device or not isinstance(summary, str):
            return self._error("缺少 device 或 summary", 400)
        if len(summary) > 5000:
            return self._error("记忆最多 5000 字", 400)
        version = body.get("version")
        ok, memory = await self._blocking(chat_history.set_memory, device, summary,
                                          int(version) if isinstance(version, int) else None)
        if not ok:
            return self._json({"ok": False, "error": "你编辑期间记忆被自动总结更新了，请看过新内容再改", "memory": memory}, 409)
        return self._json({"ok": True, "memory": memory})

    async def handle_devices(self, request: web.Request):
        self._require_login(request)
        devices = connections.snapshot()
        from core.agent import llm_router
        from core.utils import device_homes

        for d in devices:
            d["emotion_test"] = self._emotion_progress(d["session_id"])
            # 这台设备此刻在跟谁说话（呼名切到的模型）；和大模型页的全局默认是两回事
            d["model_label"] = ("Claude Code" if d.get("model") == llm_router.CLAUDE_CLI_ENTRY
                                else "DSH" if d.get("model") == llm_router.DSH_ENTRY
                                else llm_router.model_label(d["model"])) if d.get("model") else ""
            home = device_homes.get(d.get("device_id") or "")
            d["home"] = home["name"] if home else ""
        return self._json({"ok": True, "devices": devices, "now": int(time.time()),
                           "seen": presence.snapshot(self._connected_ids()),
                           "emotions": [{"emotion": name, "emoji": emoji, "label": catalog.EMOTION_LABELS.get(name, name)}
                                        for emoji, name in EMOJI_MAP.items()]})

    @staticmethod
    async def _send_emotion(conn, emotion: str):
        await conn.websocket.send(json.dumps(
            {"type": "llm", "text": EMOTION_EMOJI[emotion], "emotion": emotion, "session_id": conn.session_id}
        ))

    def _online(self, session_id: str):
        conn = connections.get(session_id)
        return conn if conn is not None and getattr(conn, "websocket", None) is not None else None

    def _stop_emotion_test(self, session_id: str) -> bool:
        run = self._emotion_runs.get(session_id)
        if run and not run["task"].done():
            run["task"].cancel()
            return True
        return False

    async def handle_emotion_one(self, request: web.Request):
        """给设备单独发一个表情。"""
        self._require_login(request)
        session_id = request.match_info["session_id"]
        conn = self._online(session_id)
        if conn is None:
            return self._error("设备不在线", 404)
        body = await self._read_json(request)
        emotion = str(body.get("emotion") or "")
        if emotion not in EMOTION_EMOJI:
            return self._error("不支持这个表情", 400)
        self._stop_emotion_test(session_id)
        try:
            await self._send_emotion(conn, emotion)
        except Exception as e:
            return self._error(f"发送失败：{e}", 502)
        return self._json({"ok": True})

    async def handle_emotion_test(self, request: web.Request):
        """依次给设备发送全部情绪消息，用来查看固件实际支持哪些表情。"""
        self._require_login(request)
        session_id = request.match_info["session_id"]
        conn = self._online(session_id)
        if conn is None:
            return self._error("设备不在线", 404)
        body = await self._read_json(request)
        try:
            interval = min(max(float(body.get("interval") or 2), 0.5), 10)
        except (TypeError, ValueError):
            return self._error("interval 需要是数字（秒）", 400)
        self._stop_emotion_test(session_id)
        sequence = [{"emoji": e, "emotion": name} for e, name in EMOJI_MAP.items()]
        run = {"index": 0, "total": len(sequence), "emotion": ""}

        async def play():
            try:
                for i, item in enumerate(sequence, 1):
                    run["index"], run["emotion"] = i, item["emotion"]
                    await self._send_emotion(conn, item["emotion"])
                    await asyncio.sleep(interval)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # 测试中途设备断开等
                self.logger.bind(tag=TAG).info(f"表情测试已中止：{e}")

        run["task"] = asyncio.get_running_loop().create_task(play())
        self._emotion_runs = {sid: r for sid, r in self._emotion_runs.items() if connections.get(sid) is not None}
        self._emotion_runs[session_id] = run
        self.logger.bind(tag=TAG).info(f"开始表情测试：设备 {conn.device_id}，共 {len(sequence)} 种，间隔 {interval} 秒")
        return self._json({"ok": True})

    async def handle_emotion_stop(self, request: web.Request):
        self._require_login(request)
        self._stop_emotion_test(request.match_info["session_id"])
        return self._json({"ok": True})


def _billing_update(body: dict) -> dict:
    """费用设置的取值校验（单价、额度、时区、节假日），返回要写进 billing 的键。"""
    import datetime
    import re
    from zoneinfo import ZoneInfo
    out = {}

    def money(v, name):
        try:
            x = float(v or 0)
        except (TypeError, ValueError):
            raise ValueError(f"{name}要填数字")
        if x < 0 or x > 1e6:
            raise ValueError(f"{name}超出范围")
        return x

    def days(v, name):
        items = [str(d).strip() for d in (v or []) if str(d).strip()]
        for d in items:
            try:
                datetime.date.fromisoformat(d)
            except ValueError:
                raise ValueError(f"{name}里的 {d} 不是 YYYY-MM-DD 日期")
        return sorted(set(items))

    hours_re = re.compile(r"^\s*(\d\d:\d\d-\d\d:\d\d)(\s*[,，]\s*\d\d:\d\d-\d\d:\d\d)*\s*$")
    days_re = re.compile(r"^(all|workday|weekend|[1-7](-[1-7])?([,，][1-7](-[1-7])?)*)$")
    if "prices" in body:
        rules = []
        for r in body.get("prices") or []:
            if not isinstance(r, dict) or not str(r.get("match") or "").strip():
                raise ValueError("每条单价都要填模型名")
            rule = {"match": str(r["match"]).strip()[:300], "label": str(r.get("label") or "")[:60],
                    "currency": r.get("currency") if r.get("currency") in billing.pricing.CURRENCIES else "CNY",
                    **{k: money(r.get(k), "单价") for k in billing.pricing.PRICE_KEYS}, "tiers": []}
            if r.get("checked"):
                rule["checked"] = str(r["checked"])[:120]
            for t in r.get("tiers") or []:
                d, h = str(t.get("days") or "all").strip(), str(t.get("hours") or "").strip()
                if not days_re.match(d):
                    raise ValueError(f"时段的日期 {d} 要写 all / workday / weekend 或 1-5 这样的星期")
                if h and not hours_re.match(h):
                    raise ValueError(f"时段 {h} 要写成 09:00-12:00,14:00-18:00")
                rule["tiers"].append({"name": str(t.get("name") or "时段")[:20], "days": d, "hours": h,
                                      **{k: money(t.get(k), "时段单价") for k in billing.pricing.PRICE_KEYS}})
            rules.append(rule)
        out["prices"] = rules
    if "quota" in body:
        q = body.get("quota") or {}
        if q.get("action", "block") not in ("block", "downgrade"):
            raise ValueError("超额后的做法只能是 block 或 downgrade")
        out["quota"] = {"currency": q.get("currency") if q.get("currency") in billing.pricing.CURRENCIES else "CNY",
                        "daily": money(q.get("daily"), "每天额度"), "monthly": money(q.get("monthly"), "每月额度"),
                        "device_daily": money(q.get("device_daily"), "每台设备每天额度"),
                        "action": q.get("action", "block"), "downgrade_to": str(q.get("downgrade_to") or "")[:80]}
    if "settings" in body:
        st = body.get("settings") or {}
        if "timezone" in st:
            try:
                ZoneInfo(str(st["timezone"]))
            except Exception:
                raise ValueError(f"没有这个时区：{st['timezone']}")
            out["timezone"] = str(st["timezone"])
        if "holidays" in st:
            out["holidays"] = days(st["holidays"], "节假日")
        if "workdays" in st:
            out["workdays"] = days(st["workdays"], "调休上班日")
        if "count_claude_code" in st:
            out["count_claude_code"] = bool(st["count_claude_code"])
    return out


def _test_image() -> bytes:
    """看图模型的测试图：白底上一个红色圆（PNG，用 zlib 现画，不依赖图像库）。"""
    import struct
    import zlib

    w = h = 96
    rows = []
    for y in range(h):
        row = bytearray([0])
        for x in range(w):
            inside = (x - 48) ** 2 + (y - 48) ** 2 < 30 ** 2
            row += bytes((220, 40, 40) if inside else (255, 255, 255))
        rows.append(bytes(row))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b""))


def _lunar_today() -> dict:
    """农历月日（九月十九）、节气、节日。cnlunar 算不出来时返回空的。"""
    import datetime

    try:
        import cnlunar

        lu = cnlunar.Lunar(datetime.datetime.now(), godType="8char")
        festivals = [f for f in (lu.get_legalHolidays(), lu.get_otherHolidays(), lu.get_otherLunarHolidays()) if f]
        return {"text": f"{lu.lunarMonthCn[:-1]}{lu.lunarDayCn}", "term": lu.todaySolarTerms if lu.todaySolarTerms != "无" else "",
                "festival": "、".join(festivals)}
    except Exception:
        return {"text": "", "term": "", "festival": ""}
