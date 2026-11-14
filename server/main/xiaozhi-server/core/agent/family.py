"""[xiaozhi-hub] 孩子和学习（设计画布第 61 块）：后台按孩子组织；儿童模式全家一份；学一学的内容；记录和周报。

- 孩子：「家人声音」里标了孩子的人（core/utils/local_voiceprint.py），加上后台手动加的（还没录声音的）。
  每个孩子：称呼、年龄、能用的媒体分区（媒体页的儿童区，None 是全部）。认出是他在说时，媒体只放他能用的分区、按他记进度。
- 儿童模式：每天最多聊多久（看视频也算）、几点后不陪聊、童声 —— 全家一份（rules），带「改的时间」updated；
  每台开不开、几岁还是按设备（core/utils/device_prefs.py）。设备每次连上和每 20 分钟问一次 /kiosk/kids，
  网关的比设备上次用的新就照网关的；设备设置页上改了发 /kiosk/prefs，网关记成最新的。谁新听谁的，不会两边打架。
- 学一学：认字卡的字表（按顺序出，记住出到哪）、古诗清单、口算范围、十万个为什么的题目。设备点「学一学」时问
  /kiosk/kids/learn，拿一段写进提示词的话；网关连不上时设备用自带的说法。
- 记录：设备报聊天秒数、学一学、学习陪伴摘要、放了儿童区的什么；按天、按设备存 data/kid_records.json，留 60 天。
  只有分钟数、次数和名字，没有照片和聊天内容。
配置在 data/kids.json。
"""
import datetime
import json
import os
import random
import re
import threading
import time

from config.config_loader import get_project_dir

DEFAULT_RULES = {"daily_minutes": 30, "bedtime": "20:30", "voice": "child", "updated": 0}
DEFAULT_CHARS = ("人口手日月水火山石田土木禾竹米上下大小中天地左右多少来去出入"
                 "一二三四五六七八九十个子女儿门马牛羊鸟鱼虫花草果爸妈哥弟姐妹")
DEFAULT_POEMS = ["静夜思", "春晓", "咏鹅", "悯农（锄禾日当午）", "登鹳雀楼", "江雪", "画", "池上", "小池", "一去二三里"]
DEFAULT_WHY = ["天为什么是蓝的", "月亮为什么会变形状", "鱼为什么能在水里呼吸", "下雨前为什么会打雷", "树叶秋天为什么变黄"]
MATH = {"10": "10 以内的加减法", "20": "20 以内的加减法（可以进位、退位）", "100": "100 以内的加减法", "mul": "乘法口诀（九九乘法表）"}
LEARN_KINDS = {"chars": "认字卡", "poem": "背古诗", "math": "口算", "why": "十万个为什么"}
KEEP_DAYS = 60
TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

_lock = threading.Lock()


def _path() -> str:
    return os.path.join(get_project_dir(), "data", "kids.json")


def _records_path() -> str:
    return os.path.join(get_project_dir(), "data", "kid_records.json")


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path, d):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def load() -> dict:
    raw = _read(_path())
    rules = dict(DEFAULT_RULES)
    rules.update({k: v for k, v in (raw.get("rules") or {}).items() if k in DEFAULT_RULES})
    learn = {"chars": DEFAULT_CHARS, "char_pos": 0, "poems": list(DEFAULT_POEMS), "math": "10", "why": list(DEFAULT_WHY),
             "poem_done": []}
    learn.update({k: v for k, v in (raw.get("learn") or {}).items() if k in learn})
    children = {str(k): v for k, v in (raw.get("children") or {}).items() if isinstance(v, dict)}
    return {"rules": rules, "learn": learn, "children": children}


def save(d: dict):
    with _lock:
        _write(_path(), d)


# ---------------------------------------------------------------- 孩子

