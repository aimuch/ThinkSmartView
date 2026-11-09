"""Photo slideshow (photos.py): what is accepted, the show on disk, the screen hint, the page's API and
the voice tool that closes it.

  uv run --no-project --with numpy --with soxr --with websockets --with aiohttp --with pytest --with pytest-asyncio \\
      python -m pytest -q tests/test_photos.py
"""
import asyncio
import json
import sys
import types

import pytest

sys.modules.setdefault('sounddevice', types.ModuleType('sounddevice'))
sys.modules.setdefault('thinksmart_client.webrtc_apm', types.ModuleType('thinksmart_client.webrtc_apm'))

import test_barge_in  # noqa: E402
from thinksmart_client import device, mcp, photos  # noqa: E402

JPEG = b'\xff\xd8\xff\xe0' + b'x' * 100
PNG = b'\x89PNG\r\n\x1a\n' + b'x' * 100
WEBP = b'RIFF\x00\x00\x00\x00WEBPVP8 ' + b'x' * 100


def test_only_images_are_taken():
    assert [photos.sniff(d) for d in (JPEG, PNG, WEBP, b'GIF89a...', b'<svg', b'')] == \
        ['jpg', 'png', 'webp', None, None, None]


def test_settings():
    assert photos.normalize() == {'title': '', 'interval': 6, 'minutes': 30}
    assert photos.normalize('  京都\n 2026-10 ', '10', 5) == {'title': '京都 2026-10', 'interval': 10, 'minutes': 5}
    assert len(photos.normalize('长' * 100)['title']) == photos.TITLE_MAX
    for bad in ({'interval_seconds': 1}, {'interval_seconds': '3.5'}, {'minutes': 0}, {'minutes': 241}):
        with pytest.raises(ValueError):
            photos.normalize(**bad)


def test_a_show_on_disk(tmp_path):
    s = photos.Slides(tmp_path / 'p')
    up = s.begin()
    up.add(JPEG)
    up.add(PNG)
    show = up.commit('京都', '8', '20', now=1000)
    assert show.view() == {'id': show.id, 'title': '京都', 'interval': 8, 'count': 2, 'until': 1000 + 20 * 60}
    path, kind = s.file(show.id, '1')
    assert path.read_bytes() == PNG and kind == 'image/png'
    assert s.file(show.id, 2) is None and s.file(show.id, -1) is None and s.file('other', 0) is None
    assert s.file(show.id, '../x') is None

    up2 = s.begin()
    up2.add(WEBP)
    second = up2.commit()
    assert s.current is second and not show.folder.exists()      # a new show replaces the old one
    assert s.close() and not second.folder.exists() and s.current is None
    assert not s.close()


def test_a_bad_upload_leaves_nothing(tmp_path):
    s = photos.Slides(tmp_path)
    up = s.begin()
    with pytest.raises(ValueError):
        up.add(b'not an image')
    with pytest.raises(ValueError):
        up.add(b'\xff\xd8\xff' + b'x' * photos.MAX_IMAGE_BYTES)
    with pytest.raises(ValueError):
        up.commit()                                   # no images
    assert not up.folder.exists() and s.current is None
    up = s.begin()
    up.add(JPEG)
    with pytest.raises(ValueError):
        up.commit(minutes='forever')
    assert not up.folder.exists() and s.current is None


def test_limits(tmp_path, monkeypatch):
    monkeypatch.setattr(photos, 'MAX_IMAGES', 2)
    up = photos.Slides(tmp_path).begin()
    up.add(JPEG)
    up.add(JPEG)
    with pytest.raises(ValueError):
        up.add(JPEG)
    monkeypatch.setattr(photos, 'MAX_TOTAL_BYTES', 150)
    up = photos.Slides(tmp_path).begin()
    up.add(JPEG)
    with pytest.raises(ValueError):
        up.add(JPEG)


def test_the_screen_stays_on_while_photos_show(tmp_path, monkeypatch):
    monkeypatch.setattr(device, 'HINT_PATH', tmp_path / 'screen.json')
    hint = lambda: json.loads((tmp_path / 'screen.json').read_text())   # noqa: E731
    s = device.Screen()
    s.release()
    seq = hint()['seq']
    s.photos(True)
    assert hint()['hold'] and hint()['seq'] == seq + 1                  # lit, and kept on
    s.photos(False)
    assert not hint()['hold'] and hint()['seq'] == seq + 2


