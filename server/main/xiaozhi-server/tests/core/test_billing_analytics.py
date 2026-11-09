"""[xiaozhi-hub] 费用分析和单价同步（设计画布第 20 块）。"""

import datetime
import sqlite3

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from core import billing
from core.admin import admin_handler as ah, config_store
from core.billing import analytics, price_sync, pricing
from core.billing.store import UsageDB

TZ = pricing.tz({})


def ts(y, m, d, h=0, mi=0):
    return datetime.datetime(y, m, d, h, mi, tzinfo=TZ).timestamp()


@pytest.fixture
def db(tmp_path):
    d = UsageDB(tmp_path / "usage.db")
    billing.set_db(d)
    yield d
    billing.set_db(None)


def add(db, when, model="deepseek-v4-flash", device="dev-1", purpose="chat", prompt=1000, hit=400, out=200, cost=0.002,
        currency="CNY", ok=1, first_ms=800, error=None, priced=1):
    db.add({"ts": when, "day": "x", "month": "x", "device": device, "entry": "DeepSeekLLM", "model": model, "purpose": purpose,
            "prompt_tokens": prompt, "cache_hit_tokens": hit, "completion_tokens": out, "cost": cost, "currency": currency,
            "tier": "", "priced": priced, "estimated": 0, "first_ms": first_ms, "total_ms": first_ms + 500, "ok": ok, "error": error})


def test_old_database_gets_new_columns(tmp_path):
    p = tmp_path / "old.db"
    c = sqlite3.connect(p)
    c.executescript("CREATE TABLE usage (id INTEGER PRIMARY KEY, ts REAL NOT NULL, day TEXT NOT NULL, month TEXT NOT NULL, "
                    "device TEXT, entry TEXT, model TEXT, purpose TEXT, prompt_tokens INTEGER DEFAULT 0, cache_hit_tokens INTEGER DEFAULT 0, "
                    "completion_tokens INTEGER DEFAULT 0, cost REAL DEFAULT 0, currency TEXT, tier TEXT, priced INTEGER DEFAULT 1, "
                    "estimated INTEGER DEFAULT 0); INSERT INTO usage (ts, day, month, model) VALUES (1, 'd', 'm', 'x');")
    c.commit(); c.close()
    d = UsageDB(p)
    r = d.rows(0, 10)[0]
    assert r["ok"] == 1 and r["first_ms"] is None and r["error"] is None


def test_ranges_and_compare():
    now = ts(2026, 11, 9, 16, 30)
    a, b, pa, pb, label = analytics.resolve("today", now, TZ)
    assert a == ts(2026, 11, 9) and b == now and pa == ts(2026, 11, 8) and pb == now - 86400 and "昨天" in label
    a, b, pa, pb, _ = analytics.resolve("month", now, TZ)
    assert a == ts(2026, 11, 1) and pa == ts(2026, 10, 1) and pb - pa == b - a
    a, b, pa, pb, _ = analytics.resolve("custom", now, TZ, "2026-11-01", "2026-11-07")
    assert a == ts(2026, 11, 1) and b == ts(2026, 11, 8) and pa == ts(2026, 10, 25)
    with pytest.raises(ValueError):
        analytics.resolve("custom", now, TZ, "2026-11-07", "2026-11-01")
    with pytest.raises(ValueError):
        analytics.resolve("forever", now, TZ)


