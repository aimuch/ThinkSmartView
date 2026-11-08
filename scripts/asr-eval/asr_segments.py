"""Same words alone (what a 300 ms segment gives the recogniser) vs inside the whole sentence."""
import asyncio
import subprocess
import sys
import wave
from pathlib import Path

sys.path.insert(0, '.')
OUT = Path.home() / 'asr-eval' / 'seg'
PAIRS = [('扇贝', '扇贝清蒸多久'), ('清蒸多久', '扇贝清蒸多久'), ('音量', '当前音量是多少'), ('关火', '十分钟后提醒我关火'),
         ('鲈鱼', '鲈鱼蒸几分钟'), ('吸顶灯', '关掉主卧吸顶灯'), ('滑蛋', '虾仁滑蛋怎么做'), ('冬瓜', '冬瓜排骨汤怎么做')]
VOICES = ['zh-CN-XiaoxiaoNeural', 'zh-CN-YunxiNeural', 'zh-CN-YunjianNeural']


async def synth(text, voice, name):
    import edge_tts
    mp3, wav = OUT / f'{name}.mp3', OUT / f'{name}.wav'
    await edge_tts.Communicate(text, voice).save(str(mp3))
    subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-i', str(mp3), '-f', 'lavfi', '-i', 'anoisesrc=c=pink:a=0.02:r=16000',
                    '-filter_complex', '[0]aresample=16000,aecho=0.8:0.6:40|70:0.35|0.2,volume=0.35[s];[s][1]amix=inputs=2:duration=first:normalize=0',
                    '-ac', '1', '-ar', '16000', str(wav)], check=True)
    with wave.open(str(wav)) as w:
        return w.readframes(w.getnframes())


def main():
    from core.providers.asr.sherpa_onnx_local import ASRProvider
    OUT.mkdir(parents=True, exist_ok=True)
    asr = {name: ASRProvider({'type': 'sherpa_onnx_local', 'output_dir': 'tmp/', 'num_threads': 4, 'model_type': 'transducer',
                              'model_dir': d}, True)
           for name, d in (('int8', 'models/sherpa-onnx-x-asr-zipformer-transducer-zh-en-punct-int8-2026-06-03'),
                           ('fp32', 'models/sherpa-onnx-x-asr-zipformer-transducer-zh-en-punct-2026-06-03'))}
    alone_ok = whole_ok = n = 0
    for i, (word, sentence) in enumerate(PAIRS):
        for j, voice in enumerate(VOICES):
            a = asyncio.run(synth(word, voice, f'w{i}{j}'))
            s = asyncio.run(synth(sentence, voice, f's{i}{j}'))
            for name, m in asr.items():
                ra, rs = m.recognize_pcm(a), m.recognize_pcm(s)
                n += 1
                alone_ok += word in ra
                whole_ok += word in rs
                if word not in ra or word not in rs:
                    print(f'{name} {voice[6:-6]:<8} 单独「{word}」→ {ra:<10} 整句 → {rs}')
    print(f'\n单独识别对 {alone_ok}/{n}，整句里对 {whole_ok}/{n}')
    import shutil
    shutil.rmtree(OUT, ignore_errors=True)


main()
