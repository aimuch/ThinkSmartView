"""USB 接口（设计画布第 46 块）：找 U 盘里的照片、读状态、切换、导入相册（ffmpeg 换成复制）。"""
import json
import os
import time

import pytest

from thinksmart_client import usb


def make(path, size=4096, age=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'x' * size)
    t = time.time() - age
    os.utime(path, (t, t))
    return path


def test_find_images_newest_first_skipping_junk(tmp_path):
    make(tmp_path / 'a.jpg', age=30)
    make(tmp_path / 'DCIM' / '100' / 'b.JPEG', age=10)
    make(tmp_path / 'c.png', age=20)
    make(tmp_path / 'tiny.jpg', size=100)                      # 缩略图之类太小的不要
    make(tmp_path / '._d.jpg')                                 # macOS 的附带文件
    make(tmp_path / '.Trashes' / 'e.jpg')
    make(tmp_path / 'System Volume Information' / 'f.jpg')
    make(tmp_path / 'notes.txt')
    assert [p.name for p in usb.find_images(tmp_path, 10)] == ['b.JPEG', 'c.png', 'a.jpg']
    assert [p.name for p in usb.find_images(tmp_path, 2)] == ['b.JPEG', 'c.png']


@pytest.fixture
def port(tmp_path, monkeypatch):
    drive = tmp_path / 'media' / 'sda1'
    make(drive / 'x.jpg', age=5)
    make(drive / 'y.png', age=1)
    state = {'role': 'device'}
    calls = []

    async def fake_run(*args, timeout=15):
        calls.append(args)
        if args[0] == 'ffmpeg':
            src, dst = args[args.index('-i') + 1], args[-1]
            open(dst, 'wb').write(open(src, 'rb').read())
            return ''
        if args[-1] in ('host', 'device'):
            state['role'] = args[-1]
        drives = [{'dev': 'sda1', 'path': str(drive), 'label': 'KINGSTON', 'fs': 'exfat'}] if state['role'] == 'host' else []
        return 'noise\n' + json.dumps({'role': state['role'], 'drives': drives}) + '\n'
    monkeypatch.setattr(usb, '_run', fake_run)
    monkeypatch.setattr(usb.os.path, 'exists', lambda p: True if p == usb.MODE else os.path.lexists(p))
    seen = []
    u = usb.Usb(tmp_path / 'album', 2, on_change=seen.append)
    return u, calls, seen, drive


@pytest.mark.asyncio
async def test_status_switch_and_import(port, tmp_path):
    u, calls, seen, drive = port
    v = await u.refresh()
    assert v['available'] and v['role'] == 'device' and v['drives'] == []
    v = await u.set_mode('host')
    assert calls[-1] == ('sudo', '-n', usb.MODE, 'host') and v['role'] == 'host'
    v = await u.refresh()
    assert v['drives'][0]['label'] == 'KINGSTON' and v['drives'][0]['images'] == 2
    old = make(tmp_path / 'album' / 'old.jpg', age=999)
    assert await u.import_photos(str(drive)) == 2
    album = sorted(p.name for p in (tmp_path / 'album').glob('*.jpg'))
    assert len(album) == 2 and 'old.jpg' not in album and not old.exists()   # 满了删最旧的
    assert seen[-1]['importing'] is None
    with pytest.raises(ValueError):
        await u.import_photos('/nowhere')
    with pytest.raises(ValueError):
        await u.set_mode('bogus')
