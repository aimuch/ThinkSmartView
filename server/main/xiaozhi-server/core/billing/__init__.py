"""[xiaozhi-hub] What the models cost: every request's tokens and money (data/usage.db), per day / month /
total, time-of-day prices (pricing.py), and spending quotas checked before each user turn.

Who pays for a request:
- the main chat passes its session_id: the connection gives the device and the model entry;
- calls with no session (memory summaries, intent, the search plugin) run inside `context(...)`;
- Claude Code reports its own cost (USD) per turn (record_cost).
A stream cut before its usage chunk arrives (the user interrupted) is recorded with estimated tokens.

Settings: billing in data/.config.yaml (admin page 费用):
  timezone: Asia/Shanghai, holidays: [YYYY-MM-DD], workdays: [YYYY-MM-DD] (调休 working weekends),
  prices: [...] (pricing.py), count_claude_code: true,
  quota: {currency: CNY, daily: 0, monthly: 0, device_daily: 0, action: block | downgrade, downgrade_to: <entry>}
  (0 = no limit).
"""
import contextlib
import datetime
import threading
import time

from config.logger import setup_logging

from . import price_sync, pricing  # noqa: F401  price_sync 给后台的单价同步用
from .store import UsageDB

TAG = __name__
logger = setup_logging()

DEFAULTS = {'timezone': pricing.DEFAULT_TZ, 'holidays': [], 'workdays': [], 'prices': [], 'count_claude_code': True,
            'quota': {'currency': 'CNY', 'daily': 0, 'monthly': 0, 'device_daily': 0, 'action': 'block',
                      'downgrade_to': ''}}

_config = {}
_db = None
_db_lock = threading.Lock()
_ctx = threading.local()


def configure(config: dict):
    """The server's merged config (startup, and after the admin page saves billing)."""
    global _config
    _config = config or {}


def settings(config=None) -> dict:
    raw = dict((config if config is not None else _config).get('billing') or {})
    out = {**DEFAULTS, **{k: v for k, v in raw.items() if k in DEFAULTS}}
    out['quota'] = {**DEFAULTS['quota'], **(raw.get('quota') or {})}
    return out


def db() -> UsageDB:
    global _db
    with _db_lock:
        if _db is None:
            from config.config_loader import get_project_dir
            _db = UsageDB(get_project_dir() + 'data/usage.db')
        return _db


def set_db(instance):
    global _db
    _db = instance


@contextlib.contextmanager
def context(device=None, entry=None, purpose=None):
    """Attribute the requests made inside to this device / entry / purpose (same thread)."""
    old = getattr(_ctx, 'value', None)
    _ctx.value = {'device': device, 'entry': entry, 'purpose': purpose}
    try:
        yield
    finally:
        _ctx.value = old


def _from_session(session_id):
    if not session_id:
        return {}
    try:
        from core.admin import connections
        from core.agent import llm_router
        conn = connections.get(session_id)
    except Exception:
        return {}
    if conn is None:
        return {}
    entry = (getattr(conn, 'hub_route', None) or {}).get('current') or llm_router.default_name(conn)
    return {'device': getattr(conn, 'device_id', None) or session_id, 'entry': entry, 'purpose': 'chat'}


def _day_month(ts, cfg):
    local = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).astimezone(pricing.tz(cfg))
    return local.date().isoformat(), local.strftime('%Y-%m')


@contextlib.contextmanager
def unbilled():
    """Requests made inside (this thread) aren't recorded: the admin page's 测试连接."""
    old = getattr(_ctx, 'skip', False)
    _ctx.skip = True
    try:
        yield
    finally:
        _ctx.skip = old


