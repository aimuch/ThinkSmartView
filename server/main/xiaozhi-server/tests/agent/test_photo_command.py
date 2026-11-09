"""[xiaozhi-hub] 拍照问的快捷通道：哪些话直接让设备拍照，哪些照常交给模型。"""

import pytest

from core.agent import photo_command as pc


@pytest.mark.parametrize("text", [
    "帮我看下这是什么", "看看这是什么药", "这是什么东西？", "帮我看一下这个", "这个字怎么读", "这能吃吗",
    "灯已经开了，再拍一张看看这是什么", "拍张照看看", "你认一下这是什么花", "这是啥",
])
def test_photo_asks(text):
    assert pc.is_photo_ask(text)


@pytest.mark.parametrize("text", [
    "看看今天天气", "帮我看下新闻", "现在几点", "你是什么", "什么是光合作用", "讲个故事",
    "帮我看下明天的闹钟", "这首歌叫什么", "今天吃什么好呢我也不知道你帮我想想办法吧好不好嘛拜托啦",
])
def test_not_photo_asks(text):
    assert not pc.is_photo_ask(text)


def test_question_of():
    assert pc.question_of("帮我看下这是什么药") == "这是什么药"
    assert pc.question_of("拍张照看看") == "这是什么"
    assert pc.question_of("这个字怎么读") == "这个字怎么读"
