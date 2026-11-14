"""[xiaozhi-hub] 哪些句子不交给大模型（设计画布第 52 块）。"""
import asyncio
import json
from types import SimpleNamespace

from core.providers.asr import base
from core.utils import turn_filter as tf

WAKE = ["小麦小麦", "哈喽小麦"]


def test_only_wake_words():
    for t in ("小麦小麦", "小麦小麦。", "小麦小麦，小麦小麦", "小麦", "哈喽小麦！", "小麦 小麦"):
        assert tf.judge(t, WAKE) == ("wake", ""), t


def test_leading_wake_is_stripped():
    assert tf.judge("小麦小麦，今天天气怎么样？", WAKE) == (None, "今天天气怎么样？")
    assert tf.judge("小麦小麦小麦小麦今天下雨吗", WAKE) == (None, "今天下雨吗")
    assert tf.judge("今天小麦价格", WAKE) == (None, "今天小麦价格")          # 不在开头：照常
    assert tf.judge("小麦粉怎么做面包", WAKE) == (None, "小麦粉怎么做面包")   # 单个「小麦」开头不是在叫它
    assert tf.judge("小麦小麦小麦", WAKE) == ("wake", "")


def test_fillers_and_closers():
    for t in ("嗯", "嗯嗯。", "好的", "哦", "OK", "哈哈"):
        assert tf.judge(t, WAKE)[0] == "filler", t
    for t in ("谢谢", "谢谢你！", "好了", "没事了", "不用了", "小麦小麦，谢谢"):
        assert tf.judge(t, WAKE)[0] == "close", t
    assert tf.judge("好的，明天七点叫我", WAKE) == (None, "好的，明天七点叫我")
    assert tf.judge("谢谢你帮我订闹钟，再帮我查下天气", WAKE)[0] is None


def test_other_speaker():
    assert tf.other_speaker(True, True, "妈妈", "爸爸")
    assert tf.other_speaker(True, True, "妈妈", "")                 # 认不出是谁：也不算
    assert not tf.other_speaker(True, True, "妈妈", "妈妈")
    assert not tf.other_speaker(True, True, None, "爸爸")            # 刚才那个人认不出：不锁
    assert not tf.other_speaker(True, False, "妈妈", "爸爸")         # 没开
    assert not tf.other_speaker(False, True, "妈妈", "爸爸")         # 不是接着听


def test_vad_event_carries_the_reason():
    sent = []

    async def send(m):
        sent.append(json.loads(m))
    conn = SimpleNamespace(features={"vad_end": True}, session_id="s", websocket=SimpleNamespace(send=send))
    asyncio.run(base.send_vad_event(conn, "empty", "close"))
    asyncio.run(base.send_vad_event(conn, "empty"))
    assert sent == [{"type": "vad", "state": "empty", "session_id": "s", "reason": "close"},
                    {"type": "vad", "state": "empty", "session_id": "s"}]
