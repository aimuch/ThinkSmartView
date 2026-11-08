"""[xiaozhi-hub] 费用统计（core/billing）：分时单价、用量字段、记账归属、额度拦截和降级、OpenAI 流里的用量、后台接口。"""
import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import openai
import pytest

from core import billing
from core.billing import pricing
from core.billing.store import UsageDB

BJ = datetime.timezone(datetime.timedelta(hours=8))
FLASH = pricing.DEFAULT_PRICES[0]


@pytest.fixture(autouse=True)
def usage_db(tmp_path):
    billing.set_db(UsageDB(tmp_path / "usage.db"))
    billing.configure({})
    yield billing.db()
    billing.set_db(None)


def at(*args):
    return datetime.datetime(*args, tzinfo=BJ)


# ---- 单价 ----

def test_deepseek_peak_and_idle():
    assert pricing.price_at(FLASH, at(2026, 11, 9, 10, 0))[0] == "高峰"          # 周一上午
    assert pricing.price_at(FLASH, at(2026, 11, 9, 12, 30))[0] == "标准"         # 午休是空闲时段
    assert pricing.price_at(FLASH, at(2026, 11, 9, 17, 59))[1]["output"] == 8
    assert pricing.price_at(FLASH, at(2026, 11, 9, 18, 0))[1]["output"] == 4
    assert pricing.price_at(FLASH, at(2026, 11, 8, 10, 0))[0] == "标准"          # 周日


def test_holidays_and_workdays():
    cfg = {"holidays": ["2026-10-01"], "workdays": ["2026-10-11"]}
    assert pricing.price_at(FLASH, at(2026, 10, 1, 10, 0), cfg)[0] == "标准"     # 国庆（周四）按空闲
    assert pricing.price_at(FLASH, at(2026, 10, 11, 10, 0), cfg)[0] == "高峰"    # 调休上班的周日


def test_server_in_utc_still_uses_beijing_time():
    utc_0200 = datetime.datetime(2026, 11, 9, 2, 0, tzinfo=datetime.timezone.utc)   # 北京 10:00
    assert pricing.price_at(FLASH, utc_0200)[0] == "高峰"


def test_hours_across_midnight():
    rule = {"match": "m", "input": 1, "cache_hit": 0, "output": 1,
            "tiers": [{"name": "夜间", "days": "all", "hours": "22:00-06:00", "input": 0.5, "cache_hit": 0, "output": 0.5}]}
    assert pricing.price_at(rule, at(2026, 11, 9, 23, 0))[0] == "夜间"
    assert pricing.price_at(rule, at(2026, 11, 9, 5, 59))[0] == "夜间"
    assert pricing.price_at(rule, at(2026, 11, 9, 6, 0))[0] == "标准"


def test_cost_with_cache_hits():
    prices = {"input": 2, "cache_hit": 0.04, "output": 8}
    assert pricing.cost({"prompt": 1_000_000, "cache_hit": 600_000, "completion": 100_000}, prices) == \
        pytest.approx(0.4 * 2 + 0.6 * 0.04 + 0.1 * 8)


def test_find_rule_wildcards():
    rules = [{"match": "gpt-5*, o3"}, {"match": "deepseek-flash, deepseek-v4-flash"}]
    assert pricing.find_rule(rules, "GPT-5-mini") is rules[0]
    assert pricing.find_rule(rules, "deepseek-v4-flash") is rules[1]
    assert pricing.find_rule(rules, "kimi") is None


# ---- 用量字段 ----

def test_usage_fields():
    deepseek = SimpleNamespace(prompt_tokens=100, completion_tokens=20, prompt_cache_hit_tokens=64)
    assert billing.usage_of(deepseek) == {"prompt": 100, "cache_hit": 64, "completion": 20}
    openai_u = {"prompt_tokens": 50, "completion_tokens": 5, "prompt_tokens_details": {"cached_tokens": 32}}
    assert billing.usage_of(openai_u) == {"prompt": 50, "cache_hit": 32, "completion": 5}
    anthropic = {"input_tokens": 10, "output_tokens": 7, "cache_read_input_tokens": 90}
    assert billing.usage_of(anthropic) == {"prompt": 100, "cache_hit": 90, "completion": 7}


# ---- 记账 ----

