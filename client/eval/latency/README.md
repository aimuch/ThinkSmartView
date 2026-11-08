# 说完到上屏的延迟

2026-11-01 测「用户说完 → 页面显示用户的话」用的脚本，数据和结论见项目 `.agents/docs/sessions/识别到上屏的延迟.md`。
测试句子用 macOS `say -v Tingting` 生成，转成 16 kHz 单声道 wav（`afconvert -f WAVE -d LEI16@16000 -c 1`）。

| 脚本 | 在哪跑 | 量什么 |
| --- | --- | --- |
| `harness.py` | 设备，客户端的 venv | 模拟客户端把录音按实时速度发给网关：说完 → 文字 / 回复开始 / 首句 / 首帧音频（不出声，不经过客户端） |
| `e2e.sh` + `sse.py` | 设备 | 真客户端端到端：先建两个 PipeWire 空设备 `lat_mic`、`lat_spk`（`pactl load-module module-null-sink sink_name=...`），把客户端的录音流挪到 `lat_mic.monitor`、播放流挪到 `lat_spk`（`pactl move-source-output/move-sink-input`），再用 `/api/wake` 唤醒、`paplay -d lat_mic` 放录音（文件名前加 `t_`，只留有声部分）；`sse.py` 记页面收到的事件 |
| `analyze.py` | Mac | 合并 `runs.log`、`sse.log`、客户端日志（`journalctl -o short-unix`）和网关的毫秒计时日志，出每段耗时（网关计时日志要临时加，见交付文档） |
| `asrbench.py` | 设备，网关的 venv，**先停网关**腾内存 | X-ASR 在不同线程数、贪心/束搜索下的解码耗时 |
| `seg.py` + `sents.txt` | Mac（模型从设备拷） | 按停顿切段识别和整句识别的准确率对比（能量判停顿，离线估算） |
| `hubsim.py` + `cer.py` | Mac，网关仓库 `main/xiaozhi-server` 目录下用网关的 venv 跑 | 用网关自己的 Silero VAD 和识别代码（`receive_audio`）按实时速度喂录音，看边说边识别实际怎么切段、拼出来的字；`cer.py` 算字错率。模型目录里要放 `hotwords.txt` |

测完要把客户端的流挪回去、卸掉两个空设备（重启客户端前先卸，PipeWire 会记住上次接的设备）。
