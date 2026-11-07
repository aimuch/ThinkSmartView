"""Render one piece as the device's ring tone file: mono Ogg Opus at 64 kbit/s (ffmpeg with libopus):
a tenth of a WAV, and no difference on the device's small speaker.
  SCORES=scores2 uv run --no-project --with numpy --with scipy --with soxr python export.py <sfz> <piece prefix> <out.ogg>"""
import subprocess
import sys
import tempfile
import wave

import numpy as np

import render

piano = render.Piano(sys.argv[1])
name, fn = next(p for p in render.scores.PIECES + getattr(render.scores, 'CHIMES', []) if p[0].startswith(sys.argv[2]))
x = render.render(piano, fn).mean(axis=1)
pcm = (np.clip(x, -1, 1) * 32767).astype(np.int16)
with tempfile.NamedTemporaryFile(suffix='.wav') as tmp:
    with wave.open(tmp.name, 'wb') as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(render.SR), w.writeframes(pcm.tobytes())
    subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-i', tmp.name, '-c:a', 'libopus', '-b:a', '64k',
                    '-application', 'audio', '-map_metadata', '-1', sys.argv[3]], check=True)
print(name, f'{len(x) / render.SR:.1f} s')
