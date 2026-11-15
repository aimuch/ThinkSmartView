"""[xiaozhi-hub] 清单里一项的重复规则（设计画布第 76 块）：算下一次在哪天、用一句中文说出来。

规则 {"freq", "interval", ...}：
- daily：每 interval 天
- workdays：周一到周五（没有法定调休的数据：调休上班的周六日不算，放假的工作日照算）
- weekly：每 interval 周的 days（1=周一 … 7=周日，可多个）
- monthly：每 interval 个月的 monthday 号（-1 = 最后一天；比月份长的号落在月底），或 nth = [第几个(1-4，-1 = 最后一个), 周几]
- yearly：每 interval 年的 month 月 day 日（2 月 29 日在平年落在 28 日）
- lunar：农历每年的 month 月 day 日（不算闰月；那个月没有三十就落在廿九）
结束：until（"YYYY-MM-DD"，含这天）或 count（一共几次）。
"""
import calendar
import datetime

FREQS = ("daily", "workdays", "weekly", "monthly", "yearly", "lunar")
WEEK = "一二三四五六日"
CN_NUM = "零一二三四五六七八九十"


def _int(v, lo, hi, default=None):
    try:
        v = int(v)
    except (TypeError, ValueError):
        return default
    return v if lo <= v <= hi else default


def normalize(rule):
    """None，或者整理好的规则；不认识的 ValueError（一句中文）。"""
    if not rule:
        return None
    if isinstance(rule, str):
        rule = {"freq": rule}
    if not isinstance(rule, dict):
        raise ValueError("重复规则不对")
    freq = rule.get("freq")
    if freq not in FREQS:
        raise ValueError("重复只能是每天、工作日、每周、每月、每年或农历每年")
    out = {"freq": freq, "interval": _int(rule.get("interval"), 1, 99, 1)}
    if freq == "weekly":
        days = sorted({d for d in (_int(x, 1, 7) for x in rule.get("days") or []) if d})
        if not days:
            raise ValueError("每周要说是周几")
        out["days"] = days
    elif freq == "monthly":
        nth = rule.get("nth")
        if isinstance(nth, (list, tuple)) and len(nth) == 2 and _int(nth[0], -1, 4) not in (None, 0) and _int(nth[1], 1, 7):
            out["nth"] = [int(nth[0]), int(nth[1])]
        else:
            md = _int(rule.get("monthday"), -1, 31)
            if md in (None, 0):
                raise ValueError("每月要说是几号")
            out["monthday"] = md
    elif freq in ("yearly", "lunar"):
        m, d = _int(rule.get("month"), 1, 12), _int(rule.get("day"), 1, 31 if freq == "yearly" else 30)
        if not (m and d):
            raise ValueError("每年要说是几月几日")
        out.update(month=m, day=d)
        if freq == "lunar":
            out["interval"] = 1
    if rule.get("until"):
        try:
            out["until"] = datetime.date.fromisoformat(str(rule["until"])).isoformat()
        except ValueError:
            raise ValueError("结束日期不对")
    elif _int(rule.get("count"), 1, 9999):
        out["count"] = int(rule["count"])
    return out


def _month_add(d: datetime.date, months: int):
    m = d.month - 1 + months
    return d.year + m // 12, m % 12 + 1


def _in_month(rule, y, m):
    last = calendar.monthrange(y, m)[1]
    if "nth" in rule:
        n, wd = rule["nth"]
        days = [x for x in range(1, last + 1) if datetime.date(y, m, x).isoweekday() == wd]
        return datetime.date(y, m, days[n - 1] if n > 0 else days[-1]) if len(days) >= abs(n) else None
    md = rule["monthday"]
    return datetime.date(y, m, last if md == -1 else min(md, last))


def _lunar_md(d: datetime.date):
    import cnlunar

    lu = cnlunar.Lunar(datetime.datetime(d.year, d.month, d.day, 12), godType="8char")
    return lu.lunarMonth, lu.lunarDay, bool(lu.isLunarLeapMonth)


def _next_lunar(rule, after: datetime.date):
    want_m, want_d = rule["month"], rule["day"]
    d = after + datetime.timedelta(days=1)
    for _ in range(400):
        m, day, leap = _lunar_md(d)
        if not leap and m == want_m:
            if day == want_d:
                return d
            if want_d == 30 and day == 29:
                nm, _nd, _nl = _lunar_md(d + datetime.timedelta(days=1))
                if nm != m:          # 这个月只有廿九
                    return d
        d += datetime.timedelta(days=1)
    return None


