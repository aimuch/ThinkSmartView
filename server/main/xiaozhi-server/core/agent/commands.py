"""[xiaozhi-hub] 常用口令不走大模型（设计画布第 53 块）。

识别出的一句话先在这里认一认：日期时间、倒计时、闹钟、取消和查询、音量和静音、亮度和睡眠、播放和学习陪伴，
以及后台加的自定义口令。整句认准了就直接调设备上的同一个工具（或网关自己答），回一句确认，不走大模型；
句子里多一点认不出的东西（重复规则、条件、两件事、上下午分不清）就照常交给大模型。

配置在 data/commands.json（后台「口令」页改）：
  {"enabled": 总开关, "devices": {设备: true/false}, "builtin": {类别: {"enabled", "reply"}},
   "aliases": [{"say": "安静会儿", "means": "静音"}],
   "custom": [{"id", "enabled", "phrases": [...], "action": {"type": page|tool|say|llm, ...}, "reply"}]}
别名：整句换成另一句再认（「安静会儿」当「静音」），内置口令就这样加说法。
自定义口令的说法里可以有空位：{数字} {时长} {时间} {日期} {文字}。
"""
import datetime
import json
import os
import re
import threading
import time
from collections import deque

from config.config_loader import get_project_dir

# ---------------------------------------------------------------- 文字整理

PUNCT = re.compile(r"[\s，,。．.!！?？、~～…·:：;；\"'“”‘’（）()\[\]【】<>《》]+")
LEAD = re.compile(r"^(?:小麦小麦|小麦)?(?:请|麻烦你?|帮我|帮忙|给我|你|就|那就|那)*")
TAIL = re.compile(r"(?:一下|一下子|吧|呀|啊|哦|呢|啦|好吗|好不好|行吗|可以吗|谢谢你?|小麦)+$")
CN_DIGIT = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
CN_RUN = re.compile(r"[零〇一二两三四五六七八九十百]+")


def cn_number(s: str):
    """「二十五」→ 25，「一百」→ 100，「十」→ 10，「三五」→ None（不是一个数）。"""
    if not s:
        return None
    if all(ch in CN_DIGIT for ch in s):
        return CN_DIGIT[s] if len(s) == 1 else None
    total, num = 0, 0
    for ch in s:
        if ch in CN_DIGIT:
            num = CN_DIGIT[ch]
        elif ch == "十":
            total += (num or 1) * 10
            num = 0
        elif ch == "百":
            total += (num or 1) * 100
            num = 0
        else:
            return None
    return total + num


def to_digits(text: str) -> str:
    """把中文数字换成阿拉伯数字（「一下」「一点」这类在整理时已经去掉或照常处理）。"""
    def sub(m):
        n = cn_number(m.group(0))
        return str(n) if n is not None else m.group(0)
    return CN_RUN.sub(sub, text)


def clean(text: str) -> str:
    """去标点、开头的「帮我」「请」、结尾的「一下」「吧」，中文数字换成阿拉伯数字。"""
    t = PUNCT.sub("", str(text or ""))
    t = LEAD.sub("", t)
    t = TAIL.sub("", t)
    return to_digits(t)


# ---------------------------------------------------------------- 时长、钟点、日期

DUR = r"(?:\d+(?:个)?(?:半)?(?:小时|钟头)(?:\d+(?:分钟?|分))?|半(?:个)?(?:小时|钟头)|\d+分(?:钟)?(?:半|\d+秒(?:钟)?)?|\d+秒(?:钟)?)"
PERIOD = r"(?:凌晨|早上|早晨|清晨|上午|中午|下午|傍晚|晚上|夜里|半夜)"
CLOCK = r"(?:\d{1,2})(?:点|:|：)(?:半|1刻|3刻|整|\d{1,2}(?:分)?)?"
WEEK = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7}
DATE = r"(?:今天|今晚|今早|明天|明早|明晚|后天|大后天|(?:下个?|这个?|本)?(?:周|星期|礼拜)[1-7日天一二三四五六]|\d{1,2}月\d{1,2}(?:日|号)|\d{1,2}(?:日|号))"


