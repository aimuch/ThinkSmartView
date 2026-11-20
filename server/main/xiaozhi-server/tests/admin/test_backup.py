"""[xiaozhi-hub] 网关备份（core/admin/backup.py，设计画布第 87 块）。"""

import asyncio
import io
import json
import sqlite3
import tarfile
import time
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from core.admin import backup
from core.admin.admin_handler import AdminHandler


def make_data(root: Path) -> Path:
    data = root / "data"
    (data / "photos" / "dev1").mkdir(parents=True)
    (data / "screens").mkdir()
    (data / ".config.yaml").write_text("password_hash: x\nplugins: {}\n", encoding="utf-8")
    (data / ".config.yaml.bak-asr").write_text("old", encoding="utf-8")
    (data / "voiceprints.json").write_text('{"people": []}', encoding="utf-8")
    (data / "photos" / "dev1" / "a.jpg").write_bytes(b"\xff\xd8jpeg")
    (data / "screens" / "dev1.png").write_bytes(b"png")
    (data / ".admin-sessions-kept.json").write_text('{"now": 1}', encoding="utf-8")
    db = sqlite3.connect(data / "chat.db")
    db.execute("create table t (x text)")
    db.execute("insert into t values ('你好')")
    db.commit()
    db.close()
    return data


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_build_check_extract_round_trip(tmp_path):
    data = make_data(tmp_path)
    live = sqlite3.connect(data / "chat.db")          # 网关正开着这个库、还有没提交的写
    live.execute("insert into t values ('写到一半')")
    out = tmp_path / "b.tar.gz"
    info = backup.build(data, out, version="abc123")
    live.close()
    with tarfile.open(out) as tar:
        names = tar.getnames()
    assert names[0] == "backup.json" and "data/.config.yaml" in names and "data/photos/dev1/a.jpg" in names
    assert "data/screens/dev1.png" not in names and "data/.config.yaml.bak-asr" not in names   # 截图、.bak 不要
    assert info["files"] == len(names) - 1
    m = backup.check(out)
    assert m["app"] == backup.APP and m["version"] == "abc123"
    into = tmp_path / "restored"
    assert backup.extract(out, into) == info["files"]
    rows = sqlite3.connect(into / "chat.db").execute("select x from t").fetchall()
    assert rows == [("你好",)]                          # 拷的是提交过的那份，完整能打开


def tar_with(path: Path, members: dict, manifest=True):
    with tarfile.open(path, "w:gz") as tar:
        items = ({"backup.json": json.dumps({"app": backup.APP, "format": 1})} if manifest else {}) | members
        for name, text in items.items():
            raw = text.encode()
            ti = tarfile.TarInfo(name)
            ti.size = len(raw)
            tar.addfile(ti, io.BytesIO(raw))


def test_check_refuses_strange_archives(tmp_path):
    p = tmp_path / "x.tar.gz"
    tar_with(p, {"data/a.json": "{}"}, manifest=False)
    with pytest.raises(backup.BackupError, match="不是小麦网关的备份"):
        backup.check(p)
    tar_with(p, {"data/../../etc/passwd": "x"})
    with pytest.raises(backup.BackupError, match="不该有的东西"):
        backup.check(p)
    tar_with(p, {"/etc/x": "x"})
    with pytest.raises(backup.BackupError, match="不该有的东西"):
        backup.check(p)
    p.write_bytes(b"not a tar")
    with pytest.raises(backup.BackupError, match="打开"):
        backup.check(p)
    with tarfile.open(p, "w:gz") as tar:              # 链接不收
        raw = json.dumps({"app": backup.APP, "format": 1}).encode()
        ti = tarfile.TarInfo("backup.json")
        ti.size = len(raw)
        tar.addfile(ti, io.BytesIO(raw))
        link = tarfile.TarInfo("data/evil")
        link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
        tar.addfile(link)
    with pytest.raises(backup.BackupError, match="不该有的东西"):
        backup.check(p)