def test_record_and_summary(usage_db):
    with billing.context(device="dev1", entry="DeepSeekLLM", purpose="summary"):
        billing.record("deepseek-v4-flash", {"prompt": 1_000_000, "cache_hit": 0, "completion": 0})
    billing.record("unknown-model", {"prompt": 10, "completion": 1}, device="dev2")
    s = billing.summary({})
    assert s["today"]["CNY"] in (1.0, 2.0)            # 空闲 1 元 / 高峰 2 元（看测试跑的时刻）
    assert s["month"] == s["today"] == s["total"] and len(s["this_month"]) == 7   # 金额和月份标签不是同一个键
    assert s["tokens"]["today"]["requests"] == 2
    assert s["unpriced"] == ["unknown-model"]
    assert {r["key"] for r in s["devices"]} == {"dev1", "dev2"}
    assert {r["key"] for r in s["purposes"]} == {"summary", "chat"}


def test_admin_test_is_not_billed(usage_db):
    with billing.unbilled():
        billing.record("deepseek-v4-flash", {"prompt": 10, "completion": 1})
    billing.record("deepseek-v4-flash", {"prompt": 10, "completion": 1})
    assert billing.summary({})["tokens"]["total"]["requests"] == 1


def test_unbilled_does_not_leak_when_the_test_fails(usage_db):
    with pytest.raises(RuntimeError):
        with billing.unbilled():
            raise RuntimeError("connection refused before any stream")
    billing.record("deepseek-v4-flash", {"prompt": 10, "completion": 1})
    assert billing.summary({})["tokens"]["total"]["requests"] == 1


def test_claude_code_cost(usage_db):
    billing.record_cost("claude_cli:sonnet", 0.12, "USD", device="d", entry="ClaudeCLI")
    assert billing.summary({})["total"] == {"USD": 0.12}
    billing.configure({"billing": {"count_claude_code": False}})
    billing.record_cost("claude_cli:sonnet", 0.5, "USD", device="d")
    assert billing.summary({})["total"] == {"USD": 0.12}


# ---- 额度 ----

def conn_with(quota, current="DeepSeekLLM", device="dev1"):
    return SimpleNamespace(config={"billing": {"quota": quota}}, device_id=device, hub_route={"current": current})


def spend(amount, device="dev1"):
    rule = {"match": "m", "currency": "CNY", "input": amount, "cache_hit": 0, "output": 0, "tiers": []}
    billing.configure({"billing": {"prices": [rule]}})
    billing.record("m", {"prompt": 1_000_000, "completion": 0}, device=device)


def test_no_quota_no_check():
    spend(100)
    assert billing.check_quota(conn_with({"daily": 0})) is None


def test_daily_quota_blocks():
    spend(5)
    assert billing.check_quota(conn_with({"daily": 10})) is None
    spend(6)
    r = billing.check_quota(conn_with({"daily": 10}))
    assert r["action"] == "block" and "今天的大模型额度用完了" in r["text"] and "¥11.00" in r["text"]


def test_device_quota_only_counts_that_device():
    spend(8, device="other")
    assert billing.check_quota(conn_with({"device_daily": 5})) is None
    spend(6, device="dev1")
    assert billing.check_quota(conn_with({"device_daily": 5}))["action"] == "block"


def test_downgrade():
    spend(20)
    q = {"daily": 10, "action": "downgrade", "downgrade_to": "KimiLLM"}
    assert billing.check_quota(conn_with(q)) == {"action": "downgrade", "to": "KimiLLM", "text": pytest.approx(
        billing.check_quota(conn_with(q))["text"])}
    assert billing.check_quota(conn_with(q, current="KimiLLM")) is None     # 已经在便宜模型上，照常答


def test_chat_refuses_when_over_quota(monkeypatch):
    from tests.core.test_model_not_ready import LLM, make_handler, spoken
    LLM["KimiLLM"]["api_key"] = "sk-1"
    h = make_handler("KimiLLM")
    h.config["billing"] = {"quota": {"daily": 1}}
    spend(2)
    h.chat("你好")
    assert "额度用完了" in spoken(h)
    h.llm.response.assert_not_called()
    assert not [m for m in h.dialogue.dialogue if m.role == "user"]


# ---- OpenAI 兼容接口的流 ----

class FakeStream:
    """What openai's Stream looks like to the provider: iterable, closable, hashable."""
    response = SimpleNamespace(extensions={})

    def __init__(self, chunks):
        self.chunks = chunks

    def __iter__(self):
        return iter(self.chunks)

    def close(self):
        pass


def chunk(content=None, usage=None):
    choices = [SimpleNamespace(delta=SimpleNamespace(content=content, tool_calls=None))] if content is not None else []
    return SimpleNamespace(choices=choices, usage=usage)