def parse_duration(s: str):
    """「1个半小时」→ 5400，「3分半」→ 210，「半小时」→ 1800；认不出返回 None。"""
    s = s.replace("个", "").replace("钟头", "小时").replace("钟", "")
    if re.fullmatch(r"半小时", s):
        return 1800
    m = re.fullmatch(r"(\d+)(半)?小时(?:(\d+)分)?", s)
    if m:
        return int(m.group(1)) * 3600 + (1800 if m.group(2) else 0) + int(m.group(3) or 0) * 60
    m = re.fullmatch(r"(\d+)分(?:(半)|(\d+)秒)?", s)
    if m:
        return int(m.group(1)) * 60 + (30 if m.group(2) else int(m.group(3) or 0))
    m = re.fullmatch(r"(\d+)秒", s)
    if m:
        return int(m.group(1))
    return None


def say_duration(sec: int) -> str:
    h, rest = divmod(sec, 3600)
    m, s = divmod(rest, 60)
    parts = []
    if h:
        parts.append(f"{h} 小时")
    if m:
        parts.append(f"{m} 分钟")
    if s:
        parts.append(f"{s} 秒")
    return "".join(parts) or "0 秒"


def parse_clock(period: str, clock: str):
    """(时, 分, 明确不明确)。没说上午下午且 1-11 点：不明确（今晚还是明早），交给大模型。"""
    m = re.fullmatch(r"(\d{1,2})(?:点|:|：)(半|1刻|3刻|整|(\d{1,2})(?:分)?)?", clock)
    if not m:
        return None
    h = int(m.group(1))
    mm = {"半": 30, "1刻": 15, "3刻": 45, "整": 0, None: 0}.get(m.group(2), None)
    if mm is None:
        mm = int(m.group(3))
    if h > 24 or mm > 59:
        return None
    sure = True
    if period in ("下午", "傍晚", "晚上", "夜里") and h < 12:
        h += 12
    elif period == "中午" and h < 6:
        h += 12
    elif period in ("凌晨", "半夜") and h == 12:
        h = 0
    elif not period and 1 <= h <= 11:
        sure = False
    if h == 24:
        h = 0
    return h, mm, sure


def parse_date(date: str, now: datetime.datetime):
    """→ (距今天几天, 说法)；认不出 None。"""
    if not date:
        return 0, ""
    simple = {"今天": 0, "今晚": 0, "今早": 0, "明天": 1, "明早": 1, "明晚": 1, "后天": 2, "大后天": 3}
    if date in simple:
        return simple[date], date
    m = re.fullmatch(r"(下个?|这个?|本)?(?:周|星期|礼拜)([1-7日天一二三四五六])", date)
    if m:
        want = WEEK[m.group(2)]
        today = now.isoweekday()
        delta = (want - today) % 7
        if m.group(1) and m.group(1).startswith("下"):
            delta = (7 - today) + want           # 下周X：下一个自然周里的那天
        elif delta == 0 and not m.group(1):
            delta = 0
        return delta, date
    m = re.fullmatch(r"(?:(\d{1,2})月)?(\d{1,2})(?:日|号)", date)
    if m:
        month = int(m.group(1) or now.month)
        day = int(m.group(2))
        try:
            target = datetime.date(now.year, month, day)
        except ValueError:
            return None
        if target < now.date():
            if m.group(1):
                try:
                    target = datetime.date(now.year + 1, month, day)
                except ValueError:
                    return None
            else:
                nm = (now.month % 12) + 1
                try:
                    target = datetime.date(now.year + (1 if nm == 1 else 0), nm, day)
                except ValueError:
                    return None
        return (target - now.date()).days, date
    return None


WEEKDAY_CN = "一二三四五六日"