def record(model, usage, session_id='', purpose=None, entry=None, device=None, estimated=False,
           first_ms=None, total_ms=None, ok=True, error=None):
    """usage: {'prompt', 'cache_hit', 'completion'} tokens. Never raises (billing must not break a reply).
    [xiaozhi-hub] first_ms / total_ms：首字和总耗时（毫秒）；ok=False、error：这次调用失败了（费用页的失败率）。"""
    if getattr(_ctx, 'skip', False):
        return
    try:
        cfg = settings()
        who = {**_from_session(session_id), **{k: v for k, v in (getattr(_ctx, 'value', None) or {}).items() if v}}
        ts = time.time()
        rule = pricing.find_rule(pricing.rules(cfg), model)
        if rule:
            tier, prices = pricing.price_at(rule, datetime.datetime.fromtimestamp(ts, datetime.timezone.utc), cfg)
            money, currency = pricing.cost(usage, prices), rule.get('currency') or 'CNY'
        else:
            tier, money, currency = '', 0.0, None
        day, month = _day_month(ts, cfg)
        db().add({'ts': ts, 'day': day, 'month': month, 'device': device or who.get('device'),
                  'entry': entry or who.get('entry'), 'model': model, 'purpose': purpose or who.get('purpose') or 'chat',
                  'prompt_tokens': int(usage.get('prompt') or 0), 'cache_hit_tokens': int(usage.get('cache_hit') or 0),
                  'completion_tokens': int(usage.get('completion') or 0), 'cost': money, 'currency': currency,
                  'tier': tier, 'priced': 1 if rule else 0, 'estimated': 1 if estimated else 0,
                  'first_ms': int(first_ms) if first_ms is not None else None,
                  'total_ms': int(total_ms) if total_ms is not None else None,
                  'ok': 1 if ok else 0, 'error': (str(error)[:200] if error else None)})
        logger.bind(tag=TAG).info(
            f"用量 {model} 输入 {usage.get('prompt')}（缓存命中 {usage.get('cache_hit') or 0}）输出 {usage.get('completion')}"
            + (f" ≈ {pricing.CURRENCIES.get(currency, '')}{money:.4f}（{tier}）" if rule else '（没有单价）')
            + ('，估算' if estimated else ''))
    except Exception as e:
        logger.bind(tag=TAG).warning(f"记录用量失败: {e}")


def record_failure(model, error, session_id='', total_ms=None):
    """[xiaozhi-hub] 一次失败的调用（连不上、报错）：不花钱，记下来算失败率。"""
    record(model, {}, session_id, total_ms=total_ms, ok=False, error=error)


def record_cost(model, money, currency='USD', device=None, entry=None, purpose='chat'):
    """A request that reports its own cost (Claude Code's total_cost_usd)."""
    try:
        cfg = settings()
        if not cfg['count_claude_code'] or not money:
            return
        ts = time.time()
        day, month = _day_month(ts, cfg)
        db().add({'ts': ts, 'day': day, 'month': month, 'device': device, 'entry': entry, 'model': model,
                  'purpose': purpose, 'cost': float(money), 'currency': currency, 'tier': '', 'priced': 1})
    except Exception as e:
        logger.bind(tag=TAG).warning(f"记录费用失败: {e}")


def usage_of(u):
    """OpenAI-style usage object/dict -> {'prompt', 'cache_hit', 'completion'} (DeepSeek, OpenAI, Anthropic fields)."""
    get = (lambda o, k: o.get(k) if isinstance(o, dict) else getattr(o, k, None))
    if u is None:
        return None
    prompt = get(u, 'prompt_tokens')
    if prompt is None and get(u, 'input_tokens') is not None:        # Anthropic: input excludes cache reads
        hit = get(u, 'cache_read_input_tokens') or 0
        return {'prompt': (get(u, 'input_tokens') or 0) + hit + (get(u, 'cache_creation_input_tokens') or 0),
                'cache_hit': hit, 'completion': get(u, 'output_tokens') or 0}
    hit = get(u, 'prompt_cache_hit_tokens')                          # DeepSeek
    if hit is None:
        details = get(u, 'prompt_tokens_details')                    # OpenAI and most compatible APIs
        hit = get(details, 'cached_tokens') if details is not None else None
    if hit is None:
        extra = get(u, 'model_extra') or {}
        hit = extra.get('prompt_cache_hit_tokens') if isinstance(extra, dict) else None
    return {'prompt': prompt or 0, 'cache_hit': hit or 0, 'completion': get(u, 'completion_tokens') or 0}


def estimate(dialogue, output_text):
    """Rough tokens for a cut stream (chat_history's estimate: CJK 1 token per char, ASCII 4 chars per token)."""
    from core.agent.chat_history import estimate_tokens
    text = ''.join(str(m.get('content') or '') if isinstance(m, dict) else '' for m in dialogue or [])
    return {'prompt': estimate_tokens(text), 'cache_hit': 0, 'completion': estimate_tokens(output_text or '')}


