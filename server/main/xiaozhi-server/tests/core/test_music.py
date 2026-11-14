"""[xiaozhi-hub] 音乐（设计画布第 58 块）：Navidrome 用本机起的假 Subsonic 服务测，不联网。

覆盖：token + salt 认证（密码不存）、找歌（搜不到时模糊比全部歌名）、按标签挑、随便放、本机 music 文件夹、
后台的保存/测试/浏览/上传、设备的 /kiosk/music/* 中转（带 Range）、外部服务页的状态、常用口令。
"""
import hashlib
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from core.admin.admin_handler import AdminHandler
from core.agent import commands as cm
from core.agent import music, services

USER, PASSWORD = "family", "s3cret-pw"
AUDIO = bytes(range(256)) * 40          # 10 KB 的「歌」
SONGS = [
    {"id": "s1", "title": "两只老虎 (儿歌版)", "artist": "宝宝巴士", "album": "儿歌精选", "duration": 95, "coverArt": "al-1", "parent": "d-kids"},
    {"id": "s2", "title": "小星星", "artist": "宝宝巴士", "album": "儿歌精选", "duration": 80, "coverArt": "al-1", "parent": "d-kids"},
    {"id": "s3", "title": "01.小兔子乖乖", "artist": "宝宝巴士", "album": "儿歌精选", "duration": 70, "coverArt": "al-1", "parent": "d-kids"},
    {"id": "s4", "title": "三只小猪", "artist": "凯叔", "album": "睡前故事", "duration": 600, "coverArt": "al-2", "parent": "d-story"},
    {"id": "s5", "title": "晴天", "artist": "周杰伦", "album": "叶惠美", "duration": 269, "coverArt": "al-3", "parent": "d-jay"},
]


def fake_subsonic(calls):
    """最小的 Subsonic：认 token + salt，Navidrome 的搜索只认词开头（「老虎」搜不到「两只老虎」）。"""
    def ok(**body):
        return web.json_response({"subsonic-response": {"status": "ok", "version": "1.16.1", "type": "navidrome",
                                                        "serverVersion": "0.53.3", **body}})

    def check(q):
        return q.get("u") == USER and q.get("t") == hashlib.md5((PASSWORD + q.get("s", "")).encode()).hexdigest()

    async def rest(request):
        q = request.query
        method = request.match_info["method"]
        calls.append((method, dict(q)))
        assert "p" not in q                                    # 不传明文密码
        if not check(q):
            return web.json_response({"subsonic-response": {"status": "failed", "error": {"code": 40, "message": "Wrong username or password"}}})
        if method == "ping":
            return ok()
        if method == "getMusicFolders":
            return ok(musicFolders={"musicFolder": [{"id": 1, "name": "NAS 音乐"}]})
        if method == "search3":
            query = q.get("query", "")
            if not query:
                off, n = int(q.get("songOffset", 0)), int(q.get("songCount", 20))
                return ok(searchResult3={"song": SONGS[off:off + n]})
            hit = [s for s in SONGS if any(w.startswith(query) for w in music.norm(s["title"]).split())]
            albums = [{"id": "al-1", "name": "儿歌精选", "artist": "宝宝巴士", "songCount": 3, "coverArt": "al-1"}] if query.startswith("儿歌") else []
            artists = [{"id": "ar-jay", "name": "周杰伦", "albumCount": 1}] if query.startswith("周杰伦") else []
            return ok(searchResult3={"song": hit, "album": albums, "artist": artists})
        if method == "getAlbumList2":
            return ok(albumList2={"album": [{"id": "al-1", "name": "儿歌精选", "artist": "宝宝巴士", "songCount": 3, "coverArt": "al-1"},
                                            {"id": "al-2", "name": "睡前故事", "artist": "凯叔", "songCount": 1, "coverArt": "al-2"}]})
        if method == "getAlbum":
            songs = [s for s in SONGS if {"al-1": "儿歌精选", "al-2": "睡前故事", "al-3": "叶惠美"}.get(q["id"]) == s["album"]]
            return ok(album={"id": q["id"], "name": songs[0]["album"] if songs else "", "song": songs})
        if method == "getArtist":
            return ok(artist={"id": "ar-jay", "name": "周杰伦", "album": [{"id": "al-3", "name": "叶惠美", "songCount": 1}]})
        if method == "getPlaylists":
            return ok(playlists={"playlist": [{"id": "pl-sleep", "name": "哄睡", "songCount": 1}]})
        if method == "getPlaylist":
            return ok(playlist={"id": "pl-sleep", "name": "哄睡", "entry": [SONGS[3]]})
        if method == "getIndexes":
            return ok(indexes={"index": [{"name": "E", "artist": [{"id": "d-kids", "name": "儿歌"}, {"id": "d-story", "name": "故事"}]}]})
        if method == "getMusicDirectory":
            if q["id"] == "d-kids":
                return ok(directory={"id": "d-kids", "name": "儿歌",
                                     "child": [dict(s, isDir=False) for s in SONGS if s["parent"] == "d-kids"]
                                     + [{"id": "d-kids2", "title": "英文儿歌", "isDir": True}]})
            if q["id"] == "d-kids2":
                return ok(directory={"id": "d-kids2", "name": "英文儿歌", "child": [{"id": "s9", "title": "Baby Shark", "isDir": False}]})
            return ok(directory={"id": q["id"], "name": "故事", "child": [dict(SONGS[3], isDir=False)]})
        if method == "getRandomSongs":
            return ok(randomSongs={"song": SONGS[:int(q.get("size", 10))]})
        if method == "stream":
            rng = request.headers.get("Range")
            if rng:
                start = int(rng.split("=")[1].split("-")[0])
                return web.Response(status=206, body=AUDIO[start:], content_type="audio/mpeg",
                                    headers={"Content-Range": f"bytes {start}-{len(AUDIO) - 1}/{len(AUDIO)}", "Accept-Ranges": "bytes"})
            return web.Response(body=AUDIO, content_type="audio/mpeg", headers={"Accept-Ranges": "bytes"})
        if method == "getCoverArt":
            return web.Response(body=b"\xff\xd8JPEG" + q["id"].encode(), content_type="image/jpeg")
        return web.json_response({"subsonic-response": {"status": "failed", "error": {"code": 70, "message": "not found"}}})

    app = web.Application()
    app.router.add_get("/rest/{method}", rest)
    return app


