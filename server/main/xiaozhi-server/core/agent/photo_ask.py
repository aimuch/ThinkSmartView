"""[xiaozhi-hub] 拍照问答（设计画布第 7 块）：设备没有能用的摄像头，借手机拍。

1. 设备说「帮我看下这是什么」→ 设备工具调 /kiosk/photo/start，拿到一次性链接（5 分钟有效、只能传一张），
   在屏幕上显示二维码；
2. 手机扫码打开 /photo/<token>（局域网，令牌就是凭证，128 位随机），拍照或从相册选，压缩后上传；
3. 网关把照片交给看图模型（后台「大模型 → 看图模型」：智谱 GLM-4V-Flash 免费，或百炼的千问 VL），
   设备轮询 /kiosk/photo/<token> 拿到答案，把照片和答案放进对话，再请对话模型据此回答（念出来）。

会话只放内存（服务重启就丢，最多也就 5 分钟的事）；照片存在 data/photos/<设备>/，7 天后删，
清除聊天记录时一起删。

purpose="avatar"（设计画布第 8 块）：同一套上传，照片不问看图模型，而是交给通义万相「人像风格重绘」
（wanx-style-repaint-v1，要百炼 Key）画成卡通形象，设备取回后存成自己的对话形象。
"""
import base64
import os
import re
import secrets
import shutil
import threading
import time

from config.config_loader import get_project_dir
from config.logger import setup_logging

TAG = __name__
logger = setup_logging()

UPLOAD_SECONDS = 300      # 二维码有效期
ALBUM_PER_SESSION = 30    # 家庭相册：一个码最多传几张
KEEP_SECONDS = 1800       # 会话结果留多久（设备取答案、手机看答案）
PHOTO_DAYS = 7
MAX_BYTES = 8 * 1024 * 1024
QUESTION_MAX = 100
VISION_TIMEOUT = 40