def provider(create):
    from core.providers.llm.openai.openai import LLMProvider
    p = LLMProvider.__new__(LLMProvider)
    p.model_name, p.base_url, p.extra_body = "deepseek-v4-flash", "https://api.deepseek.com", None
    p.max_tokens = p.temperature = p.top_p = p.frequency_penalty = None
    p._streams, p._streams_lock = {}, __import__("threading").Lock()
    p.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return p


def test_stream_usage_is_recorded():
    seen = {}

    def create(**kw):
        seen.update(kw)
        return FakeStream([chunk("你"), chunk("好", usage=SimpleNamespace(prompt_tokens=30, completion_tokens=2,
                                                                       prompt_cache_hit_tokens=10))])
    assert "".join(provider(create).response("", [{"role": "user", "content": "hi"}])) == "你好"
    assert seen["stream_options"] == {"include_usage": True}
    t = billing.summary({})["tokens"]["total"]
    assert (t["prompt"], t["cache_hit"], t["completion"]) == (30, 10, 2)


def test_cut_stream_is_estimated():
    p = provider(lambda **kw: FakeStream([chunk("一二三"), chunk("四五")]))
    gen = p.response_with_functions("", [{"role": "user", "content": "你好吗"}], functions=[])
    next(gen)
    gen.close()          # 被打断：没收到用量
    rows = billing.summary({})["models"]
    assert rows[0]["estimated"] and billing.summary({})["tokens"]["total"]["prompt"] == 3


def test_stream_options_rejected_falls_back():
    from core.providers.llm.openai import openai as mod
    calls = []

    def create(**kw):
        calls.append(kw)
        if "stream_options" in kw:
            raise openai.BadRequestError("Unrecognized request argument: stream_options",
                                         response=MagicMock(status_code=400), body=None)
        return FakeStream([chunk("好")])
    p = provider(create)
    p.base_url = "http://local.test/v1"
    assert "".join(p.response("", [{"role": "user", "content": "x"}])) == "好"
    assert "http://local.test/v1" in mod._NO_STREAM_USAGE and len(calls) == 2
    "".join(p.response("", [{"role": "user", "content": "x"}]))
    assert "stream_options" not in calls[-1]


# ---- 后台 ----

def test_billing_update_validation():
    from core.admin.admin_handler import _billing_update
    ok = _billing_update({"prices": [{"match": "m", "input": "1.5", "cache_hit": 0, "output": 3,
                                      "tiers": [{"name": "夜间", "days": "1-5", "hours": "22:00-06:00", "input": 1}]}],
                          "quota": {"daily": 5, "action": "downgrade", "downgrade_to": "KimiLLM"},
                          "settings": {"holidays": ["2026-10-01", "2026-10-01"], "timezone": "Asia/Shanghai"}})
    assert ok["prices"][0]["input"] == 1.5 and ok["prices"][0]["tiers"][0]["days"] == "1-5"
    assert ok["holidays"] == ["2026-10-01"] and ok["quota"]["daily"] == 5
    for bad in ({"prices": [{"match": ""}]}, {"prices": [{"match": "m", "tiers": [{"hours": "9-12"}]}]},
                {"quota": {"daily": -1}}, {"quota": {"action": "explode"}}, {"settings": {"holidays": ["10/01"]}},
                {"settings": {"timezone": "Mars/Base"}}):
        with pytest.raises(ValueError):
            _billing_update(bad)


async def test_admin_billing_api(tmp_path):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from core.admin.admin_handler import AdminHandler
    from tests.conftest import CONFIG
    cfg_path = tmp_path / ".config.yaml"
    cfg_path.write_text("")
    app = web.Application()
    AdminHandler(dict(CONFIG), ws_server=None, config_path=cfg_path).register(app)
    async with TestClient(TestServer(app)) as c:
        assert (await c.get("/admin/api/billing")).status == 401
        await c.post("/admin/api/setup", json={"password": "pass-1234"})
        d = await (await c.get("/admin/api/billing")).json()
        assert d["ok"] and d["prices_default"] and d["quota"]["daily"] == 0
        r = await c.post("/admin/api/billing", json={"quota": {"daily": 3, "currency": "CNY"}})
        assert (await r.json())["ok"]
        assert "daily: 3" in cfg_path.read_text()
        d = await (await c.get("/admin/api/billing")).json()
        assert d["quota"]["daily"] == 3
        r = await c.get("/kiosk/billing")
        assert r.status == 200 and "today" in await r.json()