def when_text(days: int, h: int, mm: int, now: datetime.datetime) -> str:
    day = {0: "今天", 1: "明天", 2: "后天"}.get(days)
    if day is None:
        d = now.date() + datetime.timedelta(days=days)
        day = f"{d.month} 月 {d.day} 日"
    part = "凌晨" if h < 6 else "早上" if h < 9 else "上午" if h < 12 else "中午" if h < 13 else "下午" if h < 18 else "晚上"
    hh = h if h <= 12 else h - 12
    return f"{day}{part} {hh}:{mm:02d}"


# ---------------------------------------------------------------- 结果

class Plan:
    """认出来以后要做的事。kind：answer 网关直接答；tool 调一个设备工具；steps 先读再做（音量加减、取消某一种）；
    say 只回一句；llm 带着提示交给大模型。"""

    def __init__(self, rule, kind, reply="", tool=None, args=None, steps=None, hint=None, note="", values=None):
        self.rule, self.kind, self.reply, self.tool, self.args = rule, kind, reply, tool, args or {}
        self.steps, self.hint, self.note = steps, hint, note
        self.values = values or {}      # 回复里的空位：{时长} {when} {音量}

    def view(self):
        return {"rule": self.rule, "kind": self.kind, "tool": self.tool, "args": self.args, "reply": self.reply,
                "note": self.note, "hint": self.hint}


CATEGORIES = [
    ("datetime", "日期时间", "今天几号 · 星期几 · 农历几号 · 现在几点", "（直接报日期、时间）"),
    ("timer", "倒计时", "倒计时{时长} · {时长}后提醒我 · {时长}后提醒我{文字}", "好的，{时长}后提醒你"),
    ("alarm", "闹钟", "{日期}{时段}{时间}叫我 · 定个{时间}的闹钟 · {时间}提醒我{文字}", "好的，{when}叫你"),
    ("cancel", "取消、查询", "取消所有闹钟 · 取消倒计时 · 我有哪些闹钟 · 还剩多久", "（按结果说）"),
    ("volume", "音量、静音", "大点声 · 小点声 · 音量调到{数字} · 最大声 · 静音 · 取消静音", "音量 {音量}"),
    ("screen", "亮度、睡眠", "屏幕亮一点 · 调暗 · 亮度调到{数字} · 开启睡眠模式 · 关闭睡眠模式", "好的"),
    ("media", "播放、学习陪伴", "停止播放 · 别放了 · 打开学习陪伴 · 关掉学习陪伴", "好的"),
]
CATEGORY_IDS = [c[0] for c in CATEGORIES]

PAGES = [  # 设备「打开页面」能去的地方（和客户端 PAGES 一致）
    ("home", "首页"), ("work", "工作台"), ("work.rack", "工作台 · 机柜"), ("work.desk", "工作台 · 桌面"),
    ("work.repo", "工作台 · 仓库同步"), ("weather", "天气"), ("focus", "专注"), ("study", "学习记录"),
    ("smart", "家居"), ("settings", "设置"), ("settings.screen", "设置 · 屏幕和声音"), ("settings.looks", "设置 · 外观"),
    ("settings.net", "设置 · 网络"), ("settings.system", "设置 · 系统"), ("settings.alarms", "设置 · 闹钟和作息"),
    ("settings.family", "设置 · 家人"), ("settings.voice", "设置 · 语音"), ("close", "关掉当前页面"),
]
PAGE_IDS = [p[0] for p in PAGES]

DEFAULT_CUSTOM = [{
    "id": "rack", "enabled": True, "phrases": ["打开机柜", "机柜页面", "看看机柜", "打开机柜页面"],
    "action": {"type": "page", "page": "work.rack"}, "reply": "好的",
}]

KIND_NAME = {"alarm": "闹钟", "timer": "倒计时", "reminder": "提醒"}
TIMER_WORDS = r"(?:倒计时|计时器?|定时器?)"


