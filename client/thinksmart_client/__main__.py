"""python -m thinksmart_client: the ThinkSmart View voice client for xiaozhi-hub-server."""
import asyncio
import logging

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
    await asyncio.Event().wait()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s: %(message)s')
    asyncio.run(main())
