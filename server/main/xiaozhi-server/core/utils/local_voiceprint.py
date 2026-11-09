"""[xiaozhi-hub] 家人声音（声纹，设计画布第 13 块）：在网关里用 sherpa-onnx 的说话人向量（3D-Speaker CAM++，
models/voiceprint/3dspeaker_campplus_zh-cn_16k.onnx，约 28MB，每 4 秒音频约 50ms）认出是家里的谁在说话。

上游的声纹识别要另外部署一个带数据库的服务（voiceprint-api）；这里不用。设备在设置页「家人声音」里
录：先说名字，再念三句（POST /kiosk/voiceprint/enroll，16 kHz 16 位单声道 PCM）。存在 data/voiceprints.json：
{设备: {名字: {"embs": [[192 维], ...], "child": bool}}}，只在这台网关上。

识别结果交给 ASR 基类（和上游的声纹服务同一个接口 identify_speaker），用户的话变成
{"speaker": "妈妈", "content": "…"}，系统提示里 <speaker_recognition> 那段规则告诉模型怎么用。
标成孩子的，名字后面带「（孩子）」，提示词里让模型像儿童模式那样说话。
"""
import io
import json
import os
import threading
import time
import wave

import numpy as np

from config.config_loader import get_project_dir
from config.logger import setup_logging

TAG = __name__
logger = setup_logging()

MODEL = os.path.join("models", "voiceprint", "3dspeaker_campplus_zh-cn_16k.onnx")
THRESHOLD = 0.55          # 余弦相似度，CAM++ 中文模型常用 0.5～0.6
MIN_SECONDS = 1.5
MAX_SAMPLES = 5           # 每个人最多存几段
MAX_PEOPLE = 8
_lock = threading.Lock()
_extractor = {"obj": None, "failed": False}


def _path():
    return os.path.join(get_project_dir(), "data", "voiceprints.json")


def model_path():
    return os.path.join(get_project_dir(), MODEL)


def available() -> bool:
    return os.path.isfile(model_path())


def _load():
    try:
        with open(_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data):
    path = _path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def _get_extractor():
    with _lock:
        if _extractor["obj"] is None and not _extractor["failed"]:
            try:
                import sherpa_onnx

                cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=model_path(), num_threads=2)
                _extractor["obj"] = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)
            except Exception as e:
                _extractor["failed"] = True
                logger.bind(tag=TAG).warning(f"声纹模型加载失败: {e}")
        return _extractor["obj"]


def embed(samples: np.ndarray) -> np.ndarray:
    """16 kHz float32 单声道 → 归一化的 192 维向量。"""
    ex = _get_extractor()
    if ex is None:
        raise RuntimeError("网关上没有声纹模型（models/voiceprint/）")
    st = ex.create_stream()
    st.accept_waveform(16000, samples.astype(np.float32))
    st.input_finished()
    v = np.asarray(ex.compute(st), dtype=np.float32)
    return v / (np.linalg.norm(v) + 1e-9)


def pcm16_to_float(pcm: bytes) -> np.ndarray:
    return np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0


def people(device: str) -> list:
    with _lock:
        d = _load().get(device) or {}
    return [{"name": n, "samples": len(v.get("embs") or []), "child": bool(v.get("child"))} for n, v in d.items()]


def enroll(device: str, name: str, pcm: bytes, child: bool = False) -> dict:
    name = str(name or "").strip()[:12]
    if not device or not name:
        raise ValueError("缺少设备或名字")
    x = pcm16_to_float(pcm)
    if len(x) < MIN_SECONDS * 16000:
        raise ValueError("这段太短了，按住多念几个字")
    if float(np.sqrt(np.mean(x ** 2))) < 0.003:
        raise ValueError("声音太小，靠近一点再念")
    v = embed(x)
    with _lock:
        data = _load()
        d = data.setdefault(device, {})
        if name not in d and len(d) >= MAX_PEOPLE:
            raise ValueError(f"最多 {MAX_PEOPLE} 个人")
        p = d.setdefault(name, {"embs": [], "child": bool(child)})
        p["embs"] = (p["embs"] + [[round(float(a), 5) for a in v]])[-MAX_SAMPLES:]
        p["child"] = bool(child) or p.get("child", False)
        p["updated"] = time.time()
        _save(data)
    return {"name": name, "samples": len(p["embs"])}


def remove(device: str, name: str):
    with _lock:
        data = _load()
        if (data.get(device) or {}).pop(name, None) is None:
            raise ValueError(f"没有「{name}」")
        _save(data)


def identify(device: str, samples: np.ndarray):
    """(名字, 相似度) 或 (None, 最高相似度)。"""
    with _lock:
        d = _load().get(device) or {}
    if not d or len(samples) < 0.8 * 16000:
        return None, 0.0
    v = embed(samples)
    best, score = None, -1.0
    for name, p in d.items():
        embs = np.asarray(p.get("embs") or [], dtype=np.float32)
        if not len(embs):
            continue
        s = float(np.max(embs @ v))
        if s > score:
            best, score = (f"{name}（孩子）" if p.get("child") else name), s
    return (best, score) if score >= THRESHOLD else (None, score)


class LocalVoiceprint:
    """给连接用：和上游 VoiceprintProvider 一样的 identify_speaker 接口。"""

    def __init__(self, device: str):
        self.device = device
        self.enabled = True

    async def identify_speaker(self, audio_data: bytes, session_id: str):
        import asyncio

        def run():
            with wave.open(io.BytesIO(audio_data)) as w:
                pcm = w.readframes(w.getnframes())
            return identify(self.device, pcm16_to_float(pcm))
        try:
            name, score = await asyncio.get_running_loop().run_in_executor(None, run)
        except Exception as e:
            logger.bind(tag=TAG).warning(f"声纹识别失败: {e}")
            return None
        logger.bind(tag=TAG).info(f"声纹：{name or '没认出'}（{score:.2f}）")
        return name


def for_device(device: str):
    """这台设备录过家人声音、网关上有模型时返回 LocalVoiceprint，否则 None。"""
    if not device or not available():
        return None
    with _lock:
        has = bool(_load().get(device))
    return LocalVoiceprint(device) if has else None