# [xiaozhi-hub] 看图模型（设计画布第 15 块 A）。都走 OpenAI 兼容的 chat.completions + image_url。
# share：和「大模型」页的哪个厂商共用 Key（那边填过就直接用）；local：本机/局域网服务，Key 可以不填，接口地址可改。
# models：候选，第一个是默认；页面上可以填别的。DeepSeek 只有 V4.1 Flash 收图（2026-11-09 实测 V4-Pro 返回空）。
PROVIDERS = {
    "deepseek": {"entry": "DeepSeekVLLM", "label": "DeepSeek", "note": "V4.1 Flash 能看图，约 0.001 元一张",
                 "model_name": "deepseek-flash", "models": ["deepseek-flash"], "url": "https://api.deepseek.com",
                 "key_url": "https://platform.deepseek.com/api_keys", "share": "deepseek",
                 # 不关思考的话，400 个 token 全花在思考上、回答是空的；关了 1 秒出答案（2026-11-09 实测）
                 "extra_body": {"thinking": {"type": "disabled"}}},
    "zhipu": {"entry": "ChatGLMVLLM", "label": "智谱 GLM", "note": "GLM-4V-Flash 免费，注册智谱开放平台拿 Key",
              "model_name": "glm-4v-flash", "models": ["glm-4v-flash", "glm-4.5v"], "url": "https://open.bigmodel.cn/api/paas/v4/",
              "key_url": "https://bigmodel.cn/usercenter/proj-mgmt/apikeys", "share": "glm"},
    "qwen": {"entry": "QwenVLVLLM", "label": "通义千问 VL", "note": "按量付费，和千问 TTS 共用百炼 Key",
             "model_name": "qwen-vl-plus", "models": ["qwen-vl-plus", "qwen-vl-max", "qwen3-vl-plus"],
             "url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
             "key_url": "https://bailian.console.aliyun.com/?apiKey=1#/api-key", "share": "qwen"},
    "doubao": {"entry": "DoubaoVLLM", "label": "豆包", "note": "火山方舟；模型名以方舟控制台开通的为准",
               "model_name": "doubao-seed-2-0-lite-260215", "models": ["doubao-seed-2-0-lite-260215"],
               "url": "https://ark.cn-beijing.volces.com/api/v3", "key_url": "https://console.volcengine.com/ark", "share": "doubao"},
    "kimi": {"entry": "KimiVLLM", "label": "Kimi", "note": "月之暗面的视觉模型",
             "model_name": "moonshot-v1-8k-vision-preview", "models": ["moonshot-v1-8k-vision-preview", "kimi-latest"],
             "url": "https://api.moonshot.cn/v1", "key_url": "https://platform.moonshot.cn/console/api-keys", "share": "kimi"},
    "stepfun": {"entry": "StepFunVLLM", "label": "阶跃星辰", "note": "阶跃的视觉模型",
                "model_name": "step-1o-turbo-vision", "models": ["step-1o-turbo-vision"], "url": "https://api.stepfun.com/v1",
                "key_url": "https://platform.stepfun.com/interface-key", "share": "stepfun"},
    "siliconflow": {"entry": "SiliconFlowVLLM", "label": "硅基流动", "note": "开源视觉模型，模型名写成「厂商/模型」",
                    "model_name": "Qwen/Qwen2.5-VL-72B-Instruct", "models": ["Qwen/Qwen2.5-VL-72B-Instruct", "Qwen/Qwen2.5-VL-32B-Instruct"],
                    "url": "https://api.siliconflow.cn/v1", "key_url": "https://cloud.siliconflow.cn/account/ak", "share": "siliconflow"},
    "openai": {"entry": "OpenAIVLLM", "label": "OpenAI", "note": "国内可能需要代理",
               "model_name": "gpt-5-mini", "models": ["gpt-5-mini", "gpt-5"], "url": "https://api.openai.com/v1",
               "key_url": "https://platform.openai.com/api-keys", "share": "openai"},
    "gemini": {"entry": "GeminiVLLM", "label": "Gemini", "note": "国内可能需要代理",
               "model_name": "gemini-2.5-flash", "models": ["gemini-2.5-flash", "gemini-2.5-pro"],
               "url": "https://generativelanguage.googleapis.com/v1beta/openai/", "key_url": "https://aistudio.google.com/apikey",
               "share": "gemini"},
    "claude": {"entry": "ClaudeVLLM", "label": "Claude", "note": "国内可能需要代理",
               "model_name": "claude-haiku-5-5", "models": ["claude-haiku-5-5", "claude-sonnet-5-5"],
               "url": "https://api.anthropic.com/v1/", "key_url": "https://console.anthropic.com/settings/keys", "share": "claude"},
    "ollama": {"entry": "OllamaVLLM", "label": "Ollama", "note": "本机或局域网的 Ollama，用能看图的模型（如 qwen2.5vl、gemma3）",
               "model_name": "qwen2.5vl:7b", "models": ["qwen2.5vl:7b", "gemma3:4b", "llava:7b"], "url": "http://127.0.0.1:11434/v1",
               "key_url": "", "share": "ollama", "local": True},
    "custom": {"entry": "CustomVLLM", "label": "自定义", "note": "其他 OpenAI 兼容接口，例如 LM Studio、中转站",
               "model_name": "", "models": [], "url": "", "key_url": "", "local": True},
}
ENTRY_TO_PROVIDER = {p["entry"]: k for k, p in PROVIDERS.items()}

PROMPT = ("用户用手机拍了这张照片，问：「{q}」。请用中文回答：先直接回答问题（比如这是什么），"
          "再补一两句最有用的信息（能不能吃、怎么用、要注意什么）。不超过 120 字，不要用 Markdown。")

_lock = threading.Lock()
_sessions = {}   # token -> dict


class PhotoError(Exception):
    pass


def _placeholder(value) -> bool:
    return value is None or str(value).strip() == "" or "你" in str(value)


# ---------- 看图模型设置 ----------

