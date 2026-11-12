"""[xiaozhi-hub] 费用页的分析（设计画布第 20 块，参考 seakee/CPA-Manager-Plus 的用量分析，MIT）：
时间范围和粒度、和上一段同样长的时间比、趋势、排行、token 构成、热力图。

家里的用量一天几十到几百条，直接把这段时间的记录读出来在 Python 里汇总，不建汇总表。
时间按费用的时区（默认北京时间）切。
"""

import datetime
import math

from core.billing import pricing

RANGES = ("24h", "today", "yesterday", "7d", "30d", "month", "custom")
DIMS = ("model", "device", "purpose", "entry")
HEAT_DAYS = 28


def _local(ts, zone):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).astimezone(zone)


def resolve(range_id: str, now_ts: float, zone, start: str = "", end: str = ""):
    """(开始, 结束, 上一段的开始, 上一段的结束, 说明)，都是时间戳。range_id 是 RANGES 里的一个。"""
    if range_id not in RANGES:
        raise ValueError("不支持这个时间范围")
    now = _local(now_ts, zone)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day = datetime.timedelta(days=1)
    if range_id == "24h":
        a, b = now - day, now
        return a.timestamp(), b.timestamp(), (a - day).timestamp(), a.timestamp(), "和前 24 小时比"
    if range_id == "today":
        return midnight.timestamp(), now.timestamp(), (midnight - day).timestamp(), (now - day).timestamp(), "和昨天同一时段比"
    if range_id == "yesterday":
        return (midnight - day).timestamp(), midnight.timestamp(), (midnight - 2 * day).timestamp(), (midnight - day).timestamp(), "和前天比"
    if range_id in ("7d", "30d"):
        n = 7 if range_id == "7d" else 30
        a = now - n * day
        return a.timestamp(), now.timestamp(), (a - n * day).timestamp(), a.timestamp(), f"和再往前 {n} 天比"
    if range_id == "month":
        first = midnight.replace(day=1)
        prev_first = (first - day).replace(day=1)
        elapsed = now - first
        return first.timestamp(), now.timestamp(), prev_first.timestamp(), (prev_first + elapsed).timestamp(), "和上个月同样天数比"
    if range_id == "custom":
        try:
            a = datetime.datetime.fromisoformat(start).replace(tzinfo=zone)
            b = datetime.datetime.fromisoformat(end).replace(tzinfo=zone) + day
        except (TypeError, ValueError):
            raise ValueError("自定义范围要写成 YYYY-MM-DD") from None
        if b <= a or (b - a).days > 400:
            raise ValueError("自定义范围不对（最多 400 天）")
        span = b - a
        return a.timestamp(), b.timestamp(), (a - span).timestamp(), a.timestamp(), "和前面同样长的一段比"
    raise AssertionError(range_id)   # RANGES 里加了新的，上面要跟着加


def _p(values, q):
    if not values:
        return None
    v = sorted(values)
    return v[min(len(v) - 1, max(0, math.ceil(q * len(v)) - 1))]


def _split(row, rules, cfg):
    """一条记录的费用拆成 没命中的输入、缓存命中、输出 三份（按那一刻的单价）；没单价返回 None。"""
    rule = pricing.find_rule(rules, row["model"])
    if not rule:
        return None
    _, prices = pricing.price_at(rule, datetime.datetime.fromtimestamp(row["ts"], datetime.timezone.utc), cfg)
    hit = min(row["cache_hit_tokens"] or 0, row["prompt_tokens"] or 0)
    miss = (row["prompt_tokens"] or 0) - hit
    return {"input": miss * prices["input"] / 1e6, "cache": hit * prices["cache_hit"] / 1e6,
            "output": (row["completion_tokens"] or 0) * prices["output"] / 1e6,
            "saved": hit * (prices["input"] - prices["cache_hit"]) / 1e6, "currency": rule.get("currency") or "CNY"}


def _totals(rows, main):
    ok_rows = [r for r in rows if r["ok"] != 0]
    prompt = sum(r["prompt_tokens"] or 0 for r in ok_rows)
    hit = sum(min(r["cache_hit_tokens"] or 0, r["prompt_tokens"] or 0) for r in ok_rows)
    completion = sum(r["completion_tokens"] or 0 for r in ok_rows)
    cost = {}
    for r in rows:
        if r["currency"]:
            cost[r["currency"]] = cost.get(r["currency"], 0) + (r["cost"] or 0)
    firsts = [r["first_ms"] for r in rows if r["first_ms"] is not None and r["ok"] != 0]
    fails = [r for r in rows if r["ok"] == 0]
    return {"requests": len(rows), "failures": len(fails), "prompt": prompt, "cache_hit": hit, "completion": completion,
            "tokens": prompt + completion, "cost": {k: round(v, 6) for k, v in cost.items()},
            "main_cost": round(cost.get(main, 0), 6), "cache_rate": round(hit / prompt, 4) if prompt else None,
            "first_p95": _p(firsts, 0.95), "first_avg": round(sum(firsts) / len(firsts)) if firsts else None,
            "fail_reasons": sorted({(r["error"] or "").split(":")[0] for r in fails if r["error"]})[:3]}