def _builtin(t: str, now: datetime.datetime):
    """内置口令：认出来返回 Plan；认不出 None。t 是 clean 过的整句。"""
    # ---- 日期时间
    if re.fullmatch(r"(?:现在)?(?:几点了?|几点钟|什么时间|几点几分)|现在时间|报时", t):
        return Plan("datetime", "answer", f"现在是 {now.hour}:{now.minute:02d}")
    m = re.fullmatch(r"(今天|明天|后天)(?:是)?(?:几号|几月几号|几月几日|什么日子|(?:星期|周|礼拜)几|日期)", t)
    if m:
        d = now.date() + datetime.timedelta(days={"今天": 0, "明天": 1, "后天": 2}[m.group(1)])
        return Plan("datetime", "answer", f"{m.group(1)}是 {d.month} 月 {d.day} 日，星期{WEEKDAY_CN[d.weekday()]}")
    if re.fullmatch(r"(?:今天)?农历(?:是)?(?:几号|几月几号|多少|哪天)?|今天农历(?:是)?(?:几号|多少)?", t):
        return Plan("datetime", "answer", "", note="lunar")

    # ---- 倒计时
    m = re.fullmatch(rf"(?:定|设|开|开始)?个?(?:{TIMER_WORDS})?({DUR})(?:的)?(?:{TIMER_WORDS}|闹钟)?", t)
    if m and (re.search(TIMER_WORDS, t) or re.fullmatch(rf"(?:定|设)个?{DUR}的?闹钟", t)):
        sec = parse_duration(m.group(1))
        if sec and 0 < sec <= 24 * 3600:
            return _timer_plan(sec)
    m = re.fullmatch(rf"({DUR})(?:以?后|之后)(?:提醒我|叫我|喊我)(.{{0,16}})", t)
    if m:
        sec = parse_duration(m.group(1))
        if sec and 0 < sec <= 24 * 3600:
            what = m.group(2)
            if what and re.search(r"\d|每天|每周|工作日|如果|然后|再", what):
                return None                         # 「提醒我每天……」「……然后……」：交给大模型
            return _timer_plan(sec, what)

    # ---- 闹钟
    m = re.fullmatch(rf"(?:定|设|订)?个?({DATE})?({PERIOD})?({CLOCK})(?:的)?(?:叫我(?:起床)?|叫醒我|喊我(?:起床)?|闹钟|提醒我(.{{0,16}}))", t)
    if m:
        return _alarm_plan(m.group(1), m.group(2), m.group(3), m.group(4), now, t)

    # ---- 取消、查询
    m = re.fullmatch(r"(?:取消|删掉|删除|关掉|关闭|清掉|清空)(所有的?|全部的?)?(闹钟|倒计时|计时器?|定时器?|提醒)(?:全部|都取消)?", t)
    if m:
        kind = {"闹钟": "alarm", "提醒": "reminder"}.get(m.group(2), "timer")
        if m.group(1) or t.endswith("全部"):
            return Plan("cancel", "tool", "{结果}", tool="self.alarm.cancel", args={"all": True, "kind": kind},
                        note=f"取消所有{KIND_NAME[kind]}")
        return Plan("cancel", "steps", "", steps="cancel_one", args={"kind": kind}, note=f"只有一个{KIND_NAME[kind]}就取消它，有好几个交给大模型问清楚")
    if re.fullmatch(r"(?:取消|删掉)(?:所有|全部)(?:的)?(?:闹钟和倒计时|提醒和闹钟)?|全部取消", t):
        return Plan("cancel", "tool", "{结果}", tool="self.alarm.cancel", args={"all": True}, note="全部取消")
    if re.fullmatch(r"(?:我)?(?:有|设了|定了|还有)?(?:哪些|几个|什么)(?:闹钟|提醒|倒计时)|(?:看看|查查|查一下)?(?:我的)?(?:闹钟|提醒)(?:有哪些)?|闹钟列表", t):
        return Plan("cancel", "steps", "", steps="list", args={}, note="读闹钟列表念出来")
    if re.fullmatch(r"(?:倒计时)?还(?:剩|有)多久|倒计时还剩多少", t):
        return Plan("cancel", "steps", "", steps="list", args={"kind": "timer"}, note="读倒计时还剩多久")

    # ---- 音量、静音
    if re.fullmatch(r"(?:声音|音量)?(?:大|高|响)(?:1点|点|些)(?:声)?|大点声|大声点|(?:声音|音量)(?:调|开)(?:大|高)(?:1点|点)?", t):
        return Plan("volume", "steps", "", steps="volume", args={"change": 15}, note="读当前音量再加 15")
    if re.fullmatch(r"(?:声音|音量)?(?:小|低|轻)(?:1点|点|些)(?:声)?|小点声|小声点|(?:声音|音量)(?:调|开)(?:小|低)(?:1点|点)?", t):
        return Plan("volume", "steps", "", steps="volume", args={"change": -15}, note="读当前音量再减 15")
    m = re.fullmatch(r"(?:声音|音量)(?:调|开|设|设置)?(?:到|成|为)?(\d{1,3})(?:%|的音量)?", t)
    if m and 0 <= int(m.group(1)) <= 100:
        return _volume_plan(int(m.group(1)))
    if re.fullmatch(r"(?:声音|音量)?(?:开|调)?(?:到)?最大(?:声|音量)?|最大声", t):
        return _volume_plan(100)
    if re.fullmatch(r"(?:声音|音量)?(?:开|调)?(?:到)?最小(?:声|音量)?", t):
        return _volume_plan(10)
    if re.fullmatch(r"静音|别出声|安静点?|关掉声音|关闭声音", t):
        return Plan("volume", "steps", "", steps="mute", args={}, note="记下现在的音量再调到 0")
    if re.fullmatch(r"取消静音|恢复声音|打开声音|开声音|恢复音量", t):
        return Plan("volume", "steps", "", steps="unmute", args={}, note="调回静音前的音量")

    # ---- 亮度、睡眠
    if re.fullmatch(r"(?:屏幕)?(?:亮|调亮)(?:1点|点|些)|屏幕调亮|(?:把)?屏幕(?:调)?亮(?:1点|点)?", t):
        return Plan("screen", "tool", "好的", tool="self.screen.set_brightness", args={"change": 20})
    if re.fullmatch(r"(?:屏幕)?(?:暗|调暗)(?:1点|点|些)|屏幕调暗|(?:把)?屏幕(?:调)?暗(?:1点|点)?", t):
        return Plan("screen", "tool", "好的", tool="self.screen.set_brightness", args={"change": -20})
    m = re.fullmatch(r"(?:屏幕)?亮度(?:调|设|设置)?(?:到|成|为)?(\d{1,3})(?:%)?", t)
    if m and 0 <= int(m.group(1)) <= 100:
        return Plan("screen", "tool", f"亮度 {int(m.group(1))}", tool="self.screen.set_brightness", args={"brightness": int(m.group(1))})
    if re.fullmatch(r"(?:屏幕)?(?:调到)?最亮", t):
        return Plan("screen", "tool", "好的", tool="self.screen.set_brightness", args={"brightness": 100})
    if re.fullmatch(r"(?:屏幕)?(?:调到)?最暗", t):
        return Plan("screen", "tool", "好的", tool="self.screen.set_brightness", args={"brightness": 0})
    if re.fullmatch(r"(?:开启|打开|进入|开)睡眠模式", t):
        return Plan("screen", "tool", "好的，睡眠模式打开了", tool="self.sleep_mode.set", args={"enabled": True})
    if re.fullmatch(r"(?:关闭|关掉|退出|关)睡眠模式", t):
        return Plan("screen", "tool", "好的，睡眠模式关了", tool="self.sleep_mode.set", args={"enabled": False})

    # ---- 播放、学习陪伴
    if re.fullmatch(r"(?:停止|关掉|关闭|停)(?:播放|音乐|电台|广播|收音机|助眠声|雨声|白噪音)|别放了|停止播放|不听了", t):
        return Plan("media", "tool", "好的", tool="self.media.play", args={"action": "stop"})
    if re.fullmatch(r"(?:打开|开启|开)学习陪伴|看着我写作业", t):
        return Plan("media", "tool", "{结果}", tool="self.study.control", args={"action": "on"})
    if re.fullmatch(r"(?:关闭|关掉|关)学习陪伴", t):
        return Plan("media", "tool", "{结果}", tool="self.study.control", args={"action": "off"})
    return None