@pytest.fixture
async def nas():
    calls = []
    async with TestServer(fake_subsonic(calls)) as server:
        server.calls = calls
        server.base = f"http://127.0.0.1:{server.port}"
        yield server


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(music, "_path", lambda: str(tmp_path / "music.json"))
    monkeypatch.setattr(music, "local_root", lambda: tmp_path / "music")
    monkeypatch.setattr(services, "_path", lambda: str(tmp_path / "services.json"))
    services.STATUS.clear()
    music._index.clear()
    yield


def configure(nas):
    music.apply({"op": "navidrome", "url": nas.base + "/", "user": USER, "password": PASSWORD})


# ---------------------------------------------------------------- 配置和认证

def test_password_is_not_stored_and_token_works(tmp_path, nas):
    configure(nas)
    raw = (tmp_path / "music.json").read_text(encoding="utf-8")
    assert PASSWORD not in raw
    d = json.loads(raw)["navidrome"]
    assert d["token"] == hashlib.md5((PASSWORD + d["salt"]).encode()).hexdigest() and d["url"] == nas.base
    assert oct((tmp_path / "music.json").stat().st_mode)[-3:] == "600"
    music.apply({"op": "navidrome", "url": nas.base + "/rest", "user": USER, "password": ""})   # 只改地址：留着 token
    assert music.load()["navidrome"]["token"] == d["token"] and music.load()["navidrome"]["url"] == nas.base
    with pytest.raises(music.MusicError, match="重新填"):
        music.apply({"op": "navidrome", "url": nas.base, "user": "someone-else", "password": ""})
    with pytest.raises(music.MusicError, match="http"):
        music.apply({"op": "navidrome", "url": "nas:4533", "user": USER, "password": "x"})
    with pytest.raises(music.MusicError, match="标"):
        music.apply({"op": "tag", "tag": "kids", "ref": {"type": "song", "id": "n.s1"}})
    music.apply({"op": "navidrome", "url": "", "user": "", "password": ""})
    assert music.navidrome() is None and music.view()["navidrome"]["has_password"] is False


