"""[xiaozhi-hub] 更多大模型厂商（本机服务可以不填 Key）和后台能选的其他语音识别服务（catalog.ASR_PROVIDERS）。"""
import pytest

from core.admin import catalog, config_store, pages
from core.agent import llm_router


def merged_with(path):
    return config_store.load_merged(custom_path=path)


@pytest.fixture
def cfg_path(tmp_path):
    p = tmp_path / ".config.yaml"
    p.write_text("")
    return p


def test_new_entries_dont_reuse_upstream_examples():
    """Upstream's config.yaml ships filled-in example entries (OllamaLLM, LMStudioLLM): a vendor reusing one of
    those names would show as configured, and its placeholder model would join the model list."""
    upstream = config_store.load_merged(custom_path=None).get("LLM") or {}
    for v in catalog.LLM_VENDORS:
        if v["id"] in ("siliconflow", "qianfan", "hunyuan", "minimax", "stepfun", "spark", "xai", "mistral",
                       "openrouter", "magpie", "ollama", "lmstudio"):
            assert v["entry"] not in upstream, v["entry"]


def test_vendor_ids_and_entries_are_unique():
    ids = [v["id"] for v in catalog.LLM_VENDORS]
    entries = [v["entry"] for v in catalog.LLM_VENDORS]
    assert len(ids) == len(set(ids)) and len(entries) == len(set(entries))
    assert {"siliconflow", "qianfan", "hunyuan", "minimax", "stepfun", "spark", "xai", "mistral", "openrouter",
            "magpie", "ollama", "lmstudio"} <= set(ids)
    for v in catalog.LLM_VENDORS:
        assert v["id"] == "custom" or v["base_url"].startswith(("http://", "https://")), v["id"]


def test_local_vendor_needs_no_key(cfg_path):
    merged = merged_with(cfg_path)
    pages.save_vendor("siliconflow", {"base_url": "https://api.siliconflow.cn/v1", "model": "Qwen/Qwen3-8B"},
                      merged, cfg_path)   # saving without a key is allowed; it just isn't ready
    merged = merged_with(cfg_path)
    assert not llm_router.model_ready(merged, "SiliconFlowLLM")
    pages.save_vendor("ollama", {"base_url": "http://127.0.0.1:11434/v1", "model": "qwen3:8b"}, merged, cfg_path)
    merged = merged_with(cfg_path)
    assert llm_router.model_ready(merged, "OllamaAPI")
    d = pages.describe_vendor(catalog.VENDOR_BY_ID["ollama"], merged)
    assert d["configured"] and d["key_optional"]
    assert pages.vendor_test_config("ollama", {"base_url": "http://127.0.0.1:11434/v1", "model": "qwen3:8b"}, merged)


def test_call_name_defaults_for_new_vendors(cfg_path):
    pages.save_vendor("hunyuan", {"base_url": "https://api.hunyuan.cloud.tencent.com/v1", "model": "hunyuan-turbos-latest",
                                  "api_key": "sk-test"}, merged_with(cfg_path), cfg_path)
    entries = {e["entry"]: e for e in pages.call_targets(merged_with(cfg_path))}
    assert entries["HunyuanLLM"]["defaults"] == ["混元"]


def test_asr_provider_catalog():
    ids = [p["id"] for p in catalog.ASR_PROVIDERS]
    assert len(ids) == len(set(ids)) and catalog.LOCAL_ASR_ENTRY not in catalog.ASR_PROVIDER_BY_ENTRY
    for p in catalog.ASR_PROVIDERS:
        assert p["group"] in ("local", "cloud", "lan")
        assert (p["group"] == "local") == bool(p.get("download")), p["id"]


def test_save_and_describe_cloud_asr(cfg_path):
    merged = merged_with(cfg_path)
    d = next(x for x in pages.describe_asr(merged)["providers"] if x["id"] == "tencent")
    assert not d["configured"] and [f["key"] for f in d["fields"]] == ["appid", "secret_id", "secret_key"]
    with pytest.raises(ValueError, match="SecretKey"):
        pages.save_asr_provider("tencent", {"appid": "123", "secret_id": "AKID1"}, merged, cfg_path)
    changed = pages.save_asr_provider("tencent", {"appid": "123", "secret_id": "AKID1", "secret_key": "s3cret"},
                                      merged, cfg_path)
    assert set(changed) >= {"appid", "secret_id", "secret_key"}
    merged = merged_with(cfg_path)
    d = next(x for x in pages.describe_asr(merged)["providers"] if x["id"] == "tencent")
    assert d["configured"] and d["exists"]
    secret = next(f for f in d["fields"] if f["key"] == "secret_key")
    assert "value" not in secret and secret["set"]          # secrets are never sent back
    # a blank secret keeps the saved one
    assert pages.save_asr_provider("tencent", {"appid": "456", "secret_id": "AKID1", "secret_key": ""},
                                   merged, cfg_path) == ["appid"]
    assert "s3cret" in cfg_path.read_text()


def test_defaults_fill_non_secret_fields(cfg_path):
    pages.save_asr_provider("groq", {"api_key": "gsk_test"}, merged_with(cfg_path), cfg_path)
    entry = merged_with(cfg_path)["ASR"]["GroqASR"]
    assert entry["model_name"] == "whisper-large-v3-turbo" and entry["base_url"].startswith("https://api.groq.com")


def test_choice_follows_selected_entry(cfg_path):
    pages.save_asr_provider("qwen3flash", {"api_key": "sk-x"}, merged_with(cfg_path), cfg_path)
    merged = merged_with(cfg_path)
    merged["selected_module"]["ASR"] = "Qwen3ASRFlash"
    assert pages.asr_choice(merged) == "qwen3flash"


def test_local_model_needs_download(cfg_path):
    merged = merged_with(cfg_path)
    d = next(x for x in pages.describe_asr(merged)["providers"] if x["id"] == "sensevoice")
    assert d["model"] is not None and d["download"]["state"] == "idle"
    model_dir, repo, files = pages.download_target("sensevoice")
    assert repo.endswith("sense-voice-zh-en-ja-ko-yue-2024-07-17") and files == ["model.int8.onnx", "tokens.txt"]
    assert pages.download_target("xasr:full")[0].endswith("punct-2026-06-03")
    with pytest.raises(KeyError):
        pages.download_target("tencent")


def test_x_asr_variant_requires_files(cfg_path, tmp_path, monkeypatch):
    merged = merged_with(cfg_path)
    variants = pages.describe_asr(merged)["local"]["variants"]
    assert [v["value"] for v in variants] == ["fast", "full"]
    with pytest.raises(ValueError):
        pages.save_x_asr_variant("huge", merged, cfg_path)


def test_local_model_is_usable_without_a_form(cfg_path, monkeypatch):
    monkeypatch.setattr(pages, "asr_model_check", lambda e: {"ok": True, "detail": "ok"})
    d = next(x for x in pages.describe_asr(merged_with(cfg_path))["providers"] if x["id"] == "qwen3asr")
    assert not d["exists"] and d["configured"] == (not d["missing"])
    pages.save_asr_provider("qwen3asr", {}, merged_with(cfg_path), cfg_path)
    entry = merged_with(cfg_path)["ASR"]["SherpaQwen3ASR"]
    assert entry["model_type"] == "qwen3_asr" and entry["model_dir"].endswith("qwen3-asr-0.6B-int8-2026-03-25")