def test_run_local_keeps_n_and_restore_swaps(tmp_path):
    data = make_data(tmp_path)
    restarted = []
    b = backup.Backups(data, version=lambda: "v1", restart=lambda: restarted.append(1))
    b.save_settings({"local_dir": str(tmp_path / "bk"), "keep": 3})

    async def go():
        names = []
        for i in range(5):
            e = await b.run("手动")
            assert e["ok"] and e["where"] == "网关本机"
            names.append(e["name"])                    # 同一秒的加 -2、-3（conftest 把 asyncio.sleep 变快了）
        return names
    names = run(go())
    assert len(set(names)) == 5
    have = sorted(p.name for p in (tmp_path / "bk").iterdir())
    assert set(have) == set(names[-3:])               # 只留最近 3 份
    assert [backup.order(n) for n in names] == sorted(backup.order(n) for n in names)
    assert oct((tmp_path / "bk" / have[0]).stat().st_mode & 0o777) == "0o600"

    # 改了数据，恢复到第一份留下的
    (data / "voiceprints.json").write_text('{"people": ["新的"]}', encoding="utf-8")
    (data / ".admin-sessions-kept.json").write_text('{"now": 2}', encoding="utf-8")
    view = run(b.view())
    assert [x["name"] for x in view["backups"]] == names[-3:][::-1]

    async def restore():
        path = tmp_path / "pick.tar.gz"
        await b.fetch(have[0], path)
        r = await b.restore(path)
        for _ in range(40):                            # call_later(1.5) 重启；asyncio.sleep 在测试里被换快了
            if restarted:
                break
            await asyncio.get_running_loop().run_in_executor(None, time.sleep, 0.1)
        return r
    r = run(restore())
    assert json.loads((data / "voiceprints.json").read_text()) == {"people": []}
    assert json.loads((data / ".admin-sessions-kept.json").read_text()) == {"now": 2}   # 登录用现在的
    assert (data / "screens" / "dev1.png").exists()                                     # 不备份的照样留着
    assert (tmp_path / r["kept"] / "voiceprints.json").read_text() == '{"people": ["新的"]}'
    assert r["before"] and restarted == [1]
    assert b.settings()["keep"] == 3                    # 备份自己的设置不跟着恢复


class FakeDav:
    def __init__(self):
        self.files = {}
        self.folder = False
        self.password = "pw"

    def app(self):
        app = web.Application(client_max_size=50 * 1024 ** 2)
        app.router.add_route("*", "/dav/{name:.*}", self.handle)
        return app

    async def handle(self, request):
        auth = request.headers.get("Authorization", "")
        import base64
        if auth != "Basic " + base64.b64encode(f"andy:{self.password}".encode()).decode():
            return web.Response(status=401)
        name = request.match_info["name"].strip("/")
        if request.method == "MKCOL":
            self.folder = True
            return web.Response(status=201)
        if request.method == "PROPFIND":
            if not self.folder:
                return web.Response(status=404)
            body = "".join(f"<d:response><d:href>/dav/xz/{n}</d:href><d:propstat><d:prop><d:getcontentlength>{len(v)}"
                           f"</d:getcontentlength></d:prop></d:propstat></d:response>" for n, v in self.files.items())
            return web.Response(status=207, text=f'<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">'
                                f'<d:response><d:href>/dav/xz/</d:href></d:response>{body}</d:multistatus>')
        key = name.split("/")[-1]
        if request.method == "PUT":
            self.files[key] = await request.read()
            return web.Response(status=201)
        if request.method == "GET":
            return web.Response(body=self.files[key]) if key in self.files else web.Response(status=404)
        if request.method == "DELETE":
            self.files.pop(key, None)
            return web.Response(status=204)
        return web.Response(status=405)


def test_webdav_upload_list_prune_and_fallback(tmp_path):
    data = make_data(tmp_path)
    dav = FakeDav()

    async def go():
        server = TestServer(dav.app())
        await server.start_server()
        url = str(server.make_url("/dav/xz/"))
        b = backup.Backups(data)
        b.save_settings({"target": "webdav", "webdav_url": url, "webdav_user": "andy", "webdav_password": "pw",
                         "local_dir": str(tmp_path / "bk"), "keep": 3})
        try:
            assert "能写" in await b.target().test() and dav.folder         # 文件夹不在时建一个
            for _ in range(4):
                e = await b.run("定时")
                assert e["ok"] and e["where"] == "NAS"
            assert len(dav.files) == 3
            v = await b.view()
            assert len(v["backups"]) == 3 and all(x["where"] == "webdav" for x in v["backups"])
            assert v["settings"]["webdav_password_set"] and "webdav_password" not in v["settings"]
            name = v["backups"][0]["name"]
            await b.fetch(name, tmp_path / "got.tar.gz")
            assert backup.check(tmp_path / "got.tar.gz")["app"] == backup.APP
            dav.password = "changed"                                         # NAS 改了密码：先放本机
            e = await b.run("定时")
            assert not e["ok"] and "密码不对" in e["error"] and "网关本机" in e["where"]
            assert list((tmp_path / "bk").iterdir())
            assert b.attention()[0]["text"] == "网关备份没成功"
        finally:
            await server.close()
    run(go())


