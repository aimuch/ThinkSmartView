"""[xiaozhi-hub] 设备健康上报、后台看屏幕（设计画布第 83 块）：只留最新一份、截图请求设备拿一次就清掉。"""
from core.agent import device_health as dh


def test_reports_and_screenshots(tmp_path, monkeypatch):
    monkeypatch.setattr(dh, "_dir", lambda: str(tmp_path))
    monkeypatch.setattr(dh, "_reports", None)
    monkeypatch.setattr(dh, "_pending", {})
    monkeypatch.setattr(dh, "_shot_errors", {})
    dh.put("a:b", {"cpu": 12})
    dh.put("a:b", {"cpu": 30})
    v = dh.view()["a:b"]
    assert v["cpu"] == 30 and v["at"] and v["screenshot_at"] is None and not v["pending"]
    monkeypatch.setattr(dh, "_reports", None)                 # 网关重启：从文件读回来
    assert dh.view()["a:b"]["cpu"] == 30
    assert not dh.take_pending("a:b")
    dh.request_screenshot("a:b")
    assert dh.view()["a:b"]["pending"]
    assert dh.take_pending("a:b") and not dh.take_pending("a:b")   # 拿一次就清掉
    dh.save_screenshot("a:b", b"\x89PNG\r\n\x1a\nxx")
    assert dh.view()["a:b"]["screenshot_at"] and dh.shot_path("a:b").endswith("a_b.png")
    dh.shot_failed("c", "没有屏幕程序")
    assert dh.view()["c"]["screenshot_error"]["text"] == "没有屏幕程序"
