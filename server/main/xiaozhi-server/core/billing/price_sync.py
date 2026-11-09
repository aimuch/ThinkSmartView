"""[xiaozhi-hub] 单价同步（设计画布第 20 块 D，做法参考 seakee/CPA-Manager-Plus 的模型价格，MIT）。

只在后台点「同步单价」时联网，依次查 models.dev、LiteLLM、OpenRouter：
- 先按模型条目或模型名认出它是哪家的（DeepSeek、OpenAI、Anthropic、阿里、月之暗面……），只在这家「官方」名下找同名模型；
  同一个模型名在 models.dev 里常挂在几十家转售商下面、价格各不相同，不能随便取一个；
- 一个来源里官方名下有唯一的同名模型才算数，按来源顺序取第一个；
- 三个来源都没有官方条目时，把各来源里同名的候选（去重后最多 6 个）放进「待确认」，点「用这个」才生效。
已经核对过的（DeepSeek 默认规则）和手动加的单价一律不动；同步只补没有单价的模型，或者更新上次同步来的。
来源给的都是美元：同步来的规则用美元，费用按美元单独统计，不换算。
"""

import fnmatch
import time

SOURCES = ("models.dev", "LiteLLM", "OpenRouter")
URLS = {
    "models.dev": "https://models.dev/api.json",
    "LiteLLM": "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json",
    "OpenRouter": "https://openrouter.ai/api/v1/models",
}

# 厂商 -> 各来源里的官方名（models.dev 的 provider 键、LiteLLM 的 litellm_provider、OpenRouter id 的前缀）
OFFICIAL = {
    "deepseek": (["deepseek"], ["deepseek"], ["deepseek"]),
    "openai": (["openai"], ["openai"], ["openai"]),
    "claude": (["anthropic"], ["anthropic"], ["anthropic"]),
    "gemini": (["google"], ["gemini"], ["google"]),
    "qwen": (["alibaba-cn", "alibaba"], ["dashscope"], ["qwen"]),
    "kimi": (["moonshotai-cn", "moonshotai"], ["moonshot"], ["moonshotai", "~moonshotai"]),
    "glm": (["zhipuai", "zai"], ["zai", "zhipuai"], ["z-ai"]),
    "xai": (["xai"], ["xai"], ["x-ai"]),
    "mistral": (["mistral"], ["mistral"], ["mistralai"]),
    "minimax": (["minimax-cn", "minimax"], ["minimax"], ["minimax"]),
    "stepfun": (["stepfun"], ["stepfun"], ["stepfun"]),
    "doubao": (["volcengine"], ["volcengine"], ["bytedance"]),
}
# 条目名 -> 厂商（catalog 的 LLM 厂商条目，加上看图模型和 DSH）
ENTRY_VENDOR = {"DeepSeekLLM": "deepseek", "DshCLI": "deepseek", "DeepSeekVLLM": "deepseek", "OpenAILLM": "openai",
                "OpenAIVLLM": "openai", "ClaudeAPI": "claude", "ClaudeVLLM": "claude", "GeminiAPI": "gemini",
                "GeminiVLLM": "gemini", "AliLLM": "qwen", "QwenVLVLLM": "qwen", "KimiLLM": "kimi", "KimiVLLM": "kimi",
                "ChatGLMLLM": "glm", "ChatGLMVLLM": "glm", "XaiLLM": "xai", "MistralLLM": "mistral",
                "MiniMaxLLM": "minimax", "StepFunLLM": "stepfun", "StepFunVLLM": "stepfun", "DoubaoLLM": "doubao",
                "DoubaoVLLM": "doubao"}
NAME_VENDOR = [("deepseek", "deepseek"), ("gpt-", "openai"), ("o1", "openai"), ("o3", "openai"), ("o4", "openai"),
               ("claude", "claude"), ("gemini", "gemini"), ("qwen", "qwen"), ("kimi", "kimi"), ("moonshot", "kimi"),
               ("glm", "glm"), ("grok", "xai"), ("mistral", "mistral"), ("minimax", "minimax"), ("step-", "stepfun"),
               ("doubao", "doubao")]


