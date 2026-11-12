"""Interrupting the hub while it recognises, thinks or speaks, straight over its WebSocket (no mic needed).

Speaks recorded questions to the hub like the client does (16 kHz Opus, 20 ms frames, realtime),
aborts at the given phase, then checks nothing of the old turn comes back and the next question
is answered normally. Uses its own device id, so the device's own conversation is not touched.

  python tests/device_instant_abort.py ws://<device-ip>:16689/xiaozhi/v1/ q1.wav q2.wav q3.wav [rounds]

q1: a question with a long answer ("讲个故事"), q2: a short one, q3: "我刚才让你做过什么" (context check).
WAVs: 16 kHz mono 16-bit. Needs opuslib_next and websockets (the hub's venv has both).
"""
import asyncio
import json
import sys
import time
import wave

import opuslib_next
import websockets

FRAME = 320                      # 20 ms at 16 kHz
SILENCE = bytes(FRAME * 2)


def pcm(path):
    with wave.open(path) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1
        data = w.readframes(w.getnframes())
    return [data[i:i + FRAME * 2].ljust(FRAME * 2, b'\0') for i in range(0, len(data), FRAME * 2)]


class Hub:
    def __init__(self, url):
        self.url = url
        self.events = []         # (t, kind, detail)
        self.audio = []          # t of each reply audio packet
        self.enc = opuslib_next.Encoder(16000, 1, opuslib_next.APPLICATION_VOIP)

    async def __aenter__(self):
        self.ws = await websockets.connect(self.url, additional_headers={
            'Authorization': 'Bearer test-token', 'Protocol-Version': '1',
            'Device-Id': 'instant-abort-test', 'Client-Id': 'instant-abort-test'}, max_size=None)
        await self.ws.send(json.dumps({'type': 'hello', 'version': 1, 'transport': 'websocket',
                                       'features': {'vad_end': True},
                                       'audio_params': {'format': 'opus', 'sample_rate': 16000,
                                                        'channels': 1, 'frame_duration': 20}}))
        while json.loads(await self.ws.recv()).get('type') != 'hello':
            pass
        self.reader = asyncio.create_task(self._read())
        return self

    async def __aexit__(self, *a):
        self.reader.cancel()
        await self.ws.close()

    async def _read(self):
        async for msg in self.ws:
            t = time.monotonic()
            if isinstance(msg, bytes):
                self.audio.append(t)
                continue
            m = json.loads(msg)
            kind = m.get('type') + (':' + m['state'] if m.get('state') else '')
            if kind == 'tts:stop' and m.get('interrupted'):
                kind = 'tts:stop(interrupted)'
            self.events.append((t, kind, m.get('text')))

    def since(self, t0, kind=None):
        return [e for e in self.events if e[0] >= t0 and (kind is None or e[1] == kind)]

    async def wait(self, kind, t0, timeout=20):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            got = self.since(t0, kind)
            if got:
                return got[0]
            await asyncio.sleep(0.02)
        return None

    async def say(self, frames, until=None, tail=1.5):
        """Stream a question in real time, then silence until `until` arrives (or tail seconds)."""
        await self.ws.send(json.dumps({'type': 'listen', 'state': 'start', 'mode': 'realtime'}))
        t0 = time.monotonic()
        for f in frames:
            await self.ws.send(self.enc.encode(f, FRAME))
            await asyncio.sleep(0.02)
        end = time.monotonic() + 10
        while time.monotonic() < end:
            if until and self.since(t0, until):
                break
            if not until and time.monotonic() > end - 10 + tail:
                break
            await self.ws.send(self.enc.encode(SILENCE, FRAME))
            await asyncio.sleep(0.02)
        return t0

    async def abort(self, played_ms=0):
        t = time.monotonic()
        await self.ws.send(json.dumps({'type': 'abort', 'played_ms': played_ms, 'reason': 'wake_word_detected'}))
        return t

    async def answer(self, frames, timeout=40):
        """Ask and wait for the whole reply: (question recognised, reply text, seconds to first audio)."""
        t0 = await self.say(frames, until='stt')
        stt = await self.wait('stt', t0)
        await self.wait('tts:stop', t0, timeout)
        text = '，'.join(e[2] for e in self.since(t0, 'tts:sentence_start') if e[2])
        first = next((a for a in self.audio if a >= t0), None)
        return (stt or (0, 0, None))[2], text, (first - t0) if first else None