def test_analyze_series_groups_heat_and_split(db):
    now = ts(2026, 11, 9, 16, 30)
    add(db, ts(2026, 11, 9, 8, 5), cost=0.004)
    add(db, ts(2026, 11, 9, 8, 40), device="dev-2", first_ms=3000)
    add(db, ts(2026, 11, 9, 13, 0), model="deepseek-flash", purpose="vision")
    add(db, ts(2026, 11, 9, 14, 0), prompt=0, hit=0, out=0, cost=0, ok=0, error="APIConnectionError: boom", first_ms=0)
    add(db, ts(2026, 11, 9, 15, 0), model="gpt-5-mini", cost=0.001, currency="USD")
    add(db, ts(2026, 11, 8, 9, 0))                      # 昨天：比较用
    cfg = billing.settings({})
    d = analytics.analyze(db, cfg, "today", now_ts=now, names={"dev-1": "客厅小麦"})
    t, p = d["totals"], d["previous"]
    assert d["bucket"] == "hour" and len(d["series"]) == 17 and d["series"][8]["requests"] == 2
    assert t["requests"] == 5 and t["failures"] == 1 and t["fail_reasons"] == ["APIConnectionError"]
    assert t["main_cost"] == pytest.approx(0.008) and t["cost"]["USD"] == pytest.approx(0.001) and p["requests"] == 1
    assert t["cache_rate"] == pytest.approx(0.4) and t["first_p95"] == 3000
    dev = {g["key"]: g for g in d["groups"]["device"]}
    assert dev["dev-1"]["label"] == "客厅小麦" and dev["dev-2"]["requests"] == 1
    assert sum(c["requests"] for row in d["heat"] for c in row) == 6
    assert d["split"]["input"] > 0 and d["split"]["saved"] > 0     # deepseek 有内置单价：能拆
    only = analytics.analyze(db, cfg, "today", filters={"purpose": "vision"}, now_ts=now)
    assert only["totals"]["requests"] == 1
    week = analytics.analyze(db, cfg, "7d", now_ts=now)
    assert week["bucket"] == "day" and len(week["series"]) == 8
    opts = analytics.options(db, now, {"dev-1": "客厅小麦"})
    assert "gpt-5-mini" in opts["models"] and {"id": "dev-1", "name": "客厅小麦"} in opts["devices"]


def test_record_failure_and_timings(db):
    billing.record("deepseek-v4-flash", {"prompt": 100, "completion": 10}, first_ms=812.4, total_ms=1500)
    billing.record_failure("deepseek-v4-flash", "Timeout: 60s", total_ms=60000)
    rows = db.rows(0, 4e9)
    assert rows[0]["first_ms"] == 812 and rows[0]["ok"] == 1
    assert rows[1]["ok"] == 0 and rows[1]["error"] == "Timeout: 60s" and rows[1]["cost"] == 0


MD = {"deepseek": {"models": {"deepseek-flash": {"cost": {"input": 0.15, "output": 0.6, "cache_read": 0.003}}}},
      "302ai": {"models": {"deepseek-flash": {"cost": {"input": 0.2, "output": 0.8}}}},
      "openai": {"models": {"gpt-5-mini": {"cost": {"input": 0.25, "output": 2, "cache_read": 0.025}}}},
      "alibaba-cn": {"models": {"qwen-vl-plus": {"cost": {"input": 0.115, "output": 0.287}}}},
      "nano-gpt": {"models": {"mystery-1": {"cost": {"input": 1, "output": 2}}}},
      "other": {"models": {"mystery-1": {"cost": {"input": 3, "output": 4}}}}}
LL = {"moonshot/kimi-latest": {"input_cost_per_token": 2e-06, "output_cost_per_token": 5e-06, "litellm_provider": "moonshot"}}
OR = {"data": [{"id": "z-ai/glm-4.5v", "pricing": {"prompt": "0.0000006", "completion": "0.0000018"}}]}


def test_match_official_then_fallbacks_then_pending():
    src = {"models.dev": MD, "LiteLLM": LL, "OpenRouter": OR}
    found, _ = price_sync.match("gpt-5-mini", "OpenAILLM", src)
    assert found["source"] == "models.dev" and found["provider"] == "openai" and found["prices"] == {"input": 0.25, "cache_hit": 0.025, "output": 2.0}
    found, _ = price_sync.match("qwen-vl-plus", "QwenVLVLLM", src)
    assert found["provider"] == "alibaba-cn" and found["prices"]["cache_hit"] == 0.115   # 没有缓存价：按输入价
    found, _ = price_sync.match("kimi-latest", "KimiLLM", src)
    assert found["source"] == "LiteLLM" and found["prices"]["input"] == pytest.approx(2.0)
    found, _ = price_sync.match("glm-4.5v", "", src)
    assert found["source"] == "OpenRouter" and found["provider"] == "z-ai"
    found, cands = price_sync.match("mystery-1", "", src)      # 没有官方：待确认，两个不同价格的候选
    assert found is None and len(cands) == 2