async def test_ping_records_status_and_wrong_password(nas):
    configure(nas)
    r = await music.navidrome().ping()
    assert r["ok"] and r["version"] == "0.53.3" and r["libraries"] == ["NAS 音乐"]
    assert services.status("music")["ok"] is True
    bad = music.navidrome(password="nope")
    with pytest.raises(music.MusicError, match="用户名或密码不对"):
        await bad.ping()
    st = services.status("music")
    assert st["ok"] is False and "127.0.0.1" not in st["err"] and "t=" not in st["err"]


async def test_unreachable_message_has_no_url():
    src = music.Navidrome("http://127.0.0.1:1", USER, "salt", "tok", timeout=2)
    with pytest.raises(music.MusicError) as e:
        await src.ping()
    assert "127.0.0.1" not in str(e.value) and "tok" not in str(e.value)


# ---------------------------------------------------------------- 找歌、标签、随便放

def test_score():
    assert music.score("两只老虎", "两只老虎 (儿歌版)") == 1.0
    assert music.score("小兔子乖乖", "01.小兔子乖乖") == 1.0
    assert music.score("两支老虎", "两只老虎") >= 0.6              # 同音字
    assert music.score("老虎", "两只老虎") >= 0.6
    assert music.score("晴天", "两只老虎") < 0.6
    assert music.split_query("周杰伦的歌") == ("周杰伦", "artist")
    assert music.split_query("两只老虎这首歌") == ("两只老虎", "")


async def test_find_song_fuzzy_artist_album(nas):
    configure(nas)
    r = await music.find("两只老虎")
    assert r["match"] == "song" and r["tracks"][0]["id"] == "n.s1" and r["tracks"][0]["cover"] == "n.al-1"
    assert r["tracks"][0]["title"] == "两只老虎 (儿歌版)" and r["tracks"][0]["duration"] == 95
    r = await music.find("老虎")                                  # 搜不到（只认开头）→ 全部歌名模糊比
    assert r["tracks"][0]["id"] == "n.s1"
    r = await music.find("两支老虎")                              # 识别成同音字
    assert r["tracks"][0]["id"] == "n.s1"
    r = await music.find("周杰伦的歌")
    assert r["match"] == "artist" and r["name"] == "周杰伦" and [t["id"] for t in r["tracks"]] == ["n.s5"]
    r = await music.find("儿歌精选")
    assert r["match"] == "album" and len(r["tracks"]) == 3
    assert (await music.find("完全没有这首"))["tracks"] == []
    n = len([c for c in nas.calls if c[0] == "search3" and not c[1].get("query")])
    await music.find("老虎")
    assert len([c for c in nas.calls if c[0] == "search3" and not c[1].get("query")]) == n   # 索引缓存着


async def test_find_without_library():
    with pytest.raises(music.MusicError, match="还没接音乐库"):
        await music.find("两只老虎")


async def test_tags_and_random(nas):
    configure(nas)
    with pytest.raises(music.MusicError, match="儿歌"):
        await music.tagged("kids")
    music.apply({"op": "tag", "tag": "kids", "ref": {"type": "folder", "id": "n.d-kids", "name": "儿歌"}})
    music.apply({"op": "tag", "tag": "bedtime", "ref": {"type": "playlist", "id": "n.pl-sleep", "name": "哄睡"}})
    r = await music.tagged("kids")
    assert r["name"] == "儿歌" and {t["id"] for t in r["tracks"]} == {"n.s1", "n.s2", "n.s3", "n.s9"}   # 子文件夹也算
    assert [t["id"] for t in (await music.tagged("bedtime"))["tracks"]] == ["n.s4"]
    assert len((await music.tagged("any", 3))["tracks"]) == 3
    music.apply({"op": "tag", "tag": "kids", "ref": {"type": "folder", "id": "n.d-kids"}, "on": False})
    assert music.load()["tags"]["kids"] == []


# ---------------------------------------------------------------- 本机 music 文件夹

