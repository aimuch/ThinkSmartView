"""后台看屏幕（设计画布第 83 块）：问屏幕程序（system/kiosk/thinksmart-kiosk.py）要一张现在的截图。

它在用户的 runtime 目录里开一个 UNIX socket：发「snapshot」，它用 weston-screenshooter 截、按屏幕方向转正，
回一行 JSON（{"ok", "path"} 或 {"ok": false, "error"}）。没有屏幕程序（开发机、老系统层）时报 ShotError。
"""
import asyncio
import json
import os
from pathlib import Path

SOCK = Path(os.environ.get('XDG_RUNTIME_DIR') or f'/run/user/{os.getuid()}') / 'thinksmart-kiosk.sock'
MAX_BYTES = 8 * 1024 * 1024


class ShotError(Exception):
    pass


async def take(timeout=25):
    """PNG 字节。"""
    if not SOCK.exists():
        raise ShotError('屏幕程序没开截图（系统层 0.2.22 以上才有）')
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(str(SOCK)), 5)
    except (OSError, asyncio.TimeoutError) as e:
        raise ShotError(f'连不上屏幕程序：{type(e).__name__}') from e
    try:
        writer.write(b'snapshot\n')
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout)
    except (OSError, asyncio.TimeoutError) as e:
        raise ShotError(f'屏幕程序没回：{type(e).__name__}') from e
    finally:
        writer.close()
    try:
        reply = json.loads(line or b'{}')
    except ValueError as e:
        raise ShotError('屏幕程序回的不对') from e
    if not reply.get('ok'):
        raise ShotError(f"没截成：{reply.get('error') or '不知道为什么'}")
    path = Path(reply['path'])
    if path.stat().st_size > MAX_BYTES:
        raise ShotError('截图太大')
    return path.read_bytes()