def _timer_plan(sec, what=""):
    h, rest = divmod(sec, 3600)
    m, s = divmod(rest, 60)
    args = {k: v for k, v in (("hours", h), ("minutes", m), ("seconds", s)) if v}
    values = {"时长": say_duration(sec), "文字": what}
    if what:
        return Plan("timer", "tool", f"好的，{say_duration(sec)}后提醒你{what}", tool="self.alarm.create",
                    args={"kind": "reminder", **args, "say": what, "label": what[:10]}, values=values)
    return Plan("timer", "tool", f"好的，{say_duration(sec)}后提醒你", tool="self.alarm.create",
                args={"kind": "timer", **args}, values=values)


def _alarm_plan(date, period, clock, what, now, t):
    parsed = parse_clock(period or ("早上" if date in ("今早", "明早") else "晚上" if date in ("今晚", "明晚") else ""), clock)
    if not parsed:
        return None
    h, mm, sure = parsed
    when = parse_date(date or "", now)
    if when is None:
        return None
    days, _ = when
    if not sure:
        if days >= 1 and 5 <= h <= 11:
            sure = True                       # 「明天七点叫我」：早上
        else:
            return None                       # 「七点叫我」：今晚还是明早？交给大模型问
    if not date and (h, mm) <= (now.hour, now.minute):
        days = 1                              # 今天这个点已经过了：明天
    if what and re.search(r"\d|每天|每周|工作日|如果|然后|再", what):
        return None
    args = {"time": f"{h:02d}:{mm:02d}"}
    if days:
        args["days_from_today"] = days
    w = when_text(days, h, mm, now)
    values = {"when": w, "文字": what or ""}
    if what:
        return Plan("alarm", "tool", f"好的，{w} 提醒你{what}", tool="self.alarm.create",
                    args={"kind": "reminder", **args, "say": what, "label": what[:10]}, values=values)
    return Plan("alarm", "tool", f"好的，{w} 叫你", tool="self.alarm.create", args={"kind": "alarm", **args}, values=values)


