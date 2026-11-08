"""Replay a wav to the hub like the client does (realtime Opus, 20 ms frames) and time each step.

Run on the device with the client's venv:
  ~/thinksmart-client/.venv/bin/python harness.py s1.wav l5.wav ... [--rounds N] [--device ID]
Times are ms after the last speech sample was sent.
"""
import asyncio, json, sys, time, wave, argparse, random
import numpy as np
sys.path.insert(0, '/home/ubuntu/thinksmart-client')
from thinksmart_client.opus import Encoder
from websockets.asyncio.client import connect

FRAME = 320


def load(path):
    with wave.open(path) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype='<i2').astype(np.float32) / 32768
    loud = np.nonzero(np.abs(x) > 0.02)[0]
    return x[loud[0]:loud[-1] + 1]


async def one(path, device, token):
    speech = load(path)
    lead = np.random.default_rng().normal(0, 0.0005, 8000).astype(np.float32)   # 0.5 s room hush
    enc = Encoder(16000, 1)
    hdr = {'Authorization': f'Bearer {token}', 'Protocol-Version': '1', 'Device-Id': device,
           'Client-Id': 'latency-harness'}
    ws = await connect('ws://127.0.0.1:16689/xiaozhi/v1/', additional_headers=hdr, compression=None,
                       max_size=10 * 1024 * 1024)
    await ws.send(json.dumps({'type': 'hello', 'version': 1, 'transport': 'websocket', 'features': {'mcp': True},
                              'audio_params': {'format': 'opus', 'sample_rate': 16000, 'channels': 1,
                                               'frame_duration': 20}}))
    while True:
        m = json.loads(await ws.recv())
        if m.get('type') == 'hello':
            sid = m.get('session_id')
            break
    await asyncio.sleep(1.5)   # like a real wake: the hub has asked for tools by now
    t = {}
    end = None
    stop = asyncio.Event()

    async def reader():
        async for msg in ws:
            now = time.monotonic()
            if isinstance(msg, bytes):
                if t.get('stt'):
                    t.setdefault('audio', now)
                    return   # first reply audio: enough, hang up
                continue
            d = json.loads(msg)
            k = d.get('type')
            if k == 'stt':
                t.setdefault('stt', now); t['text'] = d.get('text')
                stop.set()
            elif k == 'tts':
                s = d.get('state')
                if s == 'start': t.setdefault('tts_start', now)
                elif s == 'sentence_start': t.setdefault('sentence', now); t.setdefault('first_sentence', d.get('text'))
                elif s == 'stop': t.setdefault('tts_stop', now); return
            elif k == 'mcp':
                p = d.get('payload') or {}
                if p.get('method') == 'initialize':
                    await ws.send(json.dumps({'type': 'mcp', 'session_id': sid, 'payload': {
                        'jsonrpc': '2.0', 'id': p.get('id'), 'result': {'protocolVersion': '2024-11-05',
                                                                        'capabilities': {'tools': {}}}}}))
                elif p.get('method') == 'tools/list':
                    await ws.send(json.dumps({'type': 'mcp', 'session_id': sid, 'payload': {
                        'jsonrpc': '2.0', 'id': p.get('id'), 'result': {'tools': []}}}))

    rt = asyncio.create_task(reader())
    await ws.send(json.dumps({'type': 'listen', 'state': 'start', 'mode': 'realtime', 'session_id': sid}))
    audio = np.concatenate([lead, speech])
    nxt = time.monotonic()
    i = 0
    while not stop.is_set():
        if i < len(audio):
            chunk = audio[i:i + FRAME]
        else:
            chunk = np.random.default_rng().normal(0, 0.0005, FRAME).astype(np.float32)
        if len(chunk) < FRAME:
            chunk = np.pad(chunk, (0, FRAME - len(chunk)))
        pcm = (np.clip(chunk, -1, 1) * 32767).astype('<i2')
        await ws.send(enc.encode(pcm))
        i += FRAME
        if end is None and i >= len(audio):
            end = time.monotonic()
        nxt += FRAME / 16000
        await asyncio.sleep(max(0, nxt - time.monotonic()))
        if end and time.monotonic() - end > 20:
            break
    try:
        await asyncio.wait_for(rt, 25)
    except asyncio.TimeoutError:
        pass
    await ws.close()
    out = {'file': path, 'speech_s': round(len(speech) / 16000, 2), 'text': t.get('text'),
           'first_sentence': t.get('first_sentence')}
    for k in ('stt', 'tts_start', 'sentence', 'audio'):
        out[k] = round((t[k] - end) * 1000) if k in t else None
    return out


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('files', nargs='+')
    ap.add_argument('--rounds', type=int, default=1)
    ap.add_argument('--device', default='latency-test')
    ap.add_argument('--token', default='')
    ap.add_argument('--gap', type=float, default=3)
    a = ap.parse_args()
    for r in range(a.rounds):
        for f in a.files:
            res = await one(f, a.device, a.token)
            print(json.dumps(res, ensure_ascii=False), flush=True)
            await asyncio.sleep(a.gap)

asyncio.run(main())