def _shared_key(merged: dict, p: dict):
    """这一家在「大模型」页填过的 Key；没有返回 None。"""
    from core.admin import catalog

    vendor = catalog.VENDOR_BY_ID.get(p.get("share") or "")
    if not vendor:
        return None
    key = ((merged.get("LLM") or {}).get(vendor["entry"]) or {}).get("api_key")
    return None if _placeholder(key) else str(key)


def _url_of(entry: dict, p: dict) -> str:
    return str(entry.get("base_url") or entry.get("url") or p["url"])


def vision_settings(merged: dict) -> dict:
    """后台显示用：选的是哪个、各自有没有 Key（自己的或「大模型」页共用的；不返回 Key 本身）。"""
    vllm = merged.get("VLLM") or {}
    selected = ENTRY_TO_PROVIDER.get((merged.get("selected_module") or {}).get("VLLM") or "")
    out = []
    for pid, p in PROVIDERS.items():
        entry = vllm.get(p["entry"]) or {}
        model = entry.get("model_name") or p["model_name"]
        if pid == "qwen" and model == "qwen3.5-flash":   # 上游配置里的默认值，保存时换成看图用的 qwen-vl-plus
            model = p["model_name"]
        own = not _placeholder(entry.get("api_key"))
        shared = not own and _shared_key(merged, p) is not None
        out.append({"id": pid, "label": p["label"], "note": p["note"], "key_url": p["key_url"], "local": bool(p.get("local")),
                    "own_key": own, "shared_key": shared, "configured": own or shared or bool(p.get("local")),
                    "model": model, "models": p["models"], "url": _url_of(entry, p)})
    ready = bool(selected) and next(o for o in out if o["id"] == selected)["configured"]
    if selected == "custom":
        ready = ready and bool(next(o for o in out if o["id"] == "custom")["url"])
    return {"selected": selected, "ready": ready, "providers": out}


def save_vision(provider: str, api_key: str, path=None, model: str = None, url: str = None) -> list:
    """选用 provider；api_key 非空时一起保存（留空：用已存的，或「大模型」页这一家的 Key）。返回改了哪些项。"""
    from urllib.parse import urlparse

    from ruamel.yaml.comments import CommentedMap

    from core.admin import config_store

    p = PROVIDERS.get(provider)
    if p is None:
        raise KeyError(provider)
    key = str(api_key or "").strip()
    if any(c.isspace() for c in key):
        raise ValueError("API Key 里不能有空格或换行")
    model = str(model or "").strip()
    if any(c.isspace() for c in model) or len(model) > 120:
        raise ValueError("模型名里不能有空格")
    url = str(url or "").strip()
    if url and (urlparse(url).scheme not in ("http", "https") or not urlparse(url).netloc):
        raise ValueError("接口地址要以 http:// 或 https:// 开头")
    merged = config_store.load_merged(custom_path=path) if path else config_store.load_merged()
    entry = (merged.get("VLLM") or {}).get(p["entry"]) or {}
    if not key and _placeholder(entry.get("api_key")) and not p.get("local") and _shared_key(merged, p) is None:
        raise ValueError(f"请填写{p['label']}的 API Key")
    final_model = model or entry.get("model_name") or p["model_name"]
    if provider == "qwen" and final_model == "qwen3.5-flash":
        final_model = p["model_name"]
    if not final_model:
        raise ValueError("请填写模型名")
    final_url = url or _url_of(entry, p)
    if not final_url:
        raise ValueError("请填写接口地址")
    changed = []
    with config_store.edit_custom(path) as data:
        target = data.setdefault("VLLM", CommentedMap()).setdefault(p["entry"], CommentedMap())
        if target.get("model_name") != final_model:
            target["model_name"] = final_model
            changed.append("model_name")
        if _url_of(entry, p) != final_url or not entry.get("url"):
            target["url"] = final_url
            if entry.get("base_url"):
                target["base_url"] = final_url
            changed.append("url")
        target["type"] = "openai"
        if key and key != str(entry.get("api_key") or ""):
            target["api_key"] = key
            changed.append("api_key")
        sel = data.setdefault("selected_module", CommentedMap())
        if sel.get("VLLM") != p["entry"]:
            sel["VLLM"] = p["entry"]
            changed.append("selected")
    return changed


