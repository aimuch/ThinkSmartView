"""[xiaozhi-hub] 常用口令（设计画布第 53 块）：哪些句子认准了直接办，哪些交给大模型。"""
import asyncio
import datetime
import json
from types import SimpleNamespace

import pytest

from core.agent import command_exec, commands as cm

NOW = datetime.datetime(2026, 11, 14, 20, 30)     # 星期六晚上


@pytest.fixture(autouse=True)
def tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(cm, "_path", lambda: str(tmp_path / "commands.json"))
    monkeypatch.setattr(cm, "_stats_path", lambda: str(tmp_path / "commands_stats.json"))


def plan(text, cfg=None):
    p, _ = cm.match(text, cfg or cm.defaults(), NOW)
    return p


def test_numbers_and_cleaning():
    assert cm.cn_number("二十五") == 25 and cm.cn_number("十") == 10 and cm.cn_number("一百") == 100 and cm.cn_number("两") == 2
    assert cm.clean("小麦小麦，帮我定个七点半的闹钟吧！") == "定个7点半的闹钟"
    assert cm.parse_duration("1个半小时") == 5400 and cm.parse_duration("3分半") == 210 and cm.parse_duration("半个小时") == 1800


def test_date_time_answers():
    assert plan("现在几点了").reply == "现在是 20:30"
    assert plan("今天几号").reply == "今天是 11 月 14 日，星期六"
    assert plan("明天星期几").reply == "明天是 11 月 15 日，星期日"
    assert plan("今天农历几号").note == "lunar"


def test_timers():
    p = plan("倒计时五分钟")
    assert (p.tool, p.args, p.reply) == ("self.alarm.create", {"kind": "timer", "minutes": 5}, "好的，5 分钟后提醒你")
    assert plan("十分钟后叫我").args == {"kind": "timer", "minutes": 10}
    assert plan("一个半小时的计时").args == {"kind": "timer", "hours": 1, "minutes": 30}
    p = plan("二十分钟后提醒我关火")
    assert p.args == {"kind": "reminder", "minutes": 20, "say": "关火", "label": "关火"} and p.reply == "好的，20 分钟后提醒你关火"
    assert plan("十分钟后提醒我每天喝水") is None                 # 带重复：交给大模型


def test_alarms():
    p = plan("明天早上七点半叫我")
    assert p.args == {"kind": "alarm", "time": "07:30", "days_from_today": 1} and p.reply == "好的，明天早上 7:30 叫你"
    assert plan("明天七点叫我").args["time"] == "07:00"            # 明天 + 5-11 点：早上
    assert plan("晚上九点提醒我吃药").args == {"kind": "reminder", "time": "21:00", "say": "吃药", "label": "吃药"}
    assert plan("下午三点提醒我开会").args["days_from_today"] == 1   # 今天下午三点已经过了：明天
    assert plan("七点叫我") is None                                 # 今晚还是明早：交给大模型问
    assert plan("定个六点半的闹钟") is None
    p = plan("下周一早上八点叫我")
    assert p.args["days_from_today"] == 2 and p.args["time"] == "08:00"
    assert plan("工作日每天七点叫我") is None


def test_cancel_query_volume_screen_media():
    assert plan("取消所有闹钟").args == {"all": True, "kind": "alarm"}
    p = plan("取消倒计时")
    assert p.kind == "steps" and p.steps == "cancel_one" and p.args == {"kind": "timer"}
    assert plan("我有哪些闹钟").steps == "list" and plan("还剩多久").args == {"kind": "timer"}
    assert plan("大点声").args == {"change": 15} and plan("声音小一点").args == {"change": -15}
    assert plan("音量调到五十").args == {"volume": 50} and plan("最大声").args == {"volume": 100}
    assert plan("静音").steps == "mute" and plan("取消静音").steps == "unmute"
    assert plan("屏幕亮一点").args == {"change": 20} and plan("亮度调到30").args == {"brightness": 30}
    assert plan("开启睡眠模式").args == {"enabled": True}
    assert plan("别放了").args == {"action": "stop"} and plan("打开学习陪伴").args == {"action": "on"}
    for t in ("今天天气怎么样", "给我讲个笑话", "把声音调大一点然后放首歌", "大一点的字"):
        assert plan(t) is None, t


