"""Synthesize sentences.tsv with many voices -> $WAKE_EVAL_DATA/tts/<id>~<voice>~<rate>.wav (16 kHz mono).

Voices: 10 Microsoft edge-tts voices (needs internet) and the macOS built-in zh_CN voices (`say`).
Wake word clips get three speeds, the rest one. Existing files are skipped, so it can be rerun.

    uv run --no-project --with edge-tts --with numpy python eval/wake/gen_tts.py
"""
import asyncio
import os
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from common import DATA, sentences  # noqa: E402

EDGE = ['zh-CN-XiaoxiaoNeural', 'zh-CN-XiaoyiNeural', 'zh-CN-YunjianNeural', 'zh-CN-YunxiNeural',
        'zh-CN-YunxiaNeural', 'zh-CN-YunyangNeural', 'zh-CN-liaoning-XiaobeiNeural',
        'zh-CN-shaanxi-XiaoniNeural', 'zh-TW-HsiaoChenNeural', 'zh-TW-YunJheNeural']
SAY = ['Eddy', 'Flo', 'Grandma', 'Grandpa', 'Reed', 'Rocko', 'Sandy', 'Shelley', 'Tingting']
RATES = {'slow': ('-10%', 150), 'norm': ('+0%', 190), 'fast': ('+20%', 240)}   # edge, say (words/min)


def to_wav(src, dst):
    subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-i', src, '-ar', '16000', '-ac', '1',
                    '-sample_fmt', 's16', dst], check=True)
    os.unlink(src)


async def edge(sem, text, voice, rate, out):
    import edge_tts
    async with sem:
        for attempt in range(3):
            try:
                await edge_tts.Communicate(text, voice, rate=rate).save(str(out) + '.mp3')
                await asyncio.to_thread(to_wav, str(out) + '.mp3', str(out))
                return
            except Exception as e:   # the service drops connections now and then
                print('retry', out.name, e)
                await asyncio.sleep(2)


async def say(sem, text, voice, rate, out):
    async with sem:
        aiff = str(out) + '.aiff'
        p = await asyncio.create_subprocess_exec('say', '-v', f'{voice} (中文（中国大陆）)', '-r', str(rate),
                                                 '-o', aiff, text)
        await p.wait()
        await asyncio.to_thread(to_wav, aiff, str(out))


async def main():
    out_dir = DATA / 'tts'
    out_dir.mkdir(parents=True, exist_ok=True)
    edge_sem, say_sem, jobs = asyncio.Semaphore(6), asyncio.Semaphore(4), []
    for s in sentences():
        for name in (RATES if s.kind == 'pos' else ['norm']):
            for v in EDGE:
                f = out_dir / f'{s.id}~{v[3:]}~{name}.wav'
                if not f.exists():
                    jobs.append(edge(edge_sem, s.text, v, RATES[name][0], f))
            for v in SAY:
                f = out_dir / f'{s.id}~mac-{v}~{name}.wav'
                if not f.exists():
                    jobs.append(say(say_sem, s.text, v, RATES[name][1], f))
    print(len(jobs), 'clips to make')
    await asyncio.gather(*jobs)
    print(len(list(out_dir.glob('*.wav'))), 'clips in', out_dir)


asyncio.run(main())