def children(d: dict = None) -> list:
    """全家的孩子：家人声音里标了孩子的 + 手动加的。[{name, call, age, sections, samples, voice, manual}]"""
    from core.utils import local_voiceprint

    d = d or load()
    try:
        voices = {p["name"]: p for p in local_voiceprint.people() if p.get("child")}
    except Exception:  # noqa: BLE001  声纹库读不了：只列手动加的
        voices = {}
    out = []
    for name in list(voices) + [n for n in d["children"] if n not in voices]:
        c = d["children"].get(name) or {}
        if name not in voices and not c.get("manual"):
            continue                              # 家人声音里删掉了、又不是手动加的
        out.append({"name": name, "call": str(c.get("call") or ""), "age": int(c.get("age") or 0) or None,
                    "sections": c.get("sections") if isinstance(c.get("sections"), list) else None,
                    "samples": (voices.get(name) or {}).get("samples", 0), "voice": name in voices, "manual": bool(c.get("manual"))})
    return out


def child(name: str, d: dict = None):
    return next((c for c in children(d) if c["name"] == name), None)


def allowed_sections(name: str):
    """这个孩子能用的媒体分区 id（None：儿童区全部）。"""
    c = child(name) if name else None
    return c["sections"] if c else None


# ---------------------------------------------------------------- 儿童模式（全家一份）

def normalize_rules(v: dict, base: dict) -> dict:
    out = dict(base)
    if "daily_minutes" in v:
        m = int(v["daily_minutes"])
        if not 0 <= m <= 600:
            raise ValueError("每天最多聊的时间要在 0 到 600 分钟之间（0 是不限）")
        out["daily_minutes"] = m
    if "bedtime" in v:
        b = str(v["bedtime"] or "").strip()
        if b and not TIME.match(b):
            raise ValueError("睡觉时间要是 HH:MM，比如 20:30；不限就留空")
        out["bedtime"] = b
    if "voice" in v:
        if v["voice"] not in ("child", "adult"):
            raise ValueError("声音只能是童声或大人的声音")
        out["voice"] = v["voice"]
    return out


def device_kids(device: str) -> dict:
    """这台设备的儿童模式（设备取）：每台的开关和几岁 + 全家一份的限时、睡觉时间、声音；updated 取两者新的那个。"""
    from core.utils import device_prefs

    d = load()
    mine = device_prefs.get_kids(device) or {}
    rules = d["rules"]
    return {"enabled": bool(mine.get("enabled")), "age": int(mine.get("age") or 3), "voice": rules["voice"],
            "daily_minutes": rules["daily_minutes"], "bedtime": rules["bedtime"],
            "updated": max(float(mine.get("updated") or 0), float(rules.get("updated") or 0))}


def from_device(device: str, kids: dict) -> dict:
    """设备设置页上改了（/kiosk/prefs）：开关和几岁记在这台；限时、睡觉时间、声音写成全家最新的。"""
    from core.utils import device_prefs

    device_prefs.put_kids(device, kids)
    d = load()
    new = normalize_rules({k: kids[k] for k in ("daily_minutes", "bedtime", "voice") if k in kids}, d["rules"])
    if {k: new[k] for k in ("daily_minutes", "bedtime", "voice")} != {k: d["rules"][k] for k in ("daily_minutes", "bedtime", "voice")}:
        new["updated"] = time.time()
        d["rules"] = new
        save(d)
    return device_kids(device)


# ---------------------------------------------------------------- 学一学

def learn_text(kind: str, age: int = 6) -> str:
    """设备点「学一学」时写进提示词的那一段（认字卡按顺序出并往后挪）。不认识的 kind 返回空。"""
    with _lock:
        d = load()
        L = d["learn"]
        age = int(age or 6)
        if kind == "chars":
            chars = [c for c in re.sub(r"[\s,，、]", "", L["chars"]) if c.strip()]
            if not chars:
                return ""
            pos = int(L.get("char_pos") or 0) % len(chars)
            batch = [chars[(pos + i) % len(chars)] for i in range(10)]
            L["char_pos"] = (pos + 10) % len(chars)
            _write(_path(), d)
            return (f"和{age}岁的孩子玩认字卡：按顺序从这些字里出，一次只出一个：{'、'.join(batch)}。每个字调用工具 self_screen_show_card "
                    "在屏幕上显示这个大字（标题就是这个字，正文写拼音和一个组词），问孩子认不认识、怎么读；孩子说完告诉他对不对，再接着出下一个字。")
        if kind == "poem":
            poems = [p for p in L["poems"] if str(p).strip()]
            if not poems:
                return ""
            left = [p for p in poems if p not in L["poem_done"]] or poems
            pick = random.choice(left)
            done = [p for p in L["poem_done"] if p in poems] + [pick]
            L["poem_done"] = done if len(set(done)) < len(poems) else []        # 一轮背完了重新来
            _write(_path(), d)
            return (f"带{age}岁的孩子背古诗《{pick}》：先说诗名和作者，再一句一句念，每念一句让孩子跟着念一遍，等他念完再念下一句；"
                    "背完夸一句，问他要不要再来一首。")
        if kind == "math":
            return (f"和{age}岁的孩子玩口算，范围是{MATH.get(L['math'], MATH['10'])}：一次只出一道，等他回答；"
                    "答对了夸一句再出下一道，答错了用数手指或画小圆点的办法讲一下，再出一道差不多的。")
        if kind == "why":
            topics = [t for t in L["why"] if str(t).strip()]
            topic = random.choice(topics) if topics else "一个有意思的科学小知识"
            return (f"给{age}岁的孩子讲「{topic}」：三四句讲完，用孩子听得懂的话，最后问孩子一个简单的问题，等他回答。")
    return ""