def _volume_plan(v):
    return Plan("volume", "tool", f"音量 {v}", tool="self.audio_speaker.set_volume", args={"volume": v}, values={"音量": v})


# ---------------------------------------------------------------- 自定义口令

SLOTS = {"数字": r"(\d{1,4})", "时长": rf"({DUR})", "时间": rf"({PERIOD}?{CLOCK})", "日期": rf"({DATE})", "文字": r"(.{1,20}?)"}


def phrase_regex(phrase: str):
    """「打开{文字}页面」→ 正则和空位名单。说法本身也按 clean 整理（数字、去标点）。"""
    names, out, pos = [], "", 0
    for m in re.finditer(r"\{(数字|时长|时间|日期|文字)\}", phrase):
        out += re.escape(clean(phrase[pos:m.start()]))
        out += SLOTS[m.group(1)]
        names.append(m.group(1))
        pos = m.end()
    out += re.escape(clean(phrase[pos:]))
    return re.compile(f"^{out}$"), names


def fill(template, values: dict):
    if not isinstance(template, str):
        return template
    whole = re.fullmatch(r"\{(数字|时长|时间|日期|文字|结果|音量|when)\}", template)
    if whole and whole.group(1) in values:
        return values[whole.group(1)]          # 整个值就是一个空位：保留数字类型
    return re.sub(r"\{(数字|时长|时间|日期|文字|结果|音量|when)\}", lambda m: str(values.get(m.group(1), m.group(0))), template)