def test_plan_keeps_checked_and_manual_rules():
    rules = pricing.DEFAULT_PRICES + [{"match": "kimi-latest", "currency": "CNY", "input": 2, "cache_hit": 0.5, "output": 10}]
    old_sync = {"match": "gpt-5-mini", "currency": "USD", "input": 9, "cache_hit": 9, "output": 9, "source": "models.dev"}
    models = {"deepseek-flash": "DshCLI", "kimi-latest": "KimiLLM", "gpt-5-mini": "OpenAILLM", "qwen-vl-plus": "QwenVLVLLM",
              "mystery-1": "", "glm-4v-flash": "ChatGLMVLLM"}
    new, added, updated, pending = price_sync.plan(rules + [old_sync], models, {"models.dev": MD, "LiteLLM": LL, "OpenRouter": OR})
    assert added == ["qwen-vl-plus"] and updated == ["gpt-5-mini"] and list(pending) == ["mystery-1"]
    by = {r["match"]: r for r in new}
    assert by["kimi-latest"]["input"] == 2 and "source" not in by["kimi-latest"]           # 手动的不动
    assert pricing.find_rule(new, "deepseek-flash")["currency"] == "CNY"                     # 核对过的不动
    assert by["gpt-5-mini"]["input"] == 0.25 and by["qwen-vl-plus"]["currency"] == "USD"


@pytest.fixture
async def admin(tmp_path, db, monkeypatch):
    src = web.Application()
    src.add_routes([web.get("/md", lambda r: web.json_response(MD)), web.get("/ll", lambda r: web.json_response(LL)),
                    web.get("/or", lambda r: web.Response(status=503))])
    srv = TestServer(src)
    await srv.start_server()
    monkeypatch.setattr(price_sync, "URLS", {"models.dev": str(srv.make_url("/md")), "LiteLLM": str(srv.make_url("/ll")),
                                             "OpenRouter": str(srv.make_url("/or"))})
    cfg = tmp_path / ".config.yaml"
    cfg.write_text("selected_module:\n  LLM: DeepSeekLLM\nVLLM:\n  QwenVLVLLM:\n    type: openai\n    model_name: qwen-vl-plus\n", encoding="utf-8")
    app = web.Application()
    from tests.conftest import CONFIG
    h = ah.AdminHandler(dict(CONFIG), ws_server=None, config_path=cfg)
    h.register(app)
    ah.AdminHandler._price_pending = {}
    async with TestClient(TestServer(app)) as c:
        await c.post("/admin/api/setup", json={"password": "pass-1234"})
        c.cfg = cfg
        yield c
    await srv.close()


async def test_endpoints_analytics_sync_accept(admin, db):
    add(db, datetime.datetime.now(datetime.timezone.utc).timestamp() - 60, model="mystery-1", priced=0, cost=0)
    a = await (await admin.get("/admin/api/billing/analytics?range=24h")).json()
    assert a["ok"] and a["totals"]["requests"] == 1 and a["bucket"] == "hour" and "mystery-1" in a["options"]["models"]
    assert (await admin.get("/admin/api/billing/analytics?range=custom&start=bad&end=bad")).status == 400
    r = await (await admin.post("/admin/api/billing/prices/sync", json={})).json()
    assert r["ok"] and r["added"] == ["qwen-vl-plus"] and list(r["pending"]) == ["mystery-1"] and r["failed"][0].startswith("OpenRouter")
    prices = config_store.load_custom(admin.cfg)["billing"]["prices"]
    assert any(p["match"] == "qwen-vl-plus" and p["source"] == "models.dev" for p in prices)
    assert any("deepseek-flash" in p["match"] for p in prices)                               # 内置的核对价一起写进去了
    r = await (await admin.post("/admin/api/billing/prices/accept", json={"model": "mystery-1", "index": 1})).json()
    assert r["ok"]
    prices = config_store.load_custom(admin.cfg)["billing"]["prices"]
    assert next(p for p in prices if p["match"] == "mystery-1")["input"] == 3
    # 页面上保存单价表时，同步来的规则保留来源
    r = await admin.post("/admin/api/billing", json={"prices": prices})
    assert r.status == 200 and next(p for p in config_store.load_custom(admin.cfg)["billing"]["prices"] if p["match"] == "qwen-vl-plus")["source"] == "models.dev"
