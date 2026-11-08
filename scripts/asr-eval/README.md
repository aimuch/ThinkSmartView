# 语音识别模型对比

在网关服务器上用网关自己的 Python 跑（合成语音只在服务器上，跑完删掉）：

```bash
scp scripts/asr-eval/*.py andy@10.10.10.224:asr-eval/
ssh andy@10.10.10.224 'cd ~/xiaozhi-hub-server/main/xiaozhi-server && nice -n 10 .venv/bin/python ~/asr-eval/asr_eval.py'
```

- `asr_eval.py`：30 句家常话（Edge TTS 4 种音色），干净和「远场」（音量小、回声、噪声）两种条件，比较各模型的字错率和耗时。模型目录要先下载到 `models/`。
- `asr_segments.py`：同一个词单独识别和放在整句里识别的对比。

2026-11-08 的结果：合成语音上 X-ASR int8 / fp32 / Qwen3-ASR 0.6B 的字错率都在 1–3%，Qwen3-ASR 慢 6 倍；
单独识别短词只对 18/48，整句里对 29/48。所以网关改成整句识别（`segment_pause_ms: 0`，服务器上整句只要约 0.1 秒），
并换成 X-ASR fp32（远场略好）。改在服务器的 `data/.config.yaml`（`ASR.SherpaXASR.model_dir`、`segment_pause_ms`），
原来的配置备份在 `data/.config.yaml.bak-asr`。热词对这个模型的测试集没有改善。