def test_settings_validation_and_schedule(tmp_path):
    b = backup.Backups(make_data(tmp_path))
    with pytest.raises(backup.BackupError, match="03:30"):
        b.save_settings({"time": "3点"})
    with pytest.raises(backup.BackupError, match="3、7、14、30"):
        b.save_settings({"keep": 5})
    with pytest.raises(backup.BackupError, match="http"):
        b.save_settings({"target": "webdav", "webdav_url": "nas:5005"})
    b.save_settings({"time": "03:30", "webdav_password": "secret"})
    assert b.public_settings()["webdav_password_set"]
    b.save_settings({"webdav_password": ""})                                 # 留空是不改
    assert b.settings()["webdav_password"] == "secret"
    assert oct(b.state_path.stat().st_mode & 0o777) == "0o600"
    night = time.mktime(time.strptime("2026-11-20 03:00", "%Y-%m-%d %H:%M"))
    assert not b.due(night)
    assert b.due(night + 3600)
    b.mark_scheduled(night + 3600)
    assert not b.due(night + 7200)                                           # 今天做过了
    assert b.due(night + 86400 + 3600)
    b.save_settings({"enabled": False})
    assert not b.due(night + 86400 + 3600)
    assert b.attention() == []                                               # 没做过的不报


def test_attention_when_stale(tmp_path):
    b = backup.Backups(make_data(tmp_path))
    b._record({"at": int(time.time()) - 5 * 86400, "ok": True, "name": "xiaozhi-20261115-033000.tar.gz"})
    a = b.attention()
    assert a and a[0]["level"] == "warn" and "5 天没备份" in a[0]["text"]


@pytest.fixture
async def admin(tmp_path):
    cfg = tmp_path / "data" / ".config.yaml"
    make_data(tmp_path)
    app = web.Application()
    h = AdminHandler({"selected_module": {}}, config_path=cfg)
    h.backups.restart = lambda: None
    h.backups.save_settings({"local_dir": str(tmp_path / "bk")})
    h.register(app)
    async with TestClient(TestServer(app)) as c:
        yield c, h


async def test_backup_api(admin, tmp_path):
    c, h = admin
    assert (await c.get("/admin/api/backup")).status == 401
    assert (await c.post("/admin/api/backup/run", json={})).status == 401
    assert (await c.get("/admin/api/backup/download")).status == 401
    assert (await c.post("/admin/api/backup/restore?name=x")).status == 401
    (tmp_path / "data" / ".config.yaml").write_text("{}\n", encoding="utf-8")
    await c.post("/admin/api/setup", json={"password": "pass-1234"})
    d = await (await c.post("/admin/api/backup/run", json={})).json()
    assert d["ok"] and d["entry"]["where"] == "网关本机"
    v = await (await c.get("/admin/api/backup")).json()
    assert v["backups"][0]["name"] == d["entry"]["name"] and v["included"]["files"] >= 4
    r = await c.get("/admin/api/backup/download")
    assert r.status == 200 and "attachment" in r.headers["Content-Disposition"]
    raw = await r.read()
    (tmp_path / "dl.tar.gz").write_bytes(raw)
    assert backup.check(tmp_path / "dl.tar.gz")["app"] == backup.APP
    r = await c.get("/admin/api/backup/download?name=" + d["entry"]["name"])
    assert r.status == 200
    assert (await c.get("/admin/api/backup/download?name=../../etc")).status == 404
    bad = await c.post("/admin/api/backup/restore", data=b"junk")
    assert bad.status == 400 and "打开" in (await bad.json())["error"]
    t = await (await c.post("/admin/api/backup/test", json={"target": "local", "local_dir": str(tmp_path / "x")})).json()
    assert t["ok"] and "能写" in t["message"]
    s = await (await c.post("/admin/api/backup/settings", json={"keep": 14, "time": "04:10"})).json()
    assert s["settings"]["keep"] == 14
    ok = await c.post("/admin/api/backup/restore", data=raw)
    assert ok.status == 200 and (await ok.json())["restarting"]
