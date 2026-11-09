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


PNG = b"\x89PNG\r\n\x1a\n" + b"cutout" * 40


def test_original_style_keeps_up_to_four_poses(tmp_path, monkeypatch):
    monkeypatch.setattr(photo_ask, "_photos_dir", lambda device="": str(tmp_path))
    s = photo_ask.start("dev-3", "", purpose="avatar")
    for i in range(photo_ask.AVATAR_POSES):
        photo_ask.receive(s["token"], PNG + bytes([i]))           # 同一个码接着传 4 张
    try:
        photo_ask.receive(s["token"], PNG)
        assert False, "第 5 张应该被拒"
    except LookupError:
        pass
    assert photo_ask.view(s["token"])["poses"] == 4
    photo_ask.begin_avatar(s["token"], photo_ask.ORIGINAL_STYLE)
    photo_ask.generate_avatar(s["token"], photo_ask.ORIGINAL_STYLE, {})
    paths = [photo_ask.avatar_path(s["token"], i) for i in range(4)]
    assert all(p and p.endswith(".png") for p in paths) and photo_ask.avatar_path(s["token"], 4) is None
    assert [open(p, "rb").read()[-1] for p in paths] == [0, 1, 2, 3]