def report(name, ok, detail):
    print(f"{'通过' if ok else '失败'}  {name}: {detail}", flush=True)
    return ok


async def recognising(url, q1, q2):
    async with Hub(url) as h:
        await h.say(q1, until='vad:end')
        ta = await h.abort()
        ack = await h.wait('tts:stop(interrupted)', ta, 5)
        await asyncio.sleep(3)                       # the old sentence would be recognised by now
        late = h.since(ta, 'stt')
        stt, reply, first = await h.answer(q2)
        return report('识别中打断', not late and ack and stt and '几点' in stt,
                      f"打断确认 {(ack[0] - ta) * 1000:.0f} ms；旧那句 stt {late or '没有'}；"
                      f"新问题识别为「{stt}」，{first:.1f} 秒出声，回答「{reply[:30]}」" if ack and first else
                      f"ack={ack} late={late} stt={stt}")


async def thinking(url, q1, q3):
    async with Hub(url) as h:
        await h.say(q1, until='stt')
        ta = await h.abort()
        ack = await h.wait('tts:stop(interrupted)', ta, 5)
        await asyncio.sleep(4)                       # the old reply would be streaming by now
        late = [e for e in h.since(ack[0] if ack else ta) if e[1].startswith('tts:sentence')]
        late_audio = [a for a in h.audio if ack and a > ack[0]]
        stt, reply, first = await h.answer(q3)
        return report('思考中打断', ack and not late and not late_audio,
                      f"打断确认 {(ack[0] - ta) * 1000:.0f} ms；之后旧回答 {len(late)} 句、{len(late_audio)} 包音频；"
                      f"再问「{stt}」答「{reply[:60]}」" if ack else f"ack={ack}")


async def speaking(url, q1, q2):
    async with Hub(url) as h:
        t0 = await h.say(q1, until='tts:sentence_start')
        await h.wait('tts:sentence_start', t0)
        await asyncio.sleep(2)
        played = max(len([a for a in h.audio if a >= t0]) * 60 - 600, 0)
        ta = await h.abort(played)
        ack = await h.wait('tts:stop(interrupted)', ta, 5)
        await asyncio.sleep(3)
        late = [e for e in h.since(ack[0] if ack else ta) if e[1].startswith('tts:sentence')]
        late_audio = [a for a in h.audio if ack and a > ack[0] + 0.2]
        stt, reply, _ = await h.answer(q2)
        return report('回答中打断', ack and not late and not late_audio and stt and reply,
                      f"打断确认 {(ack[0] - ta) * 1000:.0f} ms；之后旧回答 {len(late)} 句、{len(late_audio)} 包音频；"
                      f"新问题「{stt}」答「{reply[:30]}」" if ack else f"ack={ack}")


async def main():
    url, q1, q2, q3 = sys.argv[1], pcm(sys.argv[2]), pcm(sys.argv[3]), pcm(sys.argv[4])
    rounds = int(sys.argv[5]) if len(sys.argv) > 5 else 3
    results = []
    for i in range(rounds):
        print(f'--- 第 {i + 1} 轮', flush=True)
        results.append(await recognising(url, q1, q2))
        results.append(await thinking(url, q1, q3))
        results.append(await speaking(url, q1, q2))
    print(f'共 {len(results)} 项，通过 {sum(1 for r in results if r)}')


if __name__ == '__main__':
    asyncio.run(main())