def put_learn(v: dict) -> dict:
    d = load()
    L = d["learn"]
    if "chars" in v:
        chars = re.sub(r"[\s,，、]", "", str(v["chars"] or ""))[:2000]
        if chars != L["chars"]:
            L["chars"], L["char_pos"] = chars, 0
    if "poems" in v:
        L["poems"] = [str(p).strip()[:30] for p in (v["poems"] or []) if str(p).strip()][:200]
    if "why" in v:
        L["why"] = [str(p).strip()[:40] for p in (v["why"] or []) if str(p).strip()][:200]
    if "math" in v:
        if v["math"] not in MATH:
            raise ValueError("口算范围只有 10 以内、20 以内、100 以内、乘法口诀")
        L["math"] = v["math"]
    if "char_pos" in v:
        L["char_pos"] = max(0, int(v["char_pos"]))
    save(d)
    return L


# ---------------------------------------------------------------- 后台的修改

def apply(op: dict) -> dict:
    kind = op.get("op")
    d = load()
    if kind == "rules":
        new = normalize_rules(op, d["rules"])
        new["updated"] = time.time()
        d["rules"] = new
    elif kind == "child":
        name = str(op.get("name") or "").strip()[:12]
        if not name:
            raise ValueError("缺孩子的名字")
        c = d["children"].setdefault(name, {})
        if "call" in op:
            c["call"] = str(op.get("call") or "").strip()[:12]
        if "age" in op:
            age = int(op.get("age") or 0)
            if age and not 1 <= age <= 14:
                raise ValueError("年龄要在 1 到 14 岁之间")
            c["age"] = age or None
        if "sections" in op:
            secs = op.get("sections")
            c["sections"] = None if secs is None else [re.sub(r"[^0-9a-zA-Z_]", "", str(s))[:24] for s in secs][:20]
    elif kind == "add_child":
        name = str(op.get("name") or "").strip()[:12]
        if not name:
            raise ValueError("填上孩子的名字")
        if child(name, d):
            raise ValueError(f"已经有「{name}」了")
        d["children"][name] = {"manual": True, "age": int(op.get("age") or 0) or None, "call": ""}
    elif kind == "remove_child":
        name = str(op.get("name") or "")
        c = d["children"].get(name)
        if not c or not c.get("manual"):
            raise ValueError("家人声音里的孩子要在设备「设置 → 家人声音」里删")
        d["children"].pop(name)
    elif kind == "learn":
        save(d)
        put_learn(op)
        return load()
    else:
        raise ValueError("不认识的操作")
    save(d)
    return d


# ---------------------------------------------------------------- 记录和周报