def make_client(tmp_path):
    c = test_barge_in.make_client()
    c.slides = photos.Slides(tmp_path / 'photos')
    c.screen.showing = False
    c.screen.photos = lambda on: setattr(c.screen, 'showing', on)
    seen = []
    orig = c.events.publish
    c.events.publish = lambda e, d: (seen.append((e, d)), orig(e, d))
    return c, seen


def form(*images, **fields):
    import aiohttp
    f = aiohttp.FormData()
    for k, v in fields.items():
        f.add_field(k, str(v))
    for i, data in enumerate(images):
        f.add_field('image', data, filename=f'{i}.jpg', content_type='image/jpeg')
    return f


@pytest.mark.asyncio
async def test_the_page_api(tmp_path):
    from aiohttp.test_utils import TestClient, TestServer
    from thinksmart_client import web as web_mod
    c, seen = make_client(tmp_path)
    async with TestClient(TestServer(web_mod.make_app(c))) as http:
        r = await http.post('/api/photos', data=form(JPEG, PNG, title='京都', interval_seconds=5))
        body = await r.json()
        assert r.status == 200 and body['ok'] and body['photos']['count'] == 2
        show_id = body['photos']['id']
        assert c.screen.showing and seen[-1] == ('slides', body['photos'])
        assert c.slides_view() == body['photos']
        assert (await (await http.get('/api/photos')).json())['photos']['id'] == show_id

        img = await http.get(f'/photos/{show_id}/1')
        assert img.status == 200 and await img.read() == PNG and img.headers['Content-Type'] == 'image/png'
        assert (await http.get(f'/photos/{show_id}/2')).status == 404
        assert (await http.get('/photos/nope/0')).status == 404

        bad = await http.post('/api/photos', data=form(b'<html>', title='x'))
        assert bad.status == 400 and 'JPEG' in (await bad.json())['error']
        assert c.slides.current.id == show_id                         # a bad upload keeps the open show
        assert (await http.post('/api/photos', json={'images': []})).status == 400

        r = await http.post('/api/photos/close')
        assert (await r.json()) == {'ok': True, 'closed': True}
        assert not c.screen.showing and seen[-1] == ('slides', None) and c.slides_view() is None
        assert (await http.get(f'/photos/{show_id}/0')).status == 404
        assert (await (await http.post('/api/photos/close')).json())['closed'] is False


@pytest.mark.asyncio
async def test_a_show_closes_itself_after_its_minutes(tmp_path):
    c, seen = make_client(tmp_path)
    up = c.slides.begin()
    up.add(JPEG)
    first = up.commit(minutes=1)
    first.minutes = 0.002                       # 0.12 s
    c.show_photos(first)
    up = c.slides.begin()
    up.add(JPEG)
    second = up.commit(minutes=1)
    c.show_photos(second)                       # replaces it: the first one's timer is gone
    await asyncio.sleep(0.2)
    assert c.slides.current is second and c.screen.showing
    await c.close_photos(first.id)              # a stale id does nothing
    assert c.slides.current is second
    second.minutes = 0.002
    c.show_photos(second)
    await asyncio.sleep(0.2)
    assert c.slides.current is None and not c.screen.showing and seen[-1] == ('slides', None)


@pytest.mark.asyncio
async def test_closing_by_voice(tmp_path):
    c, _ = make_client(tmp_path)
    assert 'self.screen.close_photos' in [t['name'] for t in mcp.TOOLS]
    call = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'self.screen.close_photos'}}
    text = json.loads((await mcp.handle(call, c))['result']['content'][0]['text'])
    assert text['closed'] is False and text['note']
    up = c.slides.begin()
    up.add(JPEG)
    c.show_photos(up.commit())
    text = json.loads((await mcp.handle(call, c))['result']['content'][0]['text'])
    assert text == {'success': True, 'closed': True} and c.slides.current is None