def _custom(t: str, rules: list):
    for r in rules:
        if not r.get("enabled", True):
            continue
        for p in r.get("phrases") or []:
            rx, names = phrase_regex(p)
            m = rx.fullmatch(t)
            if not m:
                continue
            values = {}
            for name, val in zip(names, m.groups()):
                if name == "数字":
                    values[name] = int(val)
                elif name == "时长":
                    values[name] = parse_duration(val) or val
                else:
                    values[name] = val
            a = r.get("action") or {}
            reply = fill(r.get("reply") or "", values)
            rule = f"custom:{r.get('id')}"
            kind = a.get("type")
            if kind == "page":
                return Plan(rule, "tool", reply, tool="self.screen.open_page", args={"page": a.get("page")})
            if kind == "tool" and a.get("tool"):
                args = {k: fill(v, values) for k, v in (a.get("args") or {}).items()}
                return Plan(rule, "tool", reply, tool=a["tool"], args=args)
            if kind == "say":
                return Plan(rule, "say", fill(a.get("text") or reply, values))
            if kind == "llm":
                return Plan(rule, "llm", hint=fill(a.get("hint") or "", values))
    return None


# ---------------------------------------------------------------- 配置

_lock = threading.Lock()


def _path() -> str:
    return os.path.join(get_project_dir(), "data", "commands.json")


def defaults() -> dict:
    return {"enabled": True, "devices": {},
            "builtin": {c: {"enabled": True, "reply": ""} for c in CATEGORY_IDS},
            "aliases": [], "custom": [dict(r) for r in DEFAULT_CUSTOM]}


def _aliases(raw) -> list:
    out = []
    for a in raw or []:
        if isinstance(a, dict) and str(a.get("say") or "").strip() and str(a.get("means") or "").strip():
            out.append({"say": str(a["say"]).strip()[:30], "means": str(a["means"]).strip()[:30]})
    return out[:100]