def vendor_of(model: str, entry: str = "") -> str:
    if entry in ENTRY_VENDOR:
        return ENTRY_VENDOR[entry]
    m = (model or "").lower().split("/")[-1]
    return next((v for prefix, v in NAME_VENDOR if m.startswith(prefix)), "")


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _md_entries(data, model):
    """models.dev：[(provider, {input, cache_hit, output} 每百万美元)]"""
    out = []
    for prov, v in (data or {}).items():
        for mid, m in ((v or {}).get("models") or {}).items():
            c = (m or {}).get("cost") or {}
            if mid.lower() == model and _num(c.get("input")) is not None and _num(c.get("output")) is not None:
                out.append((prov, {"input": _num(c["input"]), "cache_hit": _num(c.get("cache_read")) if c.get("cache_read") is not None else _num(c["input"]),
                                   "output": _num(c["output"])}))
    return out


def _ll_entries(data, model):
    out = []
    for key, v in (data or {}).items():
        if not isinstance(v, dict) or key.lower().split("/")[-1] != model:
            continue
        i, o = _num(v.get("input_cost_per_token")), _num(v.get("output_cost_per_token"))
        if i is None or o is None:
            continue
        c = _num(v.get("cache_read_input_token_cost"))
        out.append((str(v.get("litellm_provider") or key.split("/")[0]),
                    {"input": i * 1e6, "cache_hit": (c if c is not None else i) * 1e6, "output": o * 1e6}))
    return out


def _or_entries(data, model):
    out = []
    for m in (data or {}).get("data") or []:
        mid = str(m.get("id") or "")
        if mid.lower().split("/")[-1] != model:
            continue
        p = m.get("pricing") or {}
        i, o = _num(p.get("prompt")), _num(p.get("completion"))
        if i is None or o is None or i < 0 or o < 0:
            continue
        c = _num(p.get("input_cache_read"))
        out.append((mid.split("/")[0], {"input": i * 1e6, "cache_hit": (c if c is not None else i) * 1e6, "output": o * 1e6}))
    return out


READERS = {"models.dev": _md_entries, "LiteLLM": _ll_entries, "OpenRouter": _or_entries}


def _round(p):
    return {k: round(v, 6) for k, v in p.items()}


def match(model: str, entry: str, sources: dict):
    """(选中的 {source, provider, prices} 或 None, 候选列表)。sources: {来源名: 下载下来的 JSON}（失败的来源不在里面）。"""
    model_l = (model or "").lower().split("/")[-1]
    vendor = vendor_of(model, entry)
    candidates = []
    for i, name in enumerate(SOURCES):
        if name not in sources:
            continue
        entries = READERS[name](sources[name], model_l)
        official = OFFICIAL.get(vendor, ([], [], []))[i]
        hits = [(prov, p) for prov, p in entries if prov in official]
        if hits:
            hits.sort(key=lambda h: official.index(h[0]))
            prov, p = hits[0]
            return {"source": name, "provider": prov, "prices": _round(p)}, []
        for prov, p in entries:
            cand = {"source": name, "provider": prov, "prices": _round(p)}
            if not any(c["prices"] == cand["prices"] for c in candidates):
                candidates.append(cand)
    return None, candidates[:6]


def rule_for(model: str, found: dict) -> dict:
    return {"match": model, "label": model, "currency": "USD", **found["prices"], "source": found["source"],
            "provider": found["provider"], "synced_at": time.strftime("%Y-%m-%d %H:%M")}


def _covered(rules, model):
    m = model.lower()
    for r in rules:
        if any(fnmatch.fnmatchcase(m, pat.strip().lower()) for pat in str(r.get("match") or "").split(",") if pat.strip()):
            return r
    return None


def plan(rules: list, models: dict, sources: dict):
    """算出同步后的单价表。models: {模型名: 条目名}。返回 (新的规则表, 加了的, 更新了的, 待确认 {模型: 候选})。
    已核对和手动的规则（没有 source）不动；上次同步来的（有 source）可以被更新。"""
    new_rules = [dict(r) for r in rules]
    added, updated, pending = [], [], {}
    for model, entry in sorted(models.items()):
        if not model:
            continue
        have = _covered(new_rules, model)
        if have is not None and not have.get("source"):
            continue   # 核对过的、手动的：不碰
        found, cands = match(model, entry or "", sources)
        if found is None:
            if have is None and cands:
                pending[model] = cands
            continue
        rule = rule_for(model, found)
        if have is None:
            new_rules.append(rule)
            added.append(model)
        elif any(have.get(k) != rule[k] for k in ("input", "cache_hit", "output", "source")):
            new_rules[new_rules.index(have)] = rule
            updated.append(model)
    return new_rules, added, updated, pending
