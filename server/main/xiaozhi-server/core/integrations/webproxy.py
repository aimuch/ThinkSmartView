"""[xiaozhi-hub] 设备上看机柜（rack-screen）和智能中枢自己的网页（设计画布第 62 块）。

设备「家居」里的「机柜」「智能中枢」两页直接放它们原来的网页：功能、主题和电脑上一样，它们本来就按屏幕宽窄排版。
那两个网页不让别人嵌（X-Frame-Options: DENY）、要登录（rack-screen 的会话 cookie 是 SameSite=Strict），所以：

  设备页面 → 设备本机的小代理（127.0.0.1:8091 / 8092，路径不变）→ 已有的 SSH 隧道 → 这里 /kiosk/proxy/<app>/<路径>
  → 带上后台插件里填的令牌（rack-screen: X-Token；智能中枢: Authorization: Bearer）转给它们。

令牌不出网关；只有经隧道来的设备（本机地址、没有 X-Forwarded-For）能用，而且这台设备要开着对应的插件。
回来的时候去掉 X-Frame-Options、CSP 的 frame-ancestors 和 Set-Cookie；gzip 和 SSE（智能中枢的 /api/stream）原样流过去。
为了安全，经设备做不了的：登录退出、改密码、备份的导出 / 恢复 / 重置 / 导入（里面有密钥），智能中枢的全局设置、
固件上传和升级、节点的 Wi‑Fi 和身份（DENY）。
"""
import re

from aiohttp import ClientSession, ClientTimeout, web

APPS = {
    "rack": {"plugin": "rack_status", "label": "机柜",
             "no_token": "网关后台「外部服务 → 家里的系统」的「机柜」里还没填 rack-screen 的登录密码（打开机柜网页时输的那个，没有用户名）。"
                         "填好后点右上角的刷新。"},
    "hub": {"plugin": "smart_hub", "label": "智能中枢",
            "no_token": "网关后台「外部服务 → 家里的系统」的「智能中枢」里还没填访问令牌（网关那台机器上 ~/bluetooth-router/.env 里的 BTROUTER_API_TOKEN）。"
                        "填好后点右上角的刷新。"},
}
DENY = {
    "rack": [("POST", r"/api/(password|setup-password|login|logout)"), ("*", r"/api/backup(/.*)?"),
             ("POST", r"/api/inventory/reset")],
    "hub": [("POST", r"/api/(login|logout|restore)"), ("PUT", r"/api/settings"), ("GET", r"/api/backup"),
            ("*", r"/api/firmware(/.*)?"), ("*", r"/firmware/.*"), ("POST", r"/api/nodes/[^/]+/ota"),
            ("PUT", r"/api/nodes/[^/]+/(wifi|identity)")],
}
DENIED_TEXT = "这个操作请在电脑上做：经设备不能登录、改密码、动备份和固件"
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding",
       "upgrade", "host", "content-length"}
DROP_REQ = HOP | {"cookie", "authorization", "x-token", "origin", "referer", "x-device-id"}
DROP_RESP = HOP | {"x-frame-options", "set-cookie", "content-security-policy", "content-security-policy-report-only"}
MAX_BODY = 10 * 1024 * 1024

_session = {"s": None}


def denied(app: str, method: str, path: str) -> bool:
    for m, pattern in DENY.get(app, ()):
        if (m == "*" or m == method) and re.fullmatch(pattern, path):
            return True
    return False


def auth_headers(app: str, s: dict) -> dict:
    token = s.get("token") or ""
    if not token or (app == "rack" and s.get("password")):   # 机柜填了登录密码就用密码，旧的令牌不管
        return {}
    return {"X-Token": token} if app == "rack" else {"Authorization": f"Bearer {token}"}


FAIL_PAGE = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
             "<body style='margin:0;display:grid;place-items:center;height:100vh;background:#10162e;color:#cfd6ff;"
             "font:20px/1.7 system-ui,sans-serif;text-align:center;padding:40px;box-sizing:border-box'><div>{}</div></body>")


def _fail(request: web.Request, status: int, text: str):
    """打开网页（设备「家居」里那一页）时给一页看得懂的字；网页里的接口请求照旧给 JSON。"""
    if request.method == "GET" and "text/html" in request.headers.get("Accept", ""):
        from html import escape
        return web.Response(status=status, text=FAIL_PAGE.format(escape(text)), content_type="text/html")
    return web.json_response({"error": text}, status=status)


