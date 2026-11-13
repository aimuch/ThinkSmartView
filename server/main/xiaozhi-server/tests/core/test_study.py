"""学习陪伴（设计画布第 48 块）：网关只负责把连拍交给看图模型、把回答规整成固定的几个字段。"""
import pytest

from core.agent import photo_ask, study


def test_parse_result_keeps_known_values():
    r = study.parse_result('好的：{"present": true, "posture": "slouch", "too_close": true, "activity": "studying", '
                           '"focused": true, "note": "低头写字，背有点弯"} 以上')
    assert r == {"present": True, "posture": "slouch", "too_close": True, "activity": "studying",
                 "focused": True, "note": "低头写字，背有点弯"}


def test_parse_result_absent_and_unknown_values():
    r = study.parse_result('{"present": false, "posture": "good", "too_close": true, "activity": "phone", "focused": true}')
    assert r["activity"] == "absent" and r["posture"] == "unknown"
    assert r["too_close"] is False and r["focused"] is False
    r = study.parse_result('{"present": true, "posture": "weird", "activity": "dancing", "focused": true}')
    assert r["posture"] == "unknown" and r["activity"] == "other"
    assert r["focused"] is False            # 不是写作业、看书，就不算专心


def test_parse_result_rejects_non_json():
    with pytest.raises(ValueError):
        study.parse_result("孩子在写作业")
    with pytest.raises(ValueError):
        study.parse_result("{不是 json}")


def test_check_sends_frames_in_order(monkeypatch):
    seen = {}

    def fake(merged, prompt, images, device=None, purpose="vision"):
        seen.update(prompt=prompt, images=images, device=device, purpose=purpose)
        return '{"present": true, "posture": "good", "activity": "reading", "focused": true, "note": "在看书"}'
    monkeypatch.setattr(photo_ask, "ask_vision_images", fake)
    r = study.check({}, [b"a", b"", b"b", b"c", b"d", b"e"], 4, "dev1")
    assert r["activity"] == "reading" and r["focused"] is True
    assert seen["images"] == [b"a", b"b", b"c", b"d"]          # 空的去掉，最多 4 张
    assert seen["purpose"] == "study" and seen["device"] == "dev1"
    assert "每隔约 4 秒连拍的 4 张" in seen["prompt"]


def test_check_needs_a_photo():
    with pytest.raises(ValueError):
        study.check({}, [b""])
    assert "只根据这张图片" in study.build_prompt(1, 4)