# ---- quotas ----

def _today(cfg):
    return _day_month(time.time(), cfg)


def check_quota(conn):
    """Before a user turn: None (go on), or {'action': 'block', 'text'} / {'action': 'downgrade', 'to', 'text'}."""
    try:
        cfg = settings(getattr(conn, 'config', None) or _config)
        q = cfg['quota']
        cur = q.get('currency') or 'CNY'
        day, month = _today(cfg)
        sym = pricing.CURRENCIES.get(cur, '')
        over = None
        if float(q.get('daily') or 0) > 0:
            spent = db().spent(day=day).get(cur, 0)
            if spent >= float(q['daily']):
                over = f'今天的大模型额度用完了（已用 {sym}{spent:.2f}，每天上限 {sym}{float(q["daily"]):.2f}）'
        if not over and float(q.get('monthly') or 0) > 0:
            spent = db().spent(month=month).get(cur, 0)
            if spent >= float(q['monthly']):
                over = f'这个月的大模型额度用完了（已用 {sym}{spent:.2f}，每月上限 {sym}{float(q["monthly"]):.2f}）'
        device = getattr(conn, 'device_id', None)
        if not over and device and float(q.get('device_daily') or 0) > 0:
            spent = db().spent(day=day, device=device).get(cur, 0)
            if spent >= float(q['device_daily']):
                over = f'这台设备今天的额度用完了（已用 {sym}{spent:.2f}）'
        if not over:
            return None
        to = q.get('downgrade_to') or ''
        current = (getattr(conn, 'hub_route', None) or {}).get('current')
        if q.get('action') == 'downgrade' and to and to != current:
            return {'action': 'downgrade', 'to': to, 'text': over}
        if q.get('action') == 'downgrade' and to and to == current:
            return None          # already on the fallback model: let it answer
        return {'action': 'block', 'text': over + '。明天再来，或者在网关后台调高额度。'}
    except Exception as e:
        logger.bind(tag=TAG).warning(f"检查额度失败: {e}")
        return None


# ---- the admin page and the device ----

def summary(config=None) -> dict:
    cfg = settings(config)
    d = db()
    day, month = _today(cfg)
    q = cfg['quota']
    cur = q.get('currency') or 'CNY'
    since = (datetime.date.fromisoformat(day) - datetime.timedelta(days=29)).isoformat()
    today, this_month = d.spent(day=day), d.spent(month=month)
    return {
        'today': today, 'month': this_month, 'total': d.spent(),
        'tokens': {'today': d.totals('day = ?', (day,)), 'month': d.totals('month = ?', (month,)), 'total': d.totals()},
        'days': d.by('day', 'day >= ?', (since,), limit=60),
        'months': d.by('month', limit=24),
        'models': d.by('model', 'month = ?', (month,)),
        'devices': d.by('device', 'month = ?', (month,)),
        'purposes': d.by('purpose', 'month = ?', (month,)),
        'unpriced': d.unpriced_models(),
        'quota': {**q, 'used_today': today.get(cur, 0), 'used_month': this_month.get(cur, 0)},
        'this_day': day, 'this_month': month, 'currencies': pricing.CURRENCIES,
        'settings': {k: cfg[k] for k in ('timezone', 'holidays', 'workdays', 'count_claude_code')},
        'prices': pricing.rules(cfg), 'prices_default': not cfg['prices'],
    }


def brief(config=None) -> dict:
    """What the device's settings page (and the admin status page) shows: only these few sums, not the
    whole summary() with its per-day / model / device breakdowns."""
    cfg = settings(config)
    d = db()
    day, month = _today(cfg)
    q = cfg['quota']
    cur = q.get('currency') or 'CNY'
    today, this_month = d.spent(day=day), d.spent(month=month)
    return {'today': today, 'month': this_month, 'total': d.spent(),
            'quota': {**q, 'used_today': today.get(cur, 0), 'used_month': this_month.get(cur, 0)},
            'currencies': pricing.CURRENCIES, 'unpriced': d.unpriced_models(),
            'tokens_today': d.totals('day = ?', (day,))}
