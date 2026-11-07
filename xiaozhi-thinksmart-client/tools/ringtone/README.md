# 钢琴铃声

`web/sounds/alarm-*.ogg` 是用这里的脚本渲染的（Opus 64 kbit/s，ffmpeg 编码）：`alarm-coffee`（7 咖啡时光，默认）、`alarm-morning-light`（1 晨曦）、`alarm-bright-start`（3 元气早晨）、`alarm-sunny-hop`（9 阳光小跳跃），都是原创曲；`alarm-chime` 是提醒用的短和弦（`scores2.py` 的 `CHIMES`）。

- 采样：[Salamander Grand Piano V3](https://freepats.zenvoid.org/Piano/acoustic-grand-piano.html)（雅马哈 C5，Alexander Holm），CC BY 3.0。下载 `SalamanderGrandPianoV3+20161209_44khz16bit.tar.xz`（412 MB），解压到仓库外，不进仓库。
- `scores.py`、`scores2.py`：乐谱（每个音：拍、音高、时值、力度）。原创曲之外的几首是作曲版权已过期的古典曲片段。
- `render.py`：小 SFZ 采样器（按音高和力度选样本、变调、制音器淡出、混响、统一响度），渲染一批试听用的 48 kHz 立体声文件。
- `export.py`：把一首渲染成设备用的铃声文件（单声道 Ogg Opus 64 kbit/s，约是 WAV 的十分之一，每次在线更新少下载约 3 MB）。

```bash
SCORES=scores2 uv run --no-project --with numpy --with scipy --with soxr python tools/ringtone/export.py <SalamanderGrandPianoV3.sfz> 7- web/sounds/alarm-coffee.ogg
```