def test_custom_aliases_switches():
    cfg = cm.defaults()
    p = plan("打开机柜", cfg)                                        # 内置示例
    assert (p.rule, p.tool, p.args, p.reply) == ("custom:rack", "self.screen.open_page", {"page": "work.rack"}, "好的")
    cfg["aliases"] = [{"say": "安静会儿", "means": "静音"}]
    assert plan("安静会儿", cfg).steps == "mute"
    cfg["custom"].append(cm.validate_rule({"id": "light", "phrases": ["{时长}后关灯"], "action": {"type": "say", "text": "好，{时长}秒后关"},
                                           "reply": ""}))
    assert plan("十分钟后关灯", cfg).reply == "好，600秒后关"
    cfg["custom"].append(cm.validate_rule({"id": "vol", "phrases": ["音量{数字}档"], "action": {"type": "tool", "tool": "self.audio_speaker.set_volume",
                                           "args": {"volume": "{数字}"}}, "reply": "音量 {数字}"}))
    p = plan("音量三十档", cfg)
    assert p.args == {"volume": 30} and p.reply == "音量 30"
    cfg["builtin"]["volume"]["enabled"] = False
    assert plan("大点声", cfg) is None                               # 这一类关了：交给大模型
    cfg["builtin"]["timer"]["reply"] = "收到，{时长}后叫你"
    assert plan("倒计时三分钟", cfg).reply == "收到，3 分钟后叫你"
    assert cm.active({"enabled": True, "devices": {"d2": False}}, "d2") is False
    assert cm.active({"enabled": False, "devices": {"d3": True}}, "d3") is True
    assert cm.validate_rule({"phrases": ["x"], "action": {"type": "page", "page": "nowhere"}}) is None


def test_save_load_and_stats():
    cfg = cm.defaults()
    cfg["enabled"] = False
    cfg["custom"].append({"phrases": ["回首页"], "action": {"type": "page", "page": "home"}})
    saved = cm.save(cfg)
    assert cm.load() == saved and saved["enabled"] is False and len(saved["custom"]) == 2
    cm.record("d1", "倒计时五分钟", "fast", "timer")
    cm.record("d1", "十分钟后关灯", "llm")
    cm.record("d1", "十分钟后关灯", "llm")
    s = cm.stats()
    assert s["fast"] == 1 and s["llm"] == 2 and s["misses"][0] == {"text": "10分钟后关灯", "count": 2}
    assert cm.explain("七点叫我")["matched"] is False


def test_exec_hands_on_or_passes_through(monkeypatch):
    ran = []

    async def fake_run(conn, heard, plan, device):
        ran.append((heard, plan.rule))
    monkeypatch.setattr(command_exec, "_run", fake_run)
    conn = SimpleNamespace(device_id="d1", headers={}, logger=SimpleNamespace(bind=lambda **k: SimpleNamespace(info=lambda *a: None)))

    async def go():
        assert await command_exec.handle(conn, "倒计时五分钟") is None
        assert await command_exec.handle(conn, "今天天气怎么样") == "今天天气怎么样"
        wrapped = json.dumps({"speaker": "妈妈", "content": "大点声"}, ensure_ascii=False)
        assert await command_exec.handle(conn, wrapped) is None
        await asyncio.sleep(0)
    asyncio.run(go())
    assert ran == [("倒计时五分钟", "timer"), ("大点声", "volume")]
    cfg = cm.defaults()
    cfg["devices"] = {"d1": False}
    cm.save(cfg)
    assert asyncio.run(command_exec.handle(conn, "倒计时五分钟")) == "倒计时五分钟"   # 这台设备关了：全走大模型
