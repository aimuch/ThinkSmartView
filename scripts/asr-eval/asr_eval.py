"""Compare local ASR models on synthetic household sentences (clean + far-field-ish), on the hub server.
Run with the hub's venv from ~/xiaozhi-hub-server/main/xiaozhi-server:
  .venv/bin/python ~/asr-eval/asr_eval.py
Audio stays in ~/asr-eval (deleted at the end unless KEEP=1)."""
import asyncio
import os
import re
import shutil
import subprocess
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, '.')
import numpy as np  # noqa: E402

OUT = Path.home() / 'asr-eval' / 'wav'
SENTENCES = [
    '扇贝清蒸多久比较好', '清蒸扇贝怎么做', '当前音量是多少', '把音量调大一点', '小麦小麦今天上海天气怎么样',
    '打开书房的灯', '把客厅的空调调到二十六度', '关掉主卧吸顶灯', '冬瓜排骨汤怎么做', '三岁的宝宝推荐吃什么',
    '南京六朝古都是哪六朝', '历史上的今天发生了什么', '明天早上七点叫我起床', '十分钟后提醒我关火', '帮我把阳台的灯打开',
    '厨房现在的温度是多少', '卫生间有没有漏水', '可乐鸡翅需要哪些调料', '红烧肉炖多长时间', '番茄炒蛋先放番茄还是先放鸡蛋',
    '帮我查一下明天会不会下雨', '给我讲一个关于恐龙的故事', '苹果和香蕉哪个热量高', '把电梯厅的灯带关掉',
    '走廊的人在传感器检测到人了吗', '设置一个二十分钟的倒计时', '清除聊天记录', '今天星期几', '鲈鱼蒸几分钟', '虾仁滑蛋怎么做嫩',
]
VOICES = ['zh-CN-XiaoxiaoNeural', 'zh-CN-YunxiNeural', 'zh-CN-XiaoyiNeural', 'zh-CN-YunjianNeural']
MODELS = {
    'X-ASR int8（现在）': {'model_dir': 'models/sherpa-onnx-x-asr-zipformer-transducer-zh-en-punct-int8-2026-06-03',
                         'model_type': 'transducer'},
    'X-ASR fp32': {'model_dir': 'models/sherpa-onnx-x-asr-zipformer-transducer-zh-en-punct-2026-06-03',
                   'model_type': 'transducer'},
    'Qwen3-ASR 0.6B int8': {'model_dir': 'models/sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25', 'model_type': 'qwen3_asr'},
}


async def synth():
    import edge_tts
    OUT.mkdir(parents=True, exist_ok=True)
    items = []
    for i, text in enumerate(SENTENCES):
        voice = VOICES[i % len(VOICES)]
        mp3 = OUT / f'{i:02d}.mp3'
        if not mp3.exists():
            await edge_tts.Communicate(text, voice).save(str(mp3))
        clean, far = OUT / f'{i:02d}-clean.wav', OUT / f'{i:02d}-far.wav'
        subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-i', str(mp3), '-ac', '1', '-ar', '16000', str(clean)], check=True)
        # far-field-ish: quieter, a short room echo, steady noise ~15 dB below the speech
        subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-i', str(clean), '-f', 'lavfi', '-i', 'anoisesrc=c=pink:a=0.02:r=16000',
                        '-filter_complex', '[0]aecho=0.8:0.6:40|70:0.35|0.2,volume=0.35[s];[s][1]amix=inputs=2:duration=first:normalize=0',
                        '-ac', '1', '-ar', '16000', str(far)], check=True)
        items.append((text, clean, far))
    return items


def pcm(path):
    with wave.open(str(path)) as w:
        return w.readframes(w.getnframes())


def norm(t):
    return re.sub(r'[\s，。？！、,.?!：:；;“”"\'（）()]', '', t)


def cer(ref, hyp):
    r, h = norm(ref), norm(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
    return d[len(h)], len(r)


def main():
    from core.providers.asr.sherpa_onnx_local import ASRProvider
    items = asyncio.run(synth())
    report = {}
    for name, cfg in MODELS.items():
        if not Path(cfg['model_dir']).is_dir():
            print(f'skip {name}: no {cfg["model_dir"]}')
            continue
        t0 = time.time()
        asr = ASRProvider({'type': 'sherpa_onnx_local', 'output_dir': 'tmp/', 'num_threads': 4, **cfg}, True)
        load = time.time() - t0
        for cond in ('clean', 'far'):
            errs = chars = 0
            times, wrong = [], []
            for text, clean, far in items:
                audio = pcm(clean if cond == 'clean' else far)
                t = time.time()
                hyp = asr.recognize_pcm(audio)
                times.append(time.time() - t)
                e, n = cer(text, hyp)
                errs, chars = errs + e, chars + n
                if e:
                    wrong.append(f'{text} → {hyp}')
            report[(name, cond)] = (errs / chars, np.mean(times), max(times), wrong)
        print(f'{name}: loaded in {load:.1f}s', flush=True)
    print()
    print(f'{"model":<22}{"cond":<7}{"CER":>7}{"avg s":>8}{"max s":>8}')
    for (name, cond), (c, avg, mx, _) in report.items():
        print(f'{name:<22}{cond:<7}{c * 100:>6.1f}%{avg:>8.2f}{mx:>8.2f}')
    for (name, cond), (_, _, _, wrong) in report.items():
        print(f'\n== {name} / {cond}: {len(wrong)} 句有错')
        for w in wrong:
            print('   ', w)
    if not os.environ.get('KEEP'):
        shutil.rmtree(OUT.parent / 'wav', ignore_errors=True)


if __name__ == '__main__':
    main()
