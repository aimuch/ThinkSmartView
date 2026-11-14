"""认人打招呼（设计画布第 57 块）：够像又分得清才叫名字；说法、称呼、关掉；念没念记下来。"""
import numpy as np
import pytest

from core.agent import greetings


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(greetings, "_path", lambda: str(tmp_path / "greetings.json"))
    greetings.history.clear()
    yield


def test_decide_needs_a_clear_winner():
    cfg = greetings.load()
    g = greetings.decide("dev-1", [("宝宝", 0.71, True), ("妈妈", 0.40, False)], cfg)
    assert g["speaker"] == "宝宝" and g["child"] and "宝宝" in g["text"] and g["text"] in greetings.lines_for("宝宝", True, cfg)
    assert greetings.decide("dev-1", [("妈妈", 0.50, False)], cfg)["text"] == ""                      # 不够像
    g = greetings.decide("dev-1", [("妈妈", 0.70, False), ("奶奶", 0.67, False)], cfg)              # 分不清
    assert g["text"] == "" and g["speaker"] is None and "奶奶" in g["why"]
    assert greetings.decide("dev-1", [], cfg)["text"] == ""
    assert len(greetings.history) == 4
    # 严格程度
    other_kid = [("宝宝", 0.68, True), ("妈妈", 0.49, False)]
    assert greetings.decide("dev-1", other_kid, cfg)["text"]                              # 适中：0.68 算认出
    greetings.save({"level": "strict"})
    assert greetings.decide("dev-1", other_kid)["text"] == ""                             # 严格：要 0.72
    greetings.save({"level": "bogus"})
    assert greetings.load()["level"] == "strict"


def test_call_names_own_lines_and_off():
    greetings.save({"child": "你好呀，{称呼}\n\n你好呀，{称呼}", "people": {
        "宝宝": {"call": "小宝"}, "妈妈": {"lines": ["我在呢，亲爱的"]}, "爸爸": {"off": True}}})
    cfg = greetings.load()
    assert cfg["child"] == ["你好呀，{称呼}"]                                  # 空行、重复的去掉
    assert greetings.lines_for("宝宝", True, cfg) == ["你好呀，小宝"]
    assert greetings.lines_for("妈妈", False, cfg) == ["我在呢，亲爱的"]
    assert greetings.lines_for("爸爸", False, cfg) == []
    assert greetings.lines_for("奶奶", False, cfg) == ["我在，奶奶", "奶奶，你说"]   # 没设过：用名字、大人的说法
    g = greetings.decide("dev-1", [("爸爸", 0.8, False)], cfg)
    assert g["speaker"] == "爸爸" and g["text"] == "" and "只「叮」" in g["why"]
    greetings.save({"enabled": False})
    g = greetings.decide("dev-1", [("宝宝", 0.8, True)])
    assert g["text"] == "" and g["why"] == "打招呼关着"
    people = [{"name": "宝宝", "child": True}, {"name": "妈妈", "child": False}]
    assert greetings.all_lines(greetings.load(), people) == ["你好呀，小宝", "我在呢，亲爱的"]


def test_mark_said():
    g = greetings.decide("dev-1", [("宝宝", 0.8, True)])
    assert greetings.mark(g["id"], False)["said"] is False and greetings.mark("nope", True) is None


def test_speech_part_drops_the_quiet_around_the_word():
    x = np.concatenate([np.zeros(16000), np.ones(8000) * 0.3, np.zeros(16000)]).astype(np.float32)
    y = greetings.speech_part(x)
    assert 8000 <= len(y) <= 8000 + 2 * 9 * 320 and float(np.max(np.abs(y))) > 0.2
