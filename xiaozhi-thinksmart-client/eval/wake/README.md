# 唤醒评测

量两件事：**漏唤醒率**（说了唤醒词没反应）和**误唤醒率**（没叫它却醒了）。每次改唤醒相关的参数或代码，都先跑一遍，用数据说话。

评测直接调用客户端自己的 `thinksmart_client/kws.py`（同样的 100 ms 分批、静音门控、2 秒不应期，时钟跟着音频走）和 `wake_verify.py`，所以结果就是设备拿到同样声音时的行为。

## 数据

音频太大，不进 git，放在 `$WAKE_EVAL_DATA`（默认 `~/thinksmart-wake-eval`）：

| 目录 | 内容 | 怎么来 |
| --- | --- | --- |
| `tts/` | `sentences.tsv` 里每句话 × 19 种声音（唤醒词另有快慢三档），约 2800 个 16 kHz 单声道 wav，约 1.8 小时 | `uv run --no-project --with edge-tts --with numpy python eval/wake/gen_tts.py`（微软 edge-tts 10 种中文声音要联网；macOS 自带 9 种中文声音离线），约 20 分钟，已有的文件跳过 |
| `bg/device-ambient.wav` | 设备麦克风录的 60 秒房间底噪（约 −57 dBFS），垫在所有合成声音下面 | 设备上 `pw-record --rate 16000 --channels 1 --format s16` 录 60 秒拷回 |
| `bg/news1.wav` `news2.wav` `variety.wav` `drama.wav` | 真实中文节目（新闻联播两期、综艺《了不起的挑战》第 1 期前 60 分钟、电视剧《陈真》1982 第 1 集），约 2.8 小时（播客那段下载失败，没用上）。有就多一个 program 条件（唤醒词叠在节目上），并整段当负样本算每小时误唤醒 | 2026-10-31 用 yt-dlp 从 YouTube 下的音频（视频 ID 见下），只在本机评测用，**不进 git、不上传、不进任何发布物**，评测完可删 |
| `bg/music1.wav` | 华语慢情歌合集前 45 分钟（`music*` 开头的文件），有就多一个 music 条件，也当负样本 | 同上 |
| `real/` | 设备麦克风录的真人声音 | `eval/wake/record_real.sh <名字> <秒数>`，见下面「真人录音」 |
| `models/` | 二次确认在 Mac 上评测用的识别模型（网关的 X-ASR，从设备拷） | `thinksmart-ubuntu-image/ts.sh 'cd ~/xiaozhi-hub-server/main/xiaozhi-server/models && tar cf - sherpa-onnx-x-asr-*' \| tar xf - -C ~/thinksmart-wake-eval/models` |

下载命令（在国内需要代理时加 `--proxy http://<代理地址>`）：`yt-dlp -f bestaudio -o NAME.%(ext)s https://www.youtube.com/watch?v=ID`，再 `ffmpeg -i 文件 [-t 秒数] -ar 16000 -ac 1 -sample_fmt s16 bg/NAME.wav`。ID：news1 `jvXVcN_DMds`、news2 `FhojC1FgQFc`、variety `uUUZ-HWkir8`（前 3600 秒）、drama `ohPSxkAmGRo`、podcast `VWbjidLXVt4`、music1 `B0pBHPBCX0k`（前 2700 秒）。

`sentences.tsv`：`pos` 是唤醒词（单说、和问题连着说），`conf` 是听着像唤醒词的句子（小妹小妹、小白同学、小卖部……），`talk` 是 100 句日常说话和电视台词。

## 跑

```bash
cd thinksmart-client
# 不带参数 = 客户端 config.py 里的默认配置
uv run --no-project --with sherpa-onnx==2.0.1 --with numpy python eval/wake/run.py
# 2026-10-31 之前的配置（无增益、门控拖尾 0.5 秒）
uv run --no-project --with sherpa-onnx==2.0.1 --with numpy python eval/wake/run.py --gain 0 --hangover 5
# 加二次确认（用网关同款 X-ASR 在 Mac 上模拟）
uv run --no-project --with sherpa-onnx==2.0.1 --with numpy python eval/wake/run.py \
    --verify asr:$HOME/thinksmart-wake-eval/models/sherpa-onnx-x-asr-zipformer-transducer-zh-en-punct-int8-2026-06-03
```

`device_bench.py` 在设备上量唤醒模型 CPU 和网关确认的往返耗时（用法见文件头）。

不带二次确认约 2 分钟，带上约 5 分钟（M1，8 进程）。sherpa-onnx 版本和设备一致（2.0.1）。常用参数：`--score/--threshold/--paths/--gain/--hangover` 改第一级，`--verify` 加第二级，`--only near,far,neg,real` 只跑一部分，`--out x.json` 存明细（`summary.py x.json` 重新打印表格），`--dump 目录` 存被第二级否掉的音频。

条件（语音电平是有声部分的 RMS）：