def record(device: str, kind: str, value: dict, today: str = None):
    """设备报的一条：talk（今天聊了多少秒，覆盖）、learn（学一学点了哪个：value["learn"]）、study（学习陪伴今天的摘要，覆盖）、
    played（放了什么）。"""
    if not device or kind not in ("talk", "learn", "study", "played"):
        raise ValueError("记录不对")
    today = today or datetime.date.today().isoformat()
    with _lock:
        r = _read(_records_path())
        days = r.setdefault("days", {})
        day = days.setdefault(today, {}).setdefault(device, {})
        if kind == "talk":
            day["talk"] = max(float(day.get("talk") or 0), float(value.get("seconds") or 0))
        elif kind == "learn":
            k = str(value.get("learn") or "")
            if k in LEARN_KINDS:
                day.setdefault("learn", {})[k] = day.get("learn", {}).get(k, 0) + 1
        elif kind == "study":
            day["study"] = {"minutes": int(value.get("minutes") or 0), "present": int(value.get("present_minutes") or 0),
                            "focus": int(value.get("focus_pct") or 0), "reminders": int(value.get("reminders") or 0)}
        else:
            played = day.setdefault("played", [])
            played.append({"t": int(time.time()), "title": str(value.get("title") or "")[:60],
                           "section": str(value.get("section") or "")[:20], "who": str(value.get("who") or "")[:12]})
            del played[:-200]
        for k in sorted(days)[:-KEEP_DAYS]:
            days.pop(k, None)
        _write(_records_path(), r)


def week(today: str = None, devices: dict = None) -> dict:
    """最近 7 天（到 today）：每天聊天分钟、学习陪伴分钟，加一段话。devices：设备 id → 名字。"""
    today = datetime.date.fromisoformat(today) if today else datetime.date.today()
    days_rec = _read(_records_path()).get("days") or {}
    out, talk, learn, study, focus, reminders, played = [], 0, {}, 0, [], 0, {}
    for i in range(6, -1, -1):
        day = (today - datetime.timedelta(days=i)).isoformat()
        per = days_rec.get(day) or {}
        t = sum(float(v.get("talk") or 0) for v in per.values()) / 60
        s = sum(int((v.get("study") or {}).get("minutes") or 0) for v in per.values())
        out.append({"date": day, "talk": round(t), "study": s,
                    "devices": {(devices or {}).get(k, k): round(float(v.get("talk") or 0) / 60) for k, v in per.items()}})
        talk += t
        study += s
        for v in per.values():
            for k, n in (v.get("learn") or {}).items():
                learn[k] = learn.get(k, 0) + n
            st = v.get("study") or {}
            if st.get("present"):
                focus.append((st["focus"], st["present"]))
            reminders += int(st.get("reminders") or 0)
            for p in v.get("played") or []:
                played[p["title"]] = played.get(p["title"], 0) + 1
    parts = []
    if talk >= 1:
        best = max(out, key=lambda x: x["talk"])
        parts.append(f"聊了 {_dur(talk)}（每天平均 {round(talk / 7)} 分，{_weekday(best['date'])}最多 {best['talk']} 分）")
    if learn:
        parts.append(f"学一学 {sum(learn.values())} 次（" + "、".join(f"{LEARN_KINDS[k]} {n}" for k, n in
                                                        sorted(learn.items(), key=lambda x: -x[1])) + "）")
    if study:
        pct = round(sum(f * p for f, p in focus) / max(1, sum(p for _, p in focus))) if focus else None
        parts.append(f"学习陪伴 {_dur(study)}" + (f"，专心 {pct}%" if pct is not None else "") + (f"，提醒 {reminders} 次" if reminders else ""))
    if played:
        top = max(played.items(), key=lambda x: x[1])
        parts.append(f"看了听了 {sum(played.values())} 个，最常放《{top[0]}》")
    return {"from": out[0]["date"], "to": out[-1]["date"], "days": out,
            "summary": "；".join(parts) + "。" if parts else "这周还没有记录：设备升级到 0.1.688 以后开始记。",
            "talk": round(talk), "study": study, "learn": learn}


def _dur(minutes: float) -> str:
    m = round(minutes)
    return f"{m // 60} 小时 {m % 60} 分" if m >= 60 else f"{m} 分钟"


def _weekday(day: str) -> str:
    return "周" + "一二三四五六日"[datetime.date.fromisoformat(day).weekday()]


def today_talk(device: str) -> int:
    day = (_read(_records_path()).get("days") or {}).get(datetime.date.today().isoformat()) or {}
    return round(float((day.get(device) or {}).get("talk") or 0) / 60)