def _vision_entry(merged: dict):
    """(条目名, 条目, Key)。没配好抛 PhotoError。"""
    name = (merged.get("selected_module") or {}).get("VLLM") or ""
    entry = (merged.get("VLLM") or {}).get(name) or {}
    pid = ENTRY_TO_PROVIDER.get(name)
    if pid is None:
        raise PhotoError("还没配置看图模型，在网关后台「大模型 → 看图模型」里选一个、填上 Key")
    p = PROVIDERS[pid]
    key = None if _placeholder(entry.get("api_key")) else str(entry["api_key"])
    key = key or _shared_key(merged, p) or ("local" if p.get("local") else None)
    if key is None:
        raise PhotoError("还没配置看图模型，在网关后台「大模型 → 看图模型」里选一个、填上 Key")
    return name, entry, key


def ask_vision(merged: dict, question: str, jpeg: bytes, device: str = None) -> str:
    """同步：把照片和问题交给选好的看图模型，返回它的回答。失败抛 PhotoError（写明原因）。"""
    return ask_vision_images(merged, PROMPT.format(q=question), [jpeg], device)


def ask_vision_images(merged: dict, prompt: str, images: list, device: str = None, purpose: str = "vision") -> str:
    """同步：一段提示词和几张图（按顺序）交给选好的看图模型，返回它的回答。学习陪伴一次看 3 张连拍。"""
    import openai

    from core import billing

    name, entry, key = _vision_entry(merged)
    p = PROVIDERS[ENTRY_TO_PROVIDER[name]]
    model = entry.get("model_name") or p["model_name"]
    client = openai.OpenAI(api_key=key, base_url=_url_of(entry, p), timeout=VISION_TIMEOUT, max_retries=1)
    content = [{"type": "text", "text": prompt}]
    for jpeg in images:
        mime = "image/png" if jpeg[:4] == b"\x89PNG" else "image/jpeg"   # 后台「试一下」发的是 PNG
        content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(jpeg).decode()}"}})
    try:
        # max_tokens 给够：会思考的看图模型（GLM-4.5V 等）思考也算在里面，太少时回答是空的
        resp = client.chat.completions.create(model=model, stream=False, max_tokens=1000, extra_body=p.get("extra_body"),
                                              messages=[{"role": "user", "content": content}])
    except openai.AuthenticationError:
        raise PhotoError("看图模型的 API Key 不对，到网关后台「大模型 → 看图模型」重新填")
    except openai.APITimeoutError:
        raise PhotoError("看图模型太久没回应，稍后再试")
    except openai.APIConnectionError:
        raise PhotoError("连不上看图模型的接口，检查一下接口地址和网络")
    except openai.APIError as e:
        raise PhotoError(f"看图模型出错：{getattr(e, 'message', None) or e}")
    text = (resp.choices[0].message.content or "").strip()
    usage = getattr(resp, "usage", None)
    if usage:
        billing.record(model, {"prompt": usage.prompt_tokens, "completion": usage.completion_tokens},
                       purpose=purpose, entry=name, device=device)
    if not text:
        raise PhotoError("看图模型没给出回答")
    return text


# ---------- 会话 ----------

def _photos_dir(device: str = "") -> str:
    base = os.path.join(get_project_dir(), "data", "photos")
    return os.path.join(base, re.sub(r"[^0-9A-Za-z_-]", "_", device)) if device else base


def _tidy(now: float):
    for token in [t for t, s in _sessions.items() if now - s["created"] > UPLOAD_SECONDS + KEEP_SECONDS]:
        _sessions.pop(token, None)


