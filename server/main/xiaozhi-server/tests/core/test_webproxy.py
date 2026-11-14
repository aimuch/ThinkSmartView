"""设备上看机柜、智能中枢自己的网页（设计画布第 62 块）：令牌由网关带上、去掉不让嵌的头和 cookie、gzip 和 SSE 原样流过、危险操作挡掉。"""
import gzip

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from core.integrations import webproxy


@pytest.fixture
async def upstream():
    seen = []

    async def page(request):
        seen.append(dict(request.headers))
        return web.Response(text="<html>setup</html>", content_type="text/html",
                            headers={"X-Frame-Options": "DENY", "Set-Cookie": "rs_session=x", "Content-Security-Policy": "frame-ancestors 'none'",
                                     "X-Kept": "1"})

    async def js(request):
        body = gzip.compress(b"console.log(1)")
        return web.Response(body=body, headers={"Content-Encoding": "gzip", "Content-Type": "application/javascript"})

    async def stream(request):
        r = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await r.prepare(request)
        for i in range(3):
            await r.write(f"data: {i}\n\n".encode())
        return r

    async def echo(request):
        seen.append(dict(request.headers))
        return web.json_response({"method": request.method, "body": (await request.text()), "q": request.query_string})

    async def login(request):
        return web.HTTPFound("/setup")

    app = web.Application()
    app.router.add_get("/setup", page)
    app.router.add_get("/static/app.js", js)
    app.router.add_get("/api/stream", stream)
    app.router.add_route("*", "/api/{x:.*}", echo)
    app.router.add_get("/login", login)
    async with TestClient(TestServer(app)) as c:
        c.seen = seen
        yield c


@pytest.fixture
async def gateway(upstream, monkeypatch):
    base = str(upstream.make_url("")).rstrip("/")
    state = {"why": None}

    def settings(app, device):
        if state["why"]:
            return None, state["why"]
        return {"url": base, "token": "tok-123"}, None
    monkeypatch.setattr(webproxy, "_settings", settings)
    webproxy._session["s"] = None
    app = web.Application()
    webproxy.register(app)
    async with TestClient(TestServer(app)) as c:
        c.state = state
        yield c
    if webproxy._session["s"]:
        await webproxy._session["s"].close()


async def test_page_comes_through_without_frame_blocking_and_with_the_token(gateway, upstream):
    r = await gateway.get("/kiosk/proxy/rack/setup", headers={"Cookie": "a=b", "X-Device-Id": "dev-1"})
    assert r.status == 200 and await r.text() == "<html>setup</html>"
    assert "X-Frame-Options" not in r.headers and "Set-Cookie" not in r.headers and "Content-Security-Policy" not in r.headers
    assert r.headers["X-Kept"] == "1"
    sent = upstream.seen[-1]
    assert sent["X-Token"] == "tok-123" and "Cookie" not in sent and "X-Device-Id" not in sent


async def test_hub_gets_a_bearer_token_and_writes_pass(gateway, upstream):
    r = await gateway.post("/kiosk/proxy/hub/api/devices/lamp/on?x=1", data="{}", headers={"Authorization": "Bearer nope"})
    assert (await r.json()) == {"method": "POST", "body": "{}", "q": "x=1"}
    assert upstream.seen[-1]["Authorization"] == "Bearer tok-123"


async def test_gzip_and_sse_flow_through(gateway):
    r = await gateway.get("/kiosk/proxy/hub/static/app.js", auto_decompress=False)
    assert r.headers["Content-Encoding"] == "gzip" and gzip.decompress(await r.read()) == b"console.log(1)"
    r = await gateway.get("/kiosk/proxy/hub/api/stream")
    assert r.headers["Content-Type"].startswith("text/event-stream") and await r.text() == "data: 0\n\ndata: 1\n\ndata: 2\n\n"


async def test_redirects_are_passed_on_not_followed(gateway):
    r = await gateway.get("/kiosk/proxy/rack/login", allow_redirects=False)
    assert r.status == 302 and r.headers["Location"] == "/setup"


@pytest.mark.parametrize("app,method,path", [
    ("rack", "POST", "/api/password"), ("rack", "GET", "/api/backup/export"), ("rack", "POST", "/api/backup/restore"),
    ("rack", "POST", "/api/logout"), ("hub", "PUT", "/api/settings"), ("hub", "POST", "/api/restore"),
    ("hub", "POST", "/api/firmware"), ("hub", "POST", "/api/nodes/n1/ota"), ("hub", "PUT", "/api/nodes/n1/wifi"),
    ("hub", "GET", "/api/backup")])
async def test_dangerous_calls_are_refused(gateway, app, method, path):
    r = await gateway.request(method, f"/kiosk/proxy/{app}{path}")
    assert r.status == 403 and "电脑上" in (await r.json())["error"]


def test_deny_rules_leave_normal_use_alone():
    assert not webproxy.denied("rack", "GET", "/api/status")
    assert not webproxy.denied("rack", "POST", "/api/layout")
    assert not webproxy.denied("hub", "GET", "/api/settings")
    assert not webproxy.denied("hub", "PUT", "/api/nodes/n1/power")


async def test_off_for_this_device_and_unreachable(gateway, monkeypatch):
    gateway.state["why"] = "网关后台没给这台设备打开「机柜」插件"
    r = await gateway.get("/kiosk/proxy/rack/setup")
    assert r.status == 404 and "插件" in (await r.json())["error"]
    gateway.state["why"] = None
    monkeypatch.setattr(webproxy, "_settings", lambda app, device: ({"url": "http://127.0.0.1:9", "token": ""}, None))
    r = await gateway.get("/kiosk/proxy/rack/setup")
    assert r.status == 502 and "连不上机柜" in (await r.json())["error"]
    r = await gateway.get("/kiosk/proxy/rack/setup", headers={"Accept": "text/html,*/*"})    # 打开网页：一页看得懂的字
    assert r.status == 502 and r.content_type == "text/html" and "连不上机柜" in await r.text()


async def test_only_from_this_machine(gateway):
    r = await gateway.get("/kiosk/proxy/rack/setup", headers={"X-Forwarded-For": "10.0.0.9"})
    assert r.status == 403
    assert (await gateway.get("/kiosk/proxy/nope/x")).status == 404