def next_due(rule, current: datetime.date, done_count: int = 1):
    """current 那次之后的下一次（严格晚于它）；规则结束了返回 None。done_count：算上 current 已经完成了几次。"""
    rule = normalize(rule)
    if not rule:
        return None
    if rule.get("count") and done_count >= rule["count"]:
        return None
    f, n = rule["freq"], rule["interval"]
    nxt = None
    if f == "daily":
        nxt = current + datetime.timedelta(days=n)
    elif f == "workdays":
        nxt = current + datetime.timedelta(days=1)
        while nxt.isoweekday() > 5:
            nxt += datetime.timedelta(days=1)
    elif f == "weekly":
        anchor = current - datetime.timedelta(days=current.isoweekday() - 1)
        d = current + datetime.timedelta(days=1)
        for _ in range(7 * n * 2 + 7):
            week = (d - anchor).days // 7
            if week % n == 0 and d.isoweekday() in rule["days"]:
                nxt = d
                break
            d += datetime.timedelta(days=1)
    elif f == "monthly":
        for k in range(0, 13 * n, n):
            y, m = _month_add(current, k)
            c = _in_month(rule, y, m)
            if c and c > current:
                nxt = c
                break
    elif f == "yearly":
        for k in range(0, 5 * n + 1, n):
            y = current.year + k
            day = min(rule["day"], calendar.monthrange(y, rule["month"])[1])
            c = datetime.date(y, rule["month"], day)
            if c > current:
                nxt = c
                break
    elif f == "lunar":
        nxt = _next_lunar(rule, current)
    if nxt and rule.get("until") and nxt > datetime.date.fromisoformat(rule["until"]):
        return None
    return nxt


def first_due(rule, today: datetime.date):
    """还没定日子的重复项：今天起第一次（今天也算）。"""
    rule = normalize(rule)
    if not rule:
        return None
    rule = {k: v for k, v in rule.items() if k != "count"}
    if rule["freq"] == "daily":
        return today
    return next_due(rule, today - datetime.timedelta(days=1))


def _cn(n: int) -> str:
    if n <= 10:
        return CN_NUM[n]
    if n < 20:
        return "十" + CN_NUM[n - 10]
    return CN_NUM[n // 10] + "十" + (CN_NUM[n % 10] if n % 10 else "")


LUNAR_MONTH = ["正", "二", "三", "四", "五", "六", "七", "八", "九", "十", "冬", "腊"]


def lunar_day_text(d: int) -> str:
    if d <= 10:
        return "初" + _cn(d)
    if d < 20:
        return "十" + CN_NUM[d - 10]
    if d == 20:
        return "二十"
    if d < 30:
        return "廿" + CN_NUM[d - 20]
    return "三十"


def describe(rule) -> str:
    """「每月 15 号」「每两周的周一、周四」「农历每年腊月初八」……；没有规则是空字符串。"""
    try:
        rule = normalize(rule)
    except ValueError:
        return ""
    if not rule:
        return ""
    f, n = rule["freq"], rule["interval"]
    every = "每" + ("" if n == 1 else ("两" if n == 2 else str(n)))
    if f == "daily":
        text = "每天" if n == 1 else f"每 {n} 天"
    elif f == "workdays":
        text = "工作日"
    elif f == "weekly":
        days = "、".join("周" + WEEK[d - 1] for d in rule["days"])
        text = (f"每周{days}" if n == 1 else f"{every}周的{days}") if len(rule["days"]) < 7 else ("每天" if n == 1 else f"{every}周")
    elif f == "monthly":
        unit = "每月" if n == 1 else f"{every}个月的"
        if "nth" in rule:
            k, wd = rule["nth"]
            text = f"{unit}{'最后一个' if k == -1 else f'第{_cn(k)}个'}周{WEEK[wd - 1]}"
        else:
            text = f"{unit}{'最后一天' if rule['monthday'] == -1 else ' ' + str(rule['monthday']) + ' 号'}"
    elif f == "yearly":
        text = ("每年" if n == 1 else f"每 {n} 年") + f" {rule['month']} 月 {rule['day']} 日"
    else:
        text = f"农历每年{LUNAR_MONTH[rule['month'] - 1]}月{lunar_day_text(rule['day'])}"
    if rule.get("until"):
        text += f"，到 {rule['until']}"
    elif rule.get("count"):
        text += f"，共 {rule['count']} 次"
    return text