def purge_old_photos(now: float = None):
    now = now or time.time()
    base = _photos_dir()
    if not os.path.isdir(base):
        return
    for root, _dirs, files in os.walk(base):
        for f in files:
            p = os.path.join(root, f)
            try:
                if now - os.path.getmtime(p) > PHOTO_DAYS * 86400:
                    os.unlink(p)
            except OSError:
                pass


def start(device: str, question: str, purpose: str = "ask") -> dict:
    if not device:
        raise ValueError("缺少 device")
    if purpose not in ("ask", "avatar", "album"):
        raise ValueError("purpose 只能是 ask、avatar 或 album")
    question = re.sub(r"\s+", " ", str(question or "")).strip()[:QUESTION_MAX] or "这是什么"
    now = time.time()
    token = secrets.token_urlsafe(16)
    with _lock:
        _tidy(now)
        # 一台设备同时只有一个在等的
        for s in _sessions.values():
            if s["device"] == device and s["state"] == "waiting":
                s["state"], s["error"] = "cancelled", "已取消"
        _sessions[token] = {"device": device, "question": question, "created": now, "state": "waiting",
                            "answer": None, "error": None, "photo": None, "purpose": purpose, "avatar": None,
                            "album": [], "poses": []}
    purge_old_photos(now)
    return {"token": token, "question": question, "expires": now + UPLOAD_SECONDS}


def get(token: str):
    with _lock:
        s = _sessions.get(token or "")
        if s and s["state"] == "waiting" and time.time() - s["created"] > UPLOAD_SECONDS:
            s["state"], s["error"] = "expired", "二维码过期了，在小麦上重新说一次"
        return dict(s) if s else None


def view(token: str):
    s = get(token)
    if not s:
        return None
    return {"state": s["state"], "question": s["question"], "answer": s["answer"], "error": s["error"],
            "expires": s["created"] + UPLOAD_SECONDS, "has_photo": bool(s["photo"]), "purpose": s.get("purpose", "ask"),
            "has_avatar": bool(s.get("avatar")), "album_count": len(s.get("album") or []),
            "poses": len(s.get("avatar_poses") or s.get("poses") or [])}


def cancel(token: str) -> bool:
    with _lock:
        s = _sessions.get(token or "")
        if s and s["state"] in ("waiting", "received", "generated"):
            s["state"], s["error"] = "cancelled", "已取消"
            return True
    return False


def photo_path(token: str):
    s = get(token)
    return s and s["photo"]


def receive(token: str, jpeg: bytes, question: str = None) -> dict:
    """手机传来照片：存下来，状态改成 received。返回会话（调用方接着跑 answer）。"""
    if not jpeg or len(jpeg) > MAX_BYTES:
        raise ValueError("照片太大或是空的")
    if not (jpeg[:3] == b"\xff\xd8\xff" or jpeg[:8] == b"\x89PNG\r\n\x1a\n"):
        raise ValueError("只收 JPEG 或 PNG 照片")
    with _lock:
        s = _sessions.get(token or "")
        if not s:
            raise LookupError("这个链接无效，在小麦上重新说一次")
        more_poses = (s.get("purpose") == "avatar" and s["state"] == "received"
                      and 0 < len(s.get("poses") or []) < AVATAR_POSES)   # 形象：同一个码接着传第 2～4 张
        if (s["state"] != "waiting" and not more_poses) or time.time() - s["created"] > UPLOAD_SECONDS:
            raise LookupError("这个链接已经用过或过期了，在小麦上重新说一次")
        if s.get("purpose") == "album" and len(s["album"]) >= ALBUM_PER_SESSION:
            raise ValueError(f"一次最多传 {ALBUM_PER_SESSION} 张，在小麦上再扫一次码接着传")
        q = re.sub(r"\s+", " ", str(question or "")).strip()[:QUESTION_MAX]
        if q:
            s["question"] = q
        folder = _photos_dir(s["device"])
        os.makedirs(folder, exist_ok=True)
        ext = "png" if jpeg[:8] == b"\x89PNG\r\n\x1a\n" else "jpg"   # 去掉白底的公仔图是带透明的 PNG
        seq = len(s.get("poses") or []) + len(s.get("album") or [])   # 同一毫秒收到好几张也不会互相覆盖
        path = os.path.join(folder, f"{int(time.time() * 1000)}-{token[:8]}-{seq}.{ext}")
        with open(path, "wb") as f:
            f.write(jpeg)
        if s.get("purpose") == "album":   # 相册：一个码可以传好几张，一直收到过期
            s["album"].append(path)
            return dict(s)
        if s.get("purpose") == "avatar":
            s.setdefault("poses", []).append(path)
            s["photo"], s["state"] = s["poses"][0], "received"
            return dict(s)
        s["photo"], s["state"] = path, "received"
        return dict(s)