async def test_local_source(tmp_path):
    root = tmp_path / "music"
    (root / "儿歌").mkdir(parents=True)
    (root / "儿歌" / "01 - 两只老虎.mp3").write_bytes(AUDIO)
    (root / "儿歌" / "cover.jpg").write_bytes(b"\xff\xd8")
    (root / "晴天.flac").write_bytes(AUDIO)
    (root / "readme.txt").write_text("x")
    assert [s.key for s in music.sources()] == ["l"]
    r = await music.find("两只老虎")
    t = r["tracks"][0]
    assert t["title"] == "两只老虎" and t["id"].startswith("l.") and t["cover"].startswith("l.") and t["album"] == "儿歌"
    lo = music.Local()
    top = await lo.open("folder", "")
    assert [i["name"] for i in top["items"]] == ["儿歌"] and [s["title"] for s in top["songs"]] == ["晴天"]
    music.apply({"op": "tag", "tag": "kids", "ref": {"type": "folder", "id": top["items"][0]["id"], "name": "儿歌"}})
    assert [x["title"] for x in (await music.tagged("kids"))["tracks"]] == ["两只老虎"]
    with pytest.raises(music.MusicError):
        lo.path(music._enc("../../etc"))
    with pytest.raises(music.MusicError, match="不是空的"):
        lo.delete(top["items"][0]["id"].split(".", 1)[1])
    with pytest.raises(music.MusicError, match="只能传"):
        lo.save_upload_path("", "evil.sh")
    assert lo.save_upload_path("", "../x.mp3").name == "x.mp3"
    music.apply({"op": "toggle", "source": "local", "on": False})
    assert music.sources() == []


# ---------------------------------------------------------------- 后台和设备的接口

@pytest.fixture
async def client(tmp_path):
    p = tmp_path / ".config.yaml"
    p.write_text("", encoding="utf-8")
    app = web.Application()
    AdminHandler({"selected_module": {}, "LLM": {}}, ws_server=None, config_path=p).register(app)
    async with TestClient(TestServer(app)) as c:
        await c.post("/admin/api/setup", json={"password": "pass-1234"})
        yield c


async def test_admin_save_test_browse(client, nas):
    r = await client.post("/admin/api/music/test", json={"source": "navidrome", "url": nas.base, "user": USER, "password": PASSWORD})
    assert (await r.json())["ok"] is True and music.load()["navidrome"]["url"] == ""     # 测试不保存
    r = await client.post("/admin/api/music/test", json={"source": "navidrome", "url": nas.base, "user": USER, "password": "bad"})
    assert (await r.json()) == {"ok": False, "error": "用户名或密码不对"}
    r = await client.post("/admin/api/music", json={"op": "navidrome", "url": nas.base, "user": USER, "password": PASSWORD})
    d = await r.json()
    assert d["navidrome"]["has_password"] and "token" not in json.dumps(d) and PASSWORD not in json.dumps(d)
    assert d["navidrome"]["status"]["ok"] is True and d["ready"] is True
    r = await client.post("/admin/api/music/test", json={"source": "navidrome"})          # 用存下的
    assert (await r.json())["ok"] is True
    assert [i["name"] for i in (await (await client.get("/admin/api/music/browse?src=n&type=albums")).json())["items"]] == ["儿歌精选", "睡前故事"]
    assert (await (await client.get("/admin/api/music/browse?src=n&type=playlists")).json())["items"][0]["id"] == "n.pl-sleep"
    libs = (await (await client.get("/admin/api/music/browse?src=n&type=folders")).json())["items"]
    top = await (await client.get(f"/admin/api/music/browse?type=folder&id={libs[0]['id']}")).json()
    assert [i["name"] for i in top["items"]] == ["儿歌", "故事"]
    kids = await (await client.get("/admin/api/music/browse?type=folder&id=n.d-kids")).json()
    assert len(kids["songs"]) == 3 and kids["items"][0]["name"] == "英文儿歌"
    s = await (await client.get("/admin/api/music/browse?src=n&type=search&q=老虎")).json()
    assert s["songs"][0]["id"] == "n.s1"
    r = await client.get("/admin/api/music/stream/n.s1")                                    # 试听
    assert r.status == 200 and await r.read() == AUDIO
    sd = await (await client.get("/admin/api/services")).json()
    assert sd["music"]["configured"] and sd["music"]["status"]["ok"]
    await client.post("/admin/api/logout", json={})
    assert (await client.get("/admin/api/music")).status == 401
    assert (await client.get("/admin/api/music/stream/n.s1")).status == 401