def _local(request: web.Request) -> bool:
    if "X-Forwarded-For" in request.headers or "Forwarded" in request.headers:
        return False
    return request.remote in ("127.0.0.1", "::1")


def _client() -> ClientSession:
    s = _session["s"]
    if s is None or s.closed:
        # 不自动解压：gzip 原样转给设备（Content-Encoding 也原样）；SSE 一直开着，不设总超时
        s = _session["s"] = ClientSession(auto_decompress=False, timeout=ClientTimeout(total=None, connect=5, sock_connect=5))
    return s


def _settings(app: str, device: str):
    """(这个网页的设置, 不能用的原因)。"""
    from core.admin import config_store
    from core.integrations import common

    merged = config_store.load_merged()
    plugin = APPS[app]["plugin"]
    if device and not common.enabled_for(merged, device, plugin):
        return None, f"网关后台没给这台设备打开「{APPS[app]['label']}」插件"
    return common.settings(merged, plugin), None


async def handle(request: web.Request):
    app = request.match_info["app"]
    if app not in APPS:
        raise web.HTTPNotFound()
    if not _local(request):
        return web.json_response({"error": "仅限本机"}, status=403)
    path = "/" + request.match_info.get("tail", "")
    if denied(app, request.method, path):
        return web.json_response({"error": DENIED_TEXT}, status=403)
    s, why = _settings(app, request.headers.get("X-Device-Id", ""))
    if s is None:
        return _fail(request, 404, why + "。在网关后台「外部服务 → 家里的系统」里打开。")
    use_password = app == "rack" and bool(s.get("password"))
    if not s.get("token") and not use_password and request.method == "GET" and "text/html" in request.headers.get("Accept", ""):
        # 没令牌就只能到它的登录页，而经设备登录不了（登录挡掉了、cookie 也不转）：直接说要填什么
        return _fail(request, 409, APPS[app]["no_token"])
    headers = {k: v for k, v in request.headers.items() if k.lower() not in DROP_REQ}
    headers.update(auth_headers(app, s))
    if use_password:   # 机柜只有登录密码：网关替设备登录，带它的 cookie（设计画布第 70 块）
        from core.integrations import common, rack

        try:
            headers["Cookie"] = "rs_session=" + await rack.session(s["url"], s["password"])
        except common.IntegrationError as e:
            return _fail(request, 502, str(e))
    body = await request.read() if request.can_read_body else None
    if body and len(body) > MAX_BODY:
        return web.json_response({"error": "太大了"}, status=413)
    url = s["url"] + path + (("?" + request.query_string) if request.query_string else "")
    try:
        upstream = await _client().request(request.method, url, headers=headers, data=body, allow_redirects=False)
    except Exception as e:  # noqa: BLE001
        return _fail(request, 502, f"连不上{APPS[app]['label']}（{s['url']}）：{type(e).__name__}")
    if use_password and (upstream.status == 401 or (upstream.status in (302, 303) and
                                                   upstream.headers.get("Location", "").startswith("/login"))):
        # cookie 过期了（服务重启、过了 30 天）：重新登录一次再要，不让设备上的页面跳到登录框
        from core.integrations import common, rack

        rack.forget_session(s["url"], s["password"])
        upstream.release()
        try:
            headers["Cookie"] = "rs_session=" + await rack.session(s["url"], s["password"])
            upstream = await _client().request(request.method, url, headers=headers, data=body, allow_redirects=False)
        except common.IntegrationError as e:
            return _fail(request, 502, str(e))
        except Exception as e:  # noqa: BLE001
            return _fail(request, 502, f"连不上{APPS[app]['label']}（{s['url']}）：{type(e).__name__}")
    out = web.StreamResponse(status=upstream.status, reason=upstream.reason)
    for k, v in upstream.headers.items():
        if k.lower() not in DROP_RESP:
            out.headers.add(k, v)
    if upstream.headers.get("Content-Length") and "text/event-stream" not in upstream.headers.get("Content-Type", ""):
        out.content_length = int(upstream.headers["Content-Length"])
    try:
        await out.prepare(request)
        async for chunk in upstream.content.iter_any():
            await out.write(chunk)
        await out.write_eof()
    except (ConnectionResetError, RuntimeError):
        pass                           # 设备那头关了（切走了这一页）：上游的连接一起关
    finally:
        upstream.release()
    return out


def register(app: web.Application):
    app.router.add_route("*", r"/kiosk/proxy/{app:rack|hub}/{tail:.*}", handle)