def analyze(db, cfg: dict, range_id: str = "today", bucket: str = "auto", filters: dict = None,
            start: str = "", end: str = "", now_ts: float = None, names: dict = None) -> dict:
    zone = pricing.tz(cfg)
    now_ts = now_ts or datetime.datetime.now(datetime.timezone.utc).timestamp()
    a, b, pa, pb, compare = resolve(range_id, now_ts, zone, start, end)
    filters = {k: v for k, v in (filters or {}).items() if k in ("model", "device", "purpose") and v}
    keep = lambda rows: [r for r in rows if all((r.get(k) or "") == v for k, v in filters.items())]
    rows, prev = keep(db.rows(a, b)), keep(db.rows(pa, pb))
    main = (cfg.get("quota") or {}).get("currency") or "CNY"
    if bucket not in ("hour", "day"):
        bucket = "hour" if b - a <= 2 * 86400 + 1 else "day"
    rules = pricing.rules(cfg)

    # 趋势：按小时或按天的格子，空格子也给（图上是 0）
    step = 3600 if bucket == "hour" else 86400
    start_local = _local(a, zone).replace(minute=0, second=0, microsecond=0)
    if bucket == "day":
        start_local = start_local.replace(hour=0)
    keys, t = [], start_local
    while t.timestamp() < b and len(keys) < 800:
        keys.append(t)
        t = (t + datetime.timedelta(seconds=step)) if bucket == "hour" else (t + datetime.timedelta(days=1)).replace(hour=0)
    fmt = "%m-%d %H:00" if bucket == "hour" else "%m-%d"
    series = {k.strftime(fmt): {"label": k.strftime(fmt), "requests": 0, "input": 0, "cache_hit": 0, "output": 0,
                                "cost": 0.0, "failures": 0} for k in keys}
    split = {"input": 0.0, "cache": 0.0, "output": 0.0, "saved": 0.0}
    for r in rows:
        k = _local(r["ts"], zone).strftime(fmt)
        s = series.get(k)
        if s is None:
            continue
        s["requests"] += 1
        s["failures"] += 1 if r["ok"] == 0 else 0
        hit = min(r["cache_hit_tokens"] or 0, r["prompt_tokens"] or 0)
        s["input"] += (r["prompt_tokens"] or 0) - hit
        s["cache_hit"] += hit
        s["output"] += r["completion_tokens"] or 0
        if r["currency"] == main:
            s["cost"] += r["cost"] or 0
        parts = _split(r, rules, cfg) if r["ok"] != 0 else None
        if parts and parts["currency"] == main:
            for x in split:
                split[x] += parts[x]

    # 排行：模型、设备、用途、模型条目
    groups = {}
    for dim in DIMS:
        acc = {}
        for r in rows:
            acc.setdefault(r.get(dim) or "", []).append(r)
        items = []
        for key, rs in acc.items():
            t2 = _totals(rs, main)
            items.append({"key": key, "label": (names or {}).get(key, key) if dim == "device" else key, **t2,
                          "unpriced": any(not x["priced"] for x in rs if x["ok"] != 0)})
        items.sort(key=lambda x: (-x["main_cost"], -x["requests"]))
        groups[dim] = items[:50]

    # 热力图：最近 4 周，星期几 × 几点（跟着筛选）
    heat_rows = keep(db.rows(now_ts - HEAT_DAYS * 86400, now_ts))
    heat = [[{"requests": 0, "cost": 0.0} for _ in range(24)] for _ in range(7)]
    for r in heat_rows:
        lt = _local(r["ts"], zone)
        cell = heat[lt.weekday()][lt.hour]
        cell["requests"] += 1
        if r["currency"] == main:
            cell["cost"] += r["cost"] or 0

    return {"range": range_id, "bucket": bucket, "compare": compare, "currency": main,
            "start": a, "end": b, "totals": _totals(rows, main), "previous": _totals(prev, main),
            "series": list(series.values()), "split": {k: round(v, 6) for k, v in split.items()},
            "groups": groups, "heat": heat, "heat_days": HEAT_DAYS}


def options(db, now_ts: float, names: dict = None) -> dict:
    """筛选框里的选项：最近 90 天出现过的模型、设备、用途。"""
    rows = db.rows(now_ts - 90 * 86400, now_ts + 1)
    pick = lambda k: sorted({r[k] for r in rows if r.get(k)})
    return {"models": pick("model"), "devices": [{"id": d, "name": (names or {}).get(d, "")} for d in pick("device")],
            "purposes": pick("purpose")}
