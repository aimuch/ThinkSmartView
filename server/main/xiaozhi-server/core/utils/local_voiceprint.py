"""[xiaozhi-hub] 家人声音（声纹，设计画布第 13 块）：在网关里用 sherpa-onnx 的说话人向量（3D-Speaker CAM++，
models/voiceprint/3dspeaker_campplus_zh-cn_16k.onnx，约 28MB，每 4 秒音频约 50ms）认出是家里的谁在说话。

上游的声纹识别要另外部署一个带数据库的服务（voiceprint-api）；这里不用。设备在设置页「家人声音」里
录：先说名字，再念三句（POST /kiosk/voiceprint/enroll，16 kHz 16 位单声道 PCM）。存在 data/voiceprints.json：
{设备: {名字: {"embs": [[192 维], ...], "child": bool}}}，只在这台网关上。

全家共用（设计画布第 57 块）：在哪台设备录的都存在那台名下（删设备时一起删），但认人时看全家所有设备录的，
设备设置页列的也是全家的人；删一个人是从所有设备删掉。

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


def _merged(data: dict = None) -> dict:
    """全家的人：名字 → {"embs", "child", "devices"}（同名的合在一起）。"""
    out = {}
    for dev, d in (data if data is not None else _load()).items():
        if not isinstance(d, dict):
            continue
        for name, p in d.items():
            if not isinstance(p, dict):
                continue
            m = out.setdefault(name, {"embs": [], "child": False, "devices": []})
            m["embs"] += p.get("embs") or []
            m["child"] = m["child"] or bool(p.get("child"))
            m["devices"].append(dev)
    return out


def people(device: str = "") -> list:
    with _lock:
        m = _merged()
    return [{"name": n, "samples": len(v["embs"]), "child": v["child"], "devices": v["devices"], "here": device in v["devices"]}
            for n, v in m.items()]


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
        if name not in _merged(data) and len(_merged(data)) >= MAX_PEOPLE:
            raise ValueError(f"最多 {MAX_PEOPLE} 个人")
        d = data.setdefault(device, {})
        p = d.setdefault(name, {"embs": [], "child": bool(child)})
        p["embs"] = (p["embs"] + [[round(float(a), 5) for a in v]])[-MAX_SAMPLES:]
        p["child"] = bool(child) or p.get("child", False)
        p["updated"] = time.time()
        _save(data)
    return {"name": name, "samples": len(p["embs"])}


def remove(device: str, name: str):
    """从全家所有设备删掉这个人（device 只是从哪台发起的）。"""
    with _lock:
        data = _load()
        hit = [d.pop(name) for d in data.values() if isinstance(d, dict) and name in d]
        if not hit:
            raise ValueError(f"没有「{name}」")
        _save(data)


def forget(device: str) -> None:
    """删除设备时用：这台设备录的家人声音全删。"""
    with _lock:
        data = _load()
        if data.pop(device, None) is not None:
            _save(data)


def rank(samples: np.ndarray, min_seconds: float = 0.8) -> list:
    """全家每个人和这段声音有多像：[(名字, 相似度, 是不是孩子)]，从像到不像。太短（不到 min_seconds 秒）或没人返回 []。
    认人打招呼只有唤醒词那一点，用 0.5 秒。"""
    with _lock:
        m = _merged()
    if not m or len(samples) < min_seconds * 16000:
        return []
    v = embed(samples)
    out = []
    for name, p in m.items():
        embs = np.asarray(p["embs"], dtype=np.float32)
        if len(embs):
            out.append((name, float(np.max(embs @ v)), p["child"]))
    return sorted(out, key=lambda x: -x[1])


def identify(device: str, samples: np.ndarray):
    """(名字, 相似度) 或 (None, 最高相似度)。孩子的名字后面带「（孩子）」。认的是全家的人。"""
    r = rank(samples)
    if not r:
        return None, 0.0
    name, score, child = r[0]
    return ((f"{name}（孩子）" if child else name), score) if score >= THRESHOLD else (None, score)


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
    """家里录过家人声音（哪台录的都算）、网关上有模型时返回 LocalVoiceprint，否则 None。"""
    if not device or not available():
        return None
    with _lock:
        has = bool(_merged())
    return LocalVoiceprint(device) if has else None