def album_path(token: str, index: int):
    s = get(token)
    album = (s or {}).get("album") or []
    return album[index] if 0 <= index < len(album) else None


def answer(token: str, merged: dict):
    """同步（在线程池里跑）：问看图模型，把结果写回会话。"""
    s = get(token)
    if not s or s["state"] != "received" or s.get("purpose") == "avatar":
        return
    try:
        with open(s["photo"], "rb") as f:
            text = ask_vision(merged, s["question"], f.read(), s["device"])
        state, err = "done", None
    except PhotoError as e:
        text, state, err = None, "error", str(e)
    except Exception as e:  # 网络等
        logger.bind(tag=TAG).warning(f"看图失败: {e}")
        text, state, err = None, "error", "看图模型连不上，稍后再试"
    with _lock:
        cur = _sessions.get(token)
        if cur and cur["state"] == "received":
            cur["answer"], cur["state"], cur["error"] = text, state, err
    logger.bind(tag=TAG).info(f"拍照问答「{s['question']}」: {state} {(text or err or '')[:60]}")


def clear(device: str):
    """清除聊天记录时：删这台设备的照片和会话。"""
    with _lock:
        for t in [t for t, s in _sessions.items() if s["device"] == device]:
            _sessions.pop(t, None)
    shutil.rmtree(_photos_dir(device), ignore_errors=True)


# ---------- 卡通形象（purpose="avatar"） ----------

WANX_MODEL = "wanx-style-repaint-v1"
WANX_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/image-generation/generation"
WANX_TASK_URL = "https://dashscope.aliyuncs.com/api/v1/tasks/{}"
# 0「原图」：不经过 AI、不用 Key，照片原样当形象（拍孩子自己的公仔，设计画布第 26 块）；其余是通义万相的画风
ORIGINAL_STYLE = 0
AVATAR_POSES = 5   # 「原图」形象最多几张：待机、在听、在想、在说、睡觉（睡眠时段），按上传顺序
AVATAR_STYLES = {ORIGINAL_STYLE: "原图", 1: "3D童话", 2: "二次元", 7: "炫彩卡通", 3: "小清新", 30: "童话世界", 31: "黏土世界"}
AVATAR_SECONDS = 90


def dashscope_key(merged: dict):
    """百炼 Key：千问 VL（看图模型）或千问 TTS 里填过的那个。"""
    for section, name in (("VLLM", "QwenVLVLLM"), ("TTS", "Qwen3TTS")):
        key = ((merged.get(section) or {}).get(name) or {}).get("api_key")
        if not _placeholder(key):
            return str(key).strip()
    return None


def avatar_path(token: str, pose: int = 0):
    s = get(token)
    if not s:
        return None
    poses = s.get("avatar_poses") or ([s["avatar"]] if s.get("avatar") else [])
    return poses[pose] if 0 <= pose < len(poses) else None