| 条件 | 模拟 |
| --- | --- |
| loud / near / mid / low | −20 / −30 / −40 / −48 dBFS，无混响。客户端把 −38 dBFS 以上当作「在说话」，近处说话大约 −30 |
| far | −42 dBFS + 房间混响（RT60 0.5 秒，直达声比混响低 3 dB），隔着房间 |
| soft | −48 dBFS + 混响，又远又轻，刚过静音门控（−50） |
| tv | −30 dBFS，同时电视在 −36 dBFS 说别的话（别的句子、别的声音、带混响） |
| program / music | −30 dBFS，背景是下载的真实节目 / 音乐，整体 RMS −36 dBFS |
| 负样本 | conf 和 talk 全部声音首尾相接，当作电视/房间里的说话放（−33 dBFS + 混响）。conf 报「易混句误唤醒比例」，talk 报「每小时误唤醒次数」；下载的节目和音乐整段按 −33 dBFS 放，报每小时误唤醒 |

**合成声和真人有差距**：macOS 的几种声音（Eddy、Grandma 等）发音很怪，漏得多；混响是白噪声衰减模型，比真房间刺耳。绝对数字只作参考，**同一套数据前后对比**才是主要用途。真人录音补上后以真人数据为准。

## 真人录音（要用户配合）

录音时客户端会先停掉（不然它会应答、提示音录进去），录完自动启动。要先拿设备锁。文件名 `pos-…-n<次数>` 表示唤醒词说了几次，评测数醒了几次；`neg-…` 是不该醒的，评测算每小时误唤醒。

| 文件名 | 怎么录 | 时长 |
| --- | --- | --- |
| `pos-xmxm-1m-n10` | 离设备 1 米，正常音量，每隔 4 秒说一次「小麦小麦」，共 10 次 | 45 秒 |
| `pos-hlxm-1m-n10` | 同上，「哈喽小麦」 | 45 秒 |
| `pos-xmtx-1m-n10` | 同上，「小麦同学」 | 45 秒 |
| `pos-mix-3m-n12` | 站到房间另一头（约 3 米），三个唤醒词轮流各 4 次 | 55 秒 |
| `pos-mix-soft-n12` | 1 米，小声（像怕吵醒人那样），三个轮流各 4 次 | 55 秒 |
| `pos-mix-tv-n12` | 电视或手机外放新闻/综艺（正常看电视的音量），1–2 米处三个轮流各 4 次 | 55 秒 |
| `pos-mix-question-n9` | 1 米，唤醒词和问题一口气说，比如「小麦小麦今天天气怎么样」，三个轮流各 3 次 | 60 秒 |
| `neg-confusable` | 1 米，正常说下面这些句子各两遍：小妹小妹等等我、小白同学作业交了吗、小美同学今天没来、小卖部的阿姨人很好、你好小妹妹、晓梅快来吃饭、小爱同学播放音乐、哈喽小米打开电视 | 90 秒 |
| `neg-tv-30min` | 电视/播客正常音量放 30 分钟，人不用在场（可以多录几段：新闻、综艺、电视剧） | 30 分钟 |

```bash
TS_HOST=<设备IP> eval/wake/record_real.sh pos-xmxm-1m-n10 45
```

## 结果（2026-10-31，合成声）

漏唤醒率（所有唤醒词、所有声音合计 336 段）和误唤醒：

| 配置 | loud −20 | near −30 | mid −40 | low −48 | far | soft | tv | 易混句误唤醒（513 句） | 日常说话误唤醒（1.46 小时） |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 旧：无增益、拖尾 0.5 秒 | 7% | 15% | 16% | 26% | 39% | 51% | 30% | 29 句（5.7%） | 2 次（1.4 次/时） |
| 现默认：增益 10 dB、拖尾 1 秒 | 4% | 7% | 10% | 20% | 32% | 46% | 27% | 37 句（7.2%） | 2 次（1.4 次/时） |
| 增益 20 dB、拖尾 1 秒 | 4% | 7% | 10% | 17% | 33% | 44% | 25% | 39 句（7.6%） | 2 次 |
| 现默认 + 网关二次确认 | 5% | 8% | 12% | 22% | 38% | 48% | 37% | 9 句（1.8%） | 0 次 |

漏得最多的是「哈喽小麦」（near 下单说 50% → 12%，大部分是 macOS 的怪声音）。易混句里误唤醒集中在：小妹小妹（醒成小麦小麦）、小白同学 / 小卖部的阿姨 / 小爱同学（醒成小麦同学）。

真实节目（3.6 小时：新闻 2 期、综艺、电视剧、歌曲，整体 −33 dBFS 当电视放）：旧配置和现默认配置都是 **0 次误唤醒**（第一级一次都没命中）。

唤醒词叠在真实背景上（背景比人声低 6 dB）：旧 → 现默认，节目 33% → 35%，音乐 21% → 20%，差别在误差范围内：增益和拖尾帮的是「声音小、距离远」，帮不了「被电视盖住」。

二次确认换成客户端本地小模型（2026-10-31 从 k2-fsa 发布页下载）的结果，都比网关 X-ASR 差：

| 确认用的模型 | 被误挡的真唤醒（共 7×336 段里第一级命中的） | 易混句误唤醒 | 设备上确认耗时（2 线程） | 客户端进程内存 |
| --- | --- | --- | --- | --- |
| 网关 X-ASR | 72 | 9 | 2.1–2.5 秒（经网关） | 不增加 |
| paraformer-zh-small | 365 | 3 | 0.84 秒 | +137 MB |
| streaming-zipformer-small-ctc | 253 | 4 | 1.6 秒 | +76 MB |
| zipformer-ctc-small | 140 | 11 | 1.4 秒 | +206 MB |
