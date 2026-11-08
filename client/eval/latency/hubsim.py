"""Feed wavs through the hub's own VAD + ASR code (receive_audio) in 20 ms frames, as fast as possible.
Run from main/xiaozhi-server: python hubsim.py <model dir> <pause ms> <wav>..."""
import sys, asyncio, wave, time
from collections import deque
from types import SimpleNamespace
import numpy as np
sys.path.insert(0, '.')
import core.providers.asr.base as base
from core.providers.vad.silero import VADProvider
from core.providers.asr.sherpa_onnx_local import ASRProvider

model, pause, files = sys.argv[1], int(sys.argv[2]), sys.argv[3:]
vad = VADProvider({'model_dir': 'models/snakers4_silero-vad', 'threshold': 0.5, 'threshold_low': 0.3,
                   'min_silence_duration_ms': 800})
asr = ASRProvider({'model_dir': model, 'output_dir': '/tmp/hubsim', 'model_type': 'transducer', 'num_threads': 4,
                   'segment_pause_ms': pause, 'hotwords': open(model + '/hotwords.txt').read().replace('\n', ',')}, True)
result = {}


async def to_chat(conn, text):
    result['text'] = text
base.startToChat = to_chat
base.correct_and_record = lambda conn, t: t
base.enqueue_asr_report = lambda *a: None


async def run(path):
    w = wave.open(path)
    x = np.frombuffer(w.readframes(w.getnframes()), '<i2')
    x = np.concatenate([np.zeros(8000, '<i2'), x, np.zeros(24000, '<i2')])
    conn = SimpleNamespace(client_listen_mode='auto', asr_audio=[], client_have_voice=False, client_voice_stop=False,
                           asr=asr, features={}, websocket=None, session_id='s', vad_last_voice_time=0.0,
                           client_audio_buffer=bytearray(), client_voice_window=deque(maxlen=5), last_is_voice=False,
                           voiceprint_provider=None, client_is_speaking=False)
    conn.reset_audio_states = lambda: None
    result.clear()
    for i in range(0, len(x) - 320, 320):
        frame = x[i:i + 320].tobytes()
        have = vad.is_vad(conn, frame)
        await asr.receive_audio(conn, frame, have)
        await asyncio.sleep(0.02)   # realtime, so segments decode while "talking" like on the hub
        if conn.client_voice_stop:
            break
    return result.get('text'), len(getattr(conn, 'asr_segments', []) or [])

for f in files:
    t = time.time()
    text, n = asyncio.run(run(f))
    print(f'{f.split("/")[-1]}\t{n} segs\t{text}', flush=True)