def load() -> dict:
    d = defaults()
    try:
        with open(_path(), encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return d
    if isinstance(raw, dict):
        if isinstance(raw.get("enabled"), bool):
            d["enabled"] = raw["enabled"]
        if isinstance(raw.get("devices"), dict):
            d["devices"] = {str(k): v for k, v in raw["devices"].items() if isinstance(v, bool)}
        for c in CATEGORY_IDS:
            b = (raw.get("builtin") or {}).get(c)
            if isinstance(b, dict):
                d["builtin"][c] = {"enabled": b.get("enabled") is not False, "reply": str(b.get("reply") or "")[:60]}
        d["aliases"] = _aliases(raw.get("aliases"))
        if isinstance(raw.get("custom"), list):
            d["custom"] = [r for r in (validate_rule(x) for x in raw["custom"]) if r]
    return d


def validate_rule(r):
    """自定义口令：说法至少一个，动作是 page / tool / say / llm；不对返回 None。"""
    if not isinstance(r, dict):
        return None
    phrases = [str(p).strip()[:30] for p in r.get("phrases") or [] if str(p).strip()][:20]
    a = r.get("action") if isinstance(r.get("action"), dict) else {}
    kind = a.get("type")
    if not phrases or kind not in ("page", "tool", "say", "llm"):
        return None
    if kind == "page" and a.get("page") not in PAGE_IDS:
        return None
    if kind == "tool" and not re.fullmatch(r"self\.[a-z_]+(?:\.[a-z_]+)+", str(a.get("tool") or "")):
        return None
    action = {"type": kind}
    if kind == "page":
        action["page"] = a["page"]
    elif kind == "tool":
        action["tool"] = a["tool"]
        action["args"] = a.get("args") if isinstance(a.get("args"), dict) else {}
    elif kind == "say":
        action["text"] = str(a.get("text") or "")[:100]
    else:
        action["hint"] = str(a.get("hint") or "")[:200]
    rid = re.sub(r"[^0-9a-zA-Z_-]", "", str(r.get("id") or "")) or f"c{int(time.time() * 1000) % 10 ** 9}"
    return {"id": rid[:20], "enabled": r.get("enabled") is not False, "phrases": phrases, "action": action,
            "reply": str(r.get("reply") or "")[:60]}


def save(d: dict) -> dict:
    """后台保存：整理后写盘，返回存下的样子。"""
    clean_d = defaults()
    clean_d["enabled"] = d.get("enabled") is not False
    clean_d["devices"] = {str(k): v for k, v in (d.get("devices") or {}).items() if isinstance(v, bool)}
    for c in CATEGORY_IDS:
        b = (d.get("builtin") or {}).get(c) or {}
        clean_d["builtin"][c] = {"enabled": b.get("enabled") is not False, "reply": str(b.get("reply") or "")[:60]}
    clean_d["aliases"] = _aliases(d.get("aliases"))
    clean_d["custom"] = [r for r in (validate_rule(x) for x in d.get("custom") or []) if r][:100]
    path = _path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with _lock:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(clean_d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    return clean_d


def active(cfg: dict, device: str) -> bool:
    v = (cfg.get("devices") or {}).get(device or "")
    return v if isinstance(v, bool) else cfg.get("enabled", True)


# ---------------------------------------------------------------- 认一句话

def match(text: str, cfg: dict = None, now: datetime.datetime = None):
    """→ (Plan 或 None, 整理后的句子)。先自定义，再内置；内置的类别关了就跳过。"""
    cfg = cfg or load()
    now = now or datetime.datetime.now()
    t = clean(text)
    if not t or len(t) > 40:
        return None, t
    for a in cfg.get("aliases") or []:               # 别名：整句换成另一句再认
        if clean(a["say"]) == t:
            t = clean(a["means"])
            break
    plan = _custom(t, cfg.get("custom") or [])
    if plan:
        return plan, t
    builtin = cfg.get("builtin") or {}
    plan = _builtin(t, now)
    if plan and not (builtin.get(plan.rule) or {}).get("enabled", True):
        return None, t
    override = (builtin.get(plan.rule) or {}).get("reply") if plan else ""
    if override and plan.kind in ("tool", "answer"):
        plan.reply = fill(override, {**plan.values, "结果": "{结果}"}) if "{" in override else override
    return plan, t


def explain(text: str, cfg: dict = None, now: datetime.datetime = None) -> dict:
    """后台「试一试」。"""
    plan, t = match(text, cfg, now)
    if not plan:
        why = "句子太长" if len(t) > 40 else "没有认准的口令"
        return {"matched": False, "clean": t, "why": why}
    if plan.kind == "llm":
        return {"matched": False, "clean": t, "why": f"口令要求交给大模型（附提示：{plan.hint or '无'}）", **plan.view()}
    return {"matched": True, "clean": t, **plan.view()}


# ---------------------------------------------------------------- 统计（最近 7 天，存盘）

_stats_lock = threading.Lock()
_recent = deque(maxlen=200)


def _stats_path() -> str:
    return os.path.join(get_project_dir(), "data", "commands_stats.json")


def record(device: str, text: str, outcome: str, rule: str = None):
    """outcome：fast（不走大模型）/ llm（交给大模型）。"""
    now = time.time()
    _recent.appendleft({"t": int(now), "device": device, "text": str(text)[:40], "outcome": outcome, "rule": rule})
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    with _stats_lock:
        try:
            with open(_stats_path(), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        days = data.setdefault("days", {})
        d = days.setdefault(day, {"fast": 0, "llm": 0})
        d[outcome] = d.get(outcome, 0) + 1
        if outcome == "llm":
            misses = data.setdefault("misses", {})
            key = clean(text)
            if key and len(key) <= 16:
                misses[key] = misses.get(key, 0) + 1
                if len(misses) > 300:
                    for k in sorted(misses, key=misses.get)[:100]:
                        misses.pop(k, None)
        for k in sorted(days)[:-7]:
            days.pop(k, None)
        try:
            os.makedirs(os.path.dirname(_stats_path()), exist_ok=True)
            with open(_stats_path(), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except OSError:
            pass


def stats() -> dict:
    try:
        with open(_stats_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    days = data.get("days") or {}
    fast = sum(d.get("fast", 0) for d in days.values())
    llm = sum(d.get("llm", 0) for d in days.values())
    misses = sorted((data.get("misses") or {}).items(), key=lambda kv: -kv[1])[:10]
    return {"fast": fast, "llm": llm, "misses": [{"text": k, "count": v} for k, v in misses], "recent": list(_recent)[:30]}