def _wanx(key: str, jpeg: bytes, style: int) -> bytes:
    """同步：提交人像风格重绘，轮询到完成，下载结果图。失败抛 PhotoError。"""
    import requests

    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json", "X-DashScope-Async": "enable"}
    image = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()
    try:
        r = requests.post(WANX_URL, headers=headers, timeout=30,
                          json={"model": WANX_MODEL, "input": {"image_url": image, "style_index": style}})
        data = r.json()
        if r.status_code == 401:
            raise PhotoError("百炼 Key 不对，到网关后台重新填")
        task = (data.get("output") or {}).get("task_id")
        if not task:
            raise PhotoError(f"通义万相没接这个任务：{data.get('message') or data.get('code') or r.status_code}")
        deadline = time.time() + AVATAR_SECONDS
        while time.time() < deadline:
            time.sleep(3)
            out = requests.get(WANX_TASK_URL.format(task), headers={"Authorization": f"Bearer {key}"}, timeout=20).json()
            out = out.get("output") or {}
            status = out.get("task_status")
            if status == "SUCCEEDED":
                url = next((x.get("url") for x in out.get("results") or [] if x.get("url")), None)
                if not url:
                    raise PhotoError("通义万相没画出来，换一张正脸清楚的照片试试")
                img = requests.get(url, timeout=30)
                img.raise_for_status()
                return img.content
            if status in ("FAILED", "CANCELED", "UNKNOWN"):
                msg = out.get("message") or next((x.get("message") for x in out.get("results") or [] if x.get("message")), "")
                raise PhotoError(f"没画成：{msg or status}（照片里要有清楚的正脸）")
        raise PhotoError("通义万相太久没画完，稍后再试")
    except requests.RequestException as e:
        raise PhotoError(f"连不上通义万相：{type(e).__name__}")


def generate_avatar(token: str, style: int, merged: dict):
    """同步（在线程池里跑）：把收到的照片画成卡通形象，存成 PNG/JPEG 文件，状态改成 generated。"""
    s = get(token)
    if not s or s.get("purpose") != "avatar" or not s.get("photo"):
        return
    try:
        poses = None
        if style == ORIGINAL_STYLE:   # 原样用：手机页面上传前已经缩过（去白底的是 PNG）；几张都留着
            poses = list(s.get("poses") or [s["photo"]])
            with open(poses[0], "rb") as f:
                img = f.read()
        else:
            key = dashscope_key(merged)
            if not key:
                raise PhotoError("要百炼 Key 才能生成形象：在网关后台「语音合成 → 千问 TTS」或「大模型 → 看图模型 → 千问 VL」里填；"
                                 "不想用 Key 就选「原图」")
            with open(s["photo"], "rb") as f:
                img = _wanx(key, f.read(), style)
        path = s["photo"].rsplit(".", 1)[0] + f"-avatar{style}.img"
        with open(path, "wb") as f:
            f.write(img)
        state, err = "generated", None
    except PhotoError as e:
        path, state, err = None, "error", str(e)
    except Exception as e:
        logger.bind(tag=TAG).warning(f"生成形象失败: {e}")
        path, state, err = None, "error", "生成形象失败，稍后再试"
    with _lock:
        cur = _sessions.get(token)
        if cur and cur["state"] == "generating":
            cur["avatar"], cur["state"], cur["error"] = path, state, err
            cur["avatar_poses"] = poses if state == "generated" and poses else ([path] if path else [])
            if state == "error":
                cur["state"] = "received"   # 还能换个画风再试
                cur["error"] = err
    logger.bind(tag=TAG).info(f"生成形象（画风 {style}）: {state} {err or ''}")


def begin_avatar(token: str, style: int):
    """设备选好画风：状态改成 generating（调用方接着在线程池里跑 generate_avatar）。"""
    if style not in AVATAR_STYLES:
        raise ValueError("没有这个画风")
    with _lock:
        s = _sessions.get(token or "")
        if not s or s.get("purpose") != "avatar":
            raise LookupError("没有这个形象任务")
        if s["state"] not in ("received", "generated"):
            raise ValueError("还没收到照片" if s["state"] == "waiting" else "正在画，等一下")
        s["state"], s["error"], s["avatar"] = "generating", None, None
