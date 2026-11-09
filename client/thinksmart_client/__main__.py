"""python -m thinksmart_client: the ThinkSmart View voice client for xiaozhi-hub-server."""
import asyncio
import logging
import os
import signal

from aiohttp import web

from . import config
from .app import Client
from .web import make_app


async def main():
    cfg = config.load()
    client = Client(cfg)
    await client.start()
    runner = web.AppRunner(make_app(client), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, '127.0.0.1', cfg['port']).start()
    logging.getLogger(__name__).info('page on http://127.0.0.1:%d/', cfg['port'])
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    await stop.wait()
    # 停止（更新、重启）前先告诉屏幕页面，并正常结束 /events：直接被杀掉时，kiosk 的 WebKitGTK 留着一条
    # CLOSE-WAIT 的连接，之后整页的请求都发不出去，屏幕停在空白首页（见 kiosk_watch.py）
    logging.getLogger(__name__).info('stopping: telling the page')
    client.events.publish('bye', {})
    await asyncio.sleep(0.6)
    try:
        await asyncio.wait_for(runner.cleanup(), 3)
    except Exception:  # noqa: BLE001
        pass
    logging.shutdown()
    os._exit(0)   # 和以前被 SIGTERM 直接结束一样，不等音频等后台线程


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s: %(message)s')
    asyncio.run(main())
