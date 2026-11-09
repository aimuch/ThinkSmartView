"""[xiaozhi-hub] 「原图」画风：照片原样当形象，不用 Key（设计画布第 26 块）。"""

from core.agent import photo_ask

JPEG = b"\xff\xd8\xff\xe0" + b"toy-photo" * 50 + b"\xff\xd9"


def test_original_style_needs_no_key(tmp_path, monkeypatch):
    monkeypatch.setattr(photo_ask, "_photos_dir", lambda device="": str(tmp_path))
    s = photo_ask.start("dev-1", "", purpose="avatar")
    photo_ask.receive(s["token"], JPEG)
    assert list(photo_ask.AVATAR_STYLES)[0] == photo_ask.ORIGINAL_STYLE and photo_ask.AVATAR_STYLES[0] == "原图"
    photo_ask.begin_avatar(s["token"], photo_ask.ORIGINAL_STYLE)
    photo_ask.generate_avatar(s["token"], photo_ask.ORIGINAL_STYLE, {})   # 没有任何 Key
    cur = photo_ask.get(s["token"])
    assert cur["state"] == "generated" and cur["error"] is None
    assert open(photo_ask.avatar_path(s["token"]), "rb").read() == JPEG


def test_ai_style_without_key_points_to_original(tmp_path, monkeypatch):
    monkeypatch.setattr(photo_ask, "_photos_dir", lambda device="": str(tmp_path))
    s = photo_ask.start("dev-2", "", purpose="avatar")
    photo_ask.receive(s["token"], JPEG)
    photo_ask.begin_avatar(s["token"], 1)
    photo_ask.generate_avatar(s["token"], 1, {})
    cur = photo_ask.get(s["token"])
    assert cur["state"] == "received" and "原图" in cur["error"]
