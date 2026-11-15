"""智能中枢的卡片（设计画布第 72 块）：英文报错翻成中文、列出各节点为什么连不上；红外设备带按键。"""
from core.integrations import smarthub

REAL = ("power supply 9f:89:c2:df:6e:54 unreachable (node:atoms3u: unavailable; node:lckfb: unavailable; "
        "node:sticks3: node sticks3: connect failed: connect failed; not advertising (off, out of range, or held by another app?); "
        "node:amoled216: unavailable)")


def test_reasons_in_chinese_without_mac():
    text, tried = smarthub.reason(REAL)
    assert text == "没开机，或者离节点太远" and "9f:89" not in str(tried)
    assert [t["node"] for t in tried] == ["atoms3u", "lckfb", "sticks3", "amoled216"]
    assert tried[2]["why"] == "没听到它（没开机或太远）" and tried[0]["why"] == "节点不在线"
    assert smarthub.reason("waiting for the supply to dial in over Wi-Fi") == ("等它自己连上 Wi‑Fi", [])
    assert smarthub.reason("x unreachable (node:a: unavailable; node:b: unavailable)")[0] == "能连它的节点都不在线"
    assert smarthub.reason("") == ("", [])


def test_psu_and_ir_tidy():
    psu = smarthub.tidy({"id": "rk", "name": "RK6006H", "meta": {"kind": "psu"}, "actions": {"on": "", "off": ""},
                         "state": {"online": False, "error": REAL}})
    assert psu["summary"] == "没开机，或者离节点太远" and len(psu["tried"]) == 4
    fan = smarthub.tidy({"id": "fan", "name": "空气循环扇", "meta": {"kind": "ir"}, "actions": {"press": "", "toggle": ""},
                         "link": {"route": {"radio": "node:sticks3"}},
                         "state": {"online": True, "buttons": [{"id": "power", "name": "电源"}, {"id": "speed_up", "name": "风量+"}]}})
    assert fan["buttons"] == [{"id": "power", "name": "电源"}, {"id": "speed_up", "name": "风量+"}]
    assert "press" in fan["actions"] and fan["via"] == "sticks3" and fan["summary"] == "红外 · 电源、风量+"
    assert smarthub._params(fan, "press", {"button": "speed_up"}) == {"button": "speed_up"}
