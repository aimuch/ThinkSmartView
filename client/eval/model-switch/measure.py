"""Time model switching on the device (run there with the client's venv python).

  hub      as a second device (its own device-id, the real one is left alone): switch on the page's
           endpoint (POST /kiosk/llm/use, plus the GET an older hub needs) with a live connection,
           ask a question after each switch, and call models by name ("嗨 Claude" -> "我在")
  client   through the client's page API (POST :8090/api/model), the real device's whole round trip

  python measure.py hub [--rounds 3] [--no-ask]
  python measure.py client [--rounds 3]
Prints one JSON line per measurement, then a summary. An older hub changes its default model on
every API switch: back up data/.config.yaml before and put it back after.
"""
import argparse
import asyncio
import json
import statistics
import time
import uuid

import aiohttp
import websockets

HUB_HTTP = 'http://127.0.0.1:16688'
HUB_WS = 'ws://127.0.0.1:16689/xiaozhi/v1/'
CLIENT = 'http://127.0.0.1:8090'
DEVICE = 'measure-switch'
PLAN = [  # (label, body without device)
    ('ClaudeAPI', {'kind': 'api', 'vendor': 'claude'}),
    ('DeepSeek', {'kind': 'api', 'vendor': 'deepseek'}),
    ('Grok', {'kind': 'grok', 'vendor': 'grok_bot', 'entry': 'GrokBot_孙悟空'}),
    ('DeepSeek', {'kind': 'api', 'vendor': 'deepseek'}),
]
CALLS = [('嗨 Claude', 'ClaudeAPI'), ('嗨 deepseek', 'DeepSeek')]
QUESTION = '一加一等于几？只回答数字'


def out(**kw):
    print(json.dumps(kw, ensure_ascii=False), flush=True)
    return kw


class FakeDevice:
    def __init__(self):
        self.ws = None
        self.events = asyncio.Queue()

    async def connect(self):
        self.ws = await websockets.connect(HUB_WS, additional_headers={'device-id': DEVICE,
                                                                      'client-id': str(uuid.uuid4())},
                                           max_size=None, ping_interval=20)
        await self.ws.send(json.dumps({'type': 'hello', 'version': 1, 'transport': 'websocket',
                                       'features': {'ready_on_tts_start': True},
                                       'audio_params': {'format': 'opus', 'sample_rate': 16000, 'channels': 1,
                                                        'frame_duration': 20}}))
        asyncio.create_task(self._read())
        while True:
            _, msg = await self.events.get()
            if isinstance(msg, dict) and msg.get('type') == 'hello':
                return

    async def _read(self):
        async for msg in self.ws:
            self.events.put_nowait((time.monotonic(), msg if isinstance(msg, bytes) else json.loads(msg)))

    async def turn(self, text, timeout=60):
        """Send text as if recognised; time to the first reply audio and to tts stop, and the reply text."""
        while not self.events.empty():
            self.events.get_nowait()
        t0 = time.monotonic()
        await self.ws.send(json.dumps({'type': 'listen', 'state': 'detect', 'text': text}))
        first_audio, said = None, []
        while True:
            t, msg = await asyncio.wait_for(self.events.get(), timeout)
            if isinstance(msg, bytes):
                first_audio = first_audio or t - t0
            elif msg.get('type') == 'tts' and msg.get('state') == 'sentence_start':
                said.append(msg.get('text') or '')
            elif msg.get('type') == 'tts' and msg.get('state') == 'stop':
                return {'first_audio': round(first_audio, 3) if first_audio else None,
                        'done': round(t - t0, 3), 'reply': ''.join(said)}


async def http(session, method, url, body=None):
    t0 = time.monotonic()
    async with session.request(method, url, json=body) as r:
        data = await r.json(content_type=None)
    return time.monotonic() - t0, data


async def hub(rounds, ask):
    dev = FakeDevice()
    await dev.connect()
    await asyncio.sleep(4)   # components (TTS, tools) come up in the background
    results = []
    async with aiohttp.ClientSession() as s:
        for call, label in CALLS * rounds:
            r = await dev.turn(call)
            results.append(out(kind='call', to=label, **r))
        for _ in range(rounds):
            for label, body in PLAN:
                post, data = await http(s, 'POST', HUB_HTTP + '/kiosk/llm/use', {**body, 'device': DEVICE})
                get = 0.0
                if data.get('ok') and 'options' not in data:   # an older hub: the client asks again
                    get, data = await http(s, 'GET', f'{HUB_HTTP}/kiosk/llm?device={DEVICE}')
                rec = {'kind': 'switch', 'to': label, 'ok': data.get('ok', True), 'post': round(post, 3),
                       'get': round(get, 3), 'total': round(post + get, 3), 'selected': data.get('selected'),
                       'error': data.get('error')}
                if ask and label != 'Grok':
                    rec['ask'] = await dev.turn(QUESTION)
                results.append(out(**rec))
    await dev.ws.close()
    return results


async def client(rounds):
    results = []
    async with aiohttp.ClientSession() as s:
        for _ in range(rounds):
            for label, body in PLAN:
                t, data = await http(s, 'POST', CLIENT + '/api/model', {**body, 'entry': body.get('entry') or {
                    'claude': 'ClaudeAPI', 'deepseek': 'DeepSeekLLM'}[body['vendor']]})
                results.append(out(kind='client', to=label, total=round(t, 3), ok=data.get('ok'),
                                   error=data.get('error')))
                await asyncio.sleep(1)
    return results


def summary(results):
    groups = {}
    for r in results:
        key = (r['kind'], r['to'])
        groups.setdefault(key, []).append(r.get('total') or r.get('first_audio') or 0)
    for (kind, to), v in groups.items():
        print(f'{kind:7s} -> {to:10s} n={len(v)} median={statistics.median(v):.3f}s min={min(v):.3f} max={max(v):.3f}'
              f'  all={[round(x, 2) for x in v]}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('what', choices=['hub', 'client'])
    ap.add_argument('--rounds', type=int, default=3)
    ap.add_argument('--no-ask', action='store_true')
    a = ap.parse_args()
    results = asyncio.run(hub(a.rounds, not a.no_ask) if a.what == 'hub' else client(a.rounds))
    summary(results)


if __name__ == '__main__':
    main()
