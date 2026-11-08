"""[xiaozhi-hub] Model prices: per vendor/model, per million tokens, with time-of-day tiers.

A price rule (billing.prices[] in data/.config.yaml, edited on the admin page 费用):
  {"match": "deepseek-flash, deepseek-v4-flash",   # model names (comma separated, * wildcards)
   "currency": "CNY",
   "input": 1, "cache_hit": 0.02, "output": 4,      # the default tier (idle hours here)
   "tiers": [{"name": "高峰", "days": "workday", "hours": "09:00-12:00,14:00-18:00",
              "input": 2, "cache_hit": 0.04, "output": 8}]}
The first tier whose days/hours contain the call's time wins, else the rule's own prices.
days: "workday" (Mon-Fri, minus billing.holidays, plus billing.workdays: China's 调休), "weekend",
"all", or weekday numbers "1-5" / "6,7". Times are in billing.timezone (default Asia/Shanghai).

DEFAULT_PRICES: only prices checked against the vendor's own page (date in "checked"); everything
else starts unpriced and the admin page asks for its prices.
"""
import datetime
import fnmatch
from zoneinfo import ZoneInfo

DEFAULT_TZ = 'Asia/Shanghai'
CURRENCIES = {'CNY': '¥', 'USD': '$'}

DEFAULT_PRICES = [
    {'match': 'deepseek-flash, deepseek-v4-flash, deepseek-v4-flash-vision-exp', 'label': 'DeepSeek Flash',
     'currency': 'CNY', 'input': 1, 'cache_hit': 0.02, 'output': 4,
     'tiers': [{'name': '高峰', 'days': 'workday', 'hours': '09:00-12:00,14:00-18:00',
                'input': 2, 'cache_hit': 0.04, 'output': 8}],
     'checked': '2026-11-08 api-docs.deepseek.com：空闲时段为高峰的一半'},
    {'match': 'deepseek-v4-pro', 'label': 'DeepSeek V4 Pro',
     'currency': 'CNY', 'input': 4.5, 'cache_hit': 0.15, 'output': 13.5,
     'tiers': [{'name': '高峰', 'days': 'workday', 'hours': '09:00-12:00,14:00-18:00',
                'input': 9, 'cache_hit': 0.3, 'output': 27}],
     'checked': '2026-11-08 api-docs.deepseek.com：空闲时段为高峰的一半'},
]
PRICE_KEYS = ('input', 'cache_hit', 'output')


def tz(cfg):
    try:
        return ZoneInfo(str((cfg or {}).get('timezone') or DEFAULT_TZ))
    except Exception:
        return ZoneInfo(DEFAULT_TZ)


def _models(rule):
    return [m.strip().lower() for m in str(rule.get('match') or '').split(',') if m.strip()]


def find_rule(rules, model):
    m = str(model or '').lower()
    for rule in rules or []:
        if any(fnmatch.fnmatchcase(m, pat) for pat in _models(rule)):
            return rule
    return None


def _day_ok(spec, when, cfg):
    spec = str(spec or 'all').strip().lower()
    date = when.date().isoformat()
    holidays = {str(d) for d in (cfg or {}).get('holidays') or []}
    workdays = {str(d) for d in (cfg or {}).get('workdays') or []}
    is_work = (date in workdays) or (when.isoweekday() <= 5 and date not in holidays)
    if spec == 'all':
        return True
    if spec == 'workday':
        return is_work
    if spec == 'weekend':
        return not is_work
    days = set()
    for part in spec.replace('，', ',').split(','):
        part = part.strip()
        if '-' in part:
            a, b = part.split('-', 1)
            days.update(range(int(a), int(b) + 1))
        elif part:
            days.add(int(part))
    return when.isoweekday() in days


def _hours_ok(spec, when):
    spec = str(spec or '').strip()
    if not spec:
        return True
    now = when.hour * 60 + when.minute
    for part in spec.replace('，', ',').split(','):
        if '-' not in part:
            continue
        a, b = (x.strip() for x in part.split('-', 1))
        start = int(a[:2]) * 60 + int(a[3:5])
        end = int(b[:2]) * 60 + int(b[3:5])
        if (start <= now < end) if start <= end else (now >= start or now < end):   # may cross midnight
            return True
    return False


def price_at(rule, when, cfg=None):
    """(tier name, {input, cache_hit, output} per million) for a call at `when` (aware datetime)."""
    local = when.astimezone(tz(cfg))
    for tier in rule.get('tiers') or []:
        if _day_ok(tier.get('days'), local, cfg) and _hours_ok(tier.get('hours'), local):
            return tier.get('name') or '时段', {k: float(tier.get(k, rule.get(k)) or 0) for k in PRICE_KEYS}
    return rule.get('default_name') or '标准', {k: float(rule.get(k) or 0) for k in PRICE_KEYS}


def cost(usage, prices):
    """Money for one call. usage: prompt (all input tokens), cache_hit (part of prompt), completion."""
    hit = min(usage.get('cache_hit') or 0, usage.get('prompt') or 0)
    miss = (usage.get('prompt') or 0) - hit
    return (miss * prices['input'] + hit * prices['cache_hit'] + (usage.get('completion') or 0) * prices['output']) / 1e6


def rules(cfg):
    """The configured rules, or the defaults when none are set yet."""
    configured = (cfg or {}).get('prices')
    return configured if isinstance(configured, list) and configured else DEFAULT_PRICES


def now():
    return datetime.datetime.now(datetime.timezone.utc)
