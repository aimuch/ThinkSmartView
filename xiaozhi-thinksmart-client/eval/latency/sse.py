import aiohttp, asyncio, time, json
async def m():
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None)) as s:
        async with s.get('http://127.0.0.1:8090/events') as r:
            ev = None
            async for line in r.content:
                line = line.decode().strip()
                if line.startswith('event:'):
                    ev = line[6:].strip()
                elif line.startswith('data:') and ev in ('message', 'state', 'thread'):
                    d = json.loads(line[5:])
                    if ev == 'state': d = d.get('state')
                    elif ev == 'message': d = (d['role'], d['text'][:30])
                    else: d = len(d.get('messages', []))
                    print(f'{time.time():.3f}\t{ev}\t{d}', flush=True)
asyncio.run(m())
