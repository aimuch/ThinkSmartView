"""USB 接口（设计画布第 46 块）：底部 Type-C 口连电脑（USB 网络，默认）还是接 U 盘，U 盘照片导入家庭相册。

切换和挂载在系统层（/usr/local/sbin/thinksmart-usb-mode，sudo -n；U 盘由 udev 只读挂到
/media/thinksmart-usb/<名字>）。重启后系统回到连电脑，这里不记这个设置。
导入：U 盘里最新的 jpg / jpeg / png（最多 ALBUM_MAX 张、往下找 6 层），用 ffmpeg 缩到长边 1600 像素存成 jpg。
"""
import asyncio
import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger(__name__)

MODE = '/usr/local/sbin/thinksmart-usb-mode'
IMAGE_EXT = {'.jpg', '.jpeg', '.png'}
DEPTH = 6
LONG_SIDE = 1600
SKIP_DIRS = {'.Trashes', '.Spotlight-V100', '.fseventsd', 'System Volume Information', '$RECYCLE.BIN', '.thumbnails'}


async def _run(*args, timeout=15):
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError('没有响应')
    if proc.returncode:
        raise RuntimeError((err or out).decode(errors='replace').strip()[:200] or f'退出码 {proc.returncode}')
    return out.decode(errors='replace')


def _parse(text):
    try:
        data = json.loads(text.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None
    return data if isinstance(data, dict) else None


def find_images(root, limit, depth=DEPTH):
    """U 盘里最新的 limit 张图片（按修改时间），不进隐藏目录和系统目录。"""
    found = []
    root = Path(root)
    base = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith('.') and d not in SKIP_DIRS
                       and len(Path(dirpath, d).parts) - base <= depth]
        for f in filenames:
            if f.startswith('._') or Path(f).suffix.lower() not in IMAGE_EXT:
                continue
            p = Path(dirpath, f)
            try:
                st = p.stat()
            except OSError:
                continue
            if st.st_size > 2048:
                found.append((st.st_mtime, p))
    found.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in found[:limit]]


class Usb:
    def __init__(self, album_dir, album_max, on_change=None):
        self.album_dir = Path(album_dir)
        self.album_max = album_max
        self.on_change = on_change or (lambda view: None)
        self.state = {'available': os.path.exists(MODE), 'role': 'none', 'drives': []}
        self.importing = None    # {'done', 'total'} while photos are being copied
        self.counts = {}         # mount path -> image count (counted once per mount)

    def view(self):
        drives = [{**d, 'images': self.counts.get(d['path'])} for d in self.state.get('drives', [])]
        return {**self.state, 'drives': drives, 'importing': self.importing}

    async def refresh(self):
        if not self.state['available']:
            return self.view()
        try:
            data = _parse(await _run(MODE, 'status'))
        except (RuntimeError, OSError) as e:
            log.warning('usb status: %s', e)
            data = None
        if data:
            self.state.update(role=data.get('role', 'none'), drives=data.get('drives') or [])
            for d in self.state['drives']:
                if d['path'] not in self.counts:
                    self.counts[d['path']] = len(await asyncio.to_thread(find_images, d['path'], 10_000))
        return self.view()

    async def set_mode(self, role):
        if role not in ('host', 'device'):
            raise ValueError('只能是 host（接 U 盘）或 device（连电脑）')
        if not self.state['available']:
            raise ValueError('这台设备的系统层还没有 USB 切换（升级系统层后再试）')
        try:
            data = _parse(await _run('sudo', '-n', MODE, role, timeout=20))
        except (RuntimeError, OSError) as e:
            raise ValueError(f'没切过去：{e}') from e
        if data:
            self.state.update(role=data.get('role', role), drives=data.get('drives') or [])
        log.info('usb port -> %s', role)
        self.on_change(self.view())
        return self.view()

    async def import_photos(self, path):
        """把这个 U 盘里最新的照片导进相册；返回导入了几张。"""
        if self.importing:
            raise ValueError('正在导入，等这一批完')
        drive = next((d for d in self.state.get('drives', []) if d['path'] == path), None)
        if not drive:
            raise ValueError('没找到这个 U 盘，拔了吗？')
        files = await asyncio.to_thread(find_images, path, self.album_max)
        if not files:
            raise ValueError('U 盘里没有 jpg、png 照片')
        self.album_dir.mkdir(parents=True, exist_ok=True)
        self.importing = {'done': 0, 'total': len(files)}
        self.on_change(self.view())
        ok = 0
        try:
            stamp = int(time.time() * 1000)
            # oldest first, so the newest photo also has the newest file time in the album
            for i, src in enumerate(reversed(files)):
                dst = self.album_dir / f'usb-{stamp}-{i}.jpg'
                tmp = dst.with_suffix('.part.jpg')
                try:
                    await _run('ffmpeg', '-v', 'error', '-y', '-i', str(src), '-vf',
                               f"scale='if(gt(iw,ih),min({LONG_SIDE},iw),-2)':'if(gt(iw,ih),-2,min({LONG_SIDE},ih))'",
                               '-frames:v', '1', '-q:v', '3', str(tmp), timeout=60)
                    tmp.replace(dst)
                    ok += 1
                except (RuntimeError, OSError) as e:
                    log.warning('import %s: %s', src.name, e)
                    tmp.unlink(missing_ok=True)
                self.importing['done'] = i + 1
                if i % 5 == 4:
                    self.on_change(self.view())
        finally:
            self.importing = None
            old = sorted(self.album_dir.glob('*.jpg'), key=lambda p: p.stat().st_mtime, reverse=True)[self.album_max:]
            for p in old:
                p.unlink(missing_ok=True)
            self.on_change(self.view())
        log.info('imported %d photo(s) from %s', ok, path)
        return ok