async def test_kiosk_relay_and_find(client, nas):
    configure(nas)
    await client.post("/admin/api/logout", json={})                     # 设备不登录，经隧道来算本机
    d = await (await client.get("/kiosk/music")).json()
    assert d["ready"] and d["tags"] == {"kids": 0, "bedtime": 0}
    f = await (await client.get("/kiosk/music/find?q=两只老虎")).json()
    assert f["tracks"][0]["id"] == "n.s1"
    r = await client.get("/kiosk/music/stream/n.s1")
    assert r.status == 200 and r.headers["Content-Type"] == "audio/mpeg" and await r.read() == AUDIO
    r = await client.get("/kiosk/music/stream/n.s1", headers={"Range": "bytes=1000-"})
    assert r.status == 206 and r.headers["Content-Range"].startswith("bytes 1000-") and await r.read() == AUDIO[1000:]
    stream_call = [c for c in nas.calls if c[0] == "stream"][-1][1]
    assert stream_call["u"] == USER and "p" not in stream_call
    r = await client.get("/kiosk/music/cover/n.al-1?size=200")
    assert r.status == 200 and (await r.read()).endswith(b"al-1")
    assert [c for c in nas.calls if c[0] == "getCoverArt"][-1][1]["size"] == "200"
    t = await (await client.get("/kiosk/music/tag/kids")).json()
    assert t["tracks"] == [] and "儿歌" in t["error"]
    assert (await client.get("/kiosk/music/stream/zz")).status == 404
    assert (await client.get("/kiosk/music/find?q=x", headers={"X-Forwarded-For": "1.2.3.4"})).status == 403
    assert (await client.get("/kiosk/music/stream/n.s1", headers={"X-Forwarded-For": "1.2.3.4"})).status == 403


async def test_upload_mkdir_delete(client, tmp_path):
    r = await client.post("/admin/api/music/local", json={"op": "mkdir", "dir": "l.", "name": "儿歌"})
    folder = (await r.json())["item"]
    assert folder["name"] == "儿歌" and (tmp_path / "music" / "儿歌").is_dir()
    r = await client.post(f"/admin/api/music/upload?dir={folder['id']}&name=两只老虎.mp3", data=AUDIO)
    assert (await r.json())["size"] == len(AUDIO) and (tmp_path / "music" / "儿歌" / "两只老虎.mp3").read_bytes() == AUDIO
    assert (await client.post("/admin/api/music/upload?dir=l.&name=x.exe", data=b"1")).status == 400
    assert not list((tmp_path / "music").glob(".*.part"))
    f = await (await client.get("/kiosk/music/find?q=两只老虎")).json()
    tid = f["tracks"][0]["id"]
    r = await client.get(f"/kiosk/music/stream/{tid}", headers={"Range": "bytes=10-19"})
    assert r.status == 206 and await r.read() == AUDIO[10:20]
    assert (await client.post("/admin/api/music/local", json={"op": "delete", "id": tid})).status == 200
    assert (await client.post("/admin/api/music/local", json={"op": "delete", "id": folder["id"]})).status == 200
    assert not (tmp_path / "music" / "儿歌").exists()


# ---------------------------------------------------------------- 常用口令

def plan(text):
    return cm.match(text, cm.defaults())[0]


def test_music_commands():
    p = plan("小麦小麦，放两只老虎")
    assert p.steps == "music" and p.args == {"action": "play", "query": "两只老虎"}
    assert plan("我想听小兔子乖乖").args == {"action": "play", "query": "小兔子乖乖"}
    assert plan("来一首三只小猪吧").args == {"action": "play", "query": "三只小猪"}
    assert plan("放几首儿歌").args == {"action": "play", "tag": "kids"}
    assert plan("放睡前故事").args == {"action": "play", "tag": "bedtime"}
    assert plan("讲个故事").args == {"action": "play", "tag": "bedtime"}
    assert plan("放首歌").args == {"action": "play", "tag": "any"}
    assert plan("下一首").args == {"action": "next"} and plan("换一首").args == {"action": "next"}
    assert plan("上一首").args == {"action": "prev"}
    assert plan("暂停").args == {"action": "pause"} and plan("继续播放").args == {"action": "resume"}
    assert plan("单曲循环").args == {"action": "mode", "mode": "one"}
    assert plan("随机播放").args == {"action": "mode", "mode": "shuffle"}
    assert plan("列表循环").args == {"action": "mode", "mode": "loop"}
    assert plan("停").tool == "self.media.play" and plan("停").args == {"action": "stop"}
    for t in ("放东方广播", "放雨声", "放心吧", "放假了", "放大一点", "放十分钟雨声", "给我讲个笑话", "放松一下"):
        p = plan(t)
        assert p is None or p.rule != "music", t
