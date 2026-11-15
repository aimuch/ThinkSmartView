"""家庭相册（设计画布第 66 块）：EXIF 拍摄时间、上传、文件夹、设备的设置和挑照片。"""
import os
import shutil
import struct
import subprocess
import time
from datetime import date, datetime

import pytest

from core.agent import album

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="没有 ffmpeg")


@pytest.fixture(autouse=True)
def tmp_album(tmp_path, monkeypatch):
    monkeypatch.setattr(album, "_root", lambda: str(tmp_path / "album"))
    monkeypatch.setattr(album, "_path", lambda: str(tmp_path / "album.json"))
    album._scan.update(running=False, done=0, total=0)
    return tmp_path


def _exif(taken: str) -> bytes:
    """一个小端的 EXIF APP1 段：IFD0 只有 ExifIFD 指针，子 IFD 只有 DateTimeOriginal。"""
    s = taken.encode() + b"\0"                      # 20 字节
    tiff = b"II*\0" + struct.pack("<I", 8)
    ifd0 = struct.pack("<H", 1) + struct.pack("<HHII", 0x8769, 4, 1, 26) + struct.pack("<I", 0)   # 8 + 18 = 26
    sub = struct.pack("<H", 1) + struct.pack("<HHII", 0x9003, 2, len(s), 44) + struct.pack("<I", 0)  # 26 + 18 = 44
    body = b"Exif\0\0" + tiff + ifd0 + sub + s
    return b"\xff\xe1" + struct.pack(">H", len(body) + 2) + body


def _jpeg(path, w=64, h=48, taken=None):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=s={w}x{h}", "-frames:v", "1", str(path)], check=True)
    if taken:
        b = open(path, "rb").read()
        open(path, "wb").write(b[:2] + _exif(taken) + b[2:])
    return str(path)


def test_exif_taken_reads_datetime_original(tmp_path):
    p = tmp_path / "x.jpg"
    p.write_bytes(b"\xff\xd8" + _exif("2023:11:15 10:20:30") + b"\xff\xd9")
    assert album.exif_taken(str(p)) == int(datetime(2023, 11, 15, 10, 20, 30).timestamp())
    p.write_bytes(b"\xff\xd8\xff\xd9")
    assert album.exif_taken(str(p)) is None
    p.write_bytes(b"not a jpeg")
    assert album.exif_taken(str(p)) is None


def test_on_this_day():
    today = date(2026, 11, 15)
    ts = lambda y, m, d: int(datetime(y, m, d, 12).timestamp())
    assert album._otd(ts(2023, 11, 13), today)
    assert not album._otd(ts(2023, 11, 25), today)
    assert not album._otd(ts(2026, 11, 15), today)       # 今年的不算
    assert not album._otd(None, today)
    assert album._otd(ts(2024, 2, 29), date(2026, 3, 1))  # 闰日按 3 月 1 日


@needs_ffmpeg
def test_upload_makes_sizes_and_reads_date(tmp_path):
    src = _jpeg(tmp_path / "a.jpg", 3000, 2000, "2020:11:14 09:00:00")
    rec = album.upload(src, "a.jpg")
    assert not os.path.exists(src)                       # 临时文件用完删掉
    assert rec["taken"] == int(datetime(2020, 11, 14, 9).timestamp())
    assert rec["w"] == 2560 and rec["h"] in (1706, 1707)
    assert os.path.isfile(album.image(rec["id"], "t"))
    d = album.image(rec["id"], "d")
    assert d.endswith(os.path.join("d", rec["id"] + ".jpg")) and album._size(d)[0] == 1280
    # 同一张再传一次不重复
    album.upload(_jpeg(tmp_path / "b.jpg", 3000, 2000, "2020:11:14 09:00:00"), "b.jpg")
    assert len(album.load()["photos"]) == 1
    assert album.image("../../etc", "d") == "" and album.image(rec["id"], "x") == ""


@needs_ffmpeg
def test_device_gets_its_albums_and_skips_hidden(tmp_path):
    a = album.upload(_jpeg(tmp_path / "1.jpg", 80, 60), "1.jpg")
    b = album.upload(_jpeg(tmp_path / "2.jpg", 60, 80), "2.jpg")
    album.apply({"op": "add_album", "name": "孩子"})
    kid = album.load()["albums"][0]["id"]
    album.apply({"op": "tag", "album": kid, "ids": [b["id"]]})
    assert {p["id"] for p in album.for_device("dev-1")["photos"]} == {a["id"], b["id"]}
    album.apply({"op": "device", "device": "dev-1", "albums": [kid], "style": "photo", "interval": 30})
    got = album.for_device("dev-1", local=5)
    assert [p["id"] for p in got["photos"]] == [b["id"]] and got["photos"][0]["albums"] == ["孩子"]
    assert got["settings"]["style"] == "photo" and got["settings"]["interval"] == 30
    assert album.load()["local"]["dev-1"]["count"] == 5
    album.hide("dev-1", b["id"])
    assert album.for_device("dev-1")["photos"] == []
    album.apply({"op": "delete_album", "id": kid})
    assert album.device_settings("dev-1")["albums"] is None


def test_device_settings_newer_wins():
    album.device_set("dev-1", {"style": "photo", "updated": 2000})
    album.device_set("dev-1", {"style": "clock", "updated": 1000})    # 更旧的改动不要
    assert album.device_settings("dev-1")["style"] == "photo"
    album.device_set("dev-1", {"style": "clock", "interval": 7, "source": "nope", "updated": 3000})
    s = album.device_settings("dev-1")
    assert s["style"] == "clock" and s["interval"] == 15 and s["source"] == "hub"


@needs_ffmpeg
def test_folder_scan_uses_subfolders_as_albums(tmp_path):
    root = tmp_path / "nas"
    (root / "旅行").mkdir(parents=True)
    (root / "@eaDir").mkdir()
    _jpeg(root / "旅行" / "sea.jpg", 640, 480)
    _jpeg(root / "top.jpg", 640, 480)
    _jpeg(root / "@eaDir" / "thumb.jpg", 640, 480)
    with pytest.raises(album.AlbumError):
        album.apply({"op": "folder", "path": "relative/path"})
    album._scan["running"] = True            # 不让 apply 起线程，下面直接跑
    album.apply({"op": "folder", "path": str(root)})
    album._scan["running"] = False
    album.scan_folder()
    v = album.view()
    assert v["folder"]["count"] == 2 and v["folder"]["error"] == ""
    assert [a["name"] for a in v["albums"]] == ["旅行"] and v["albums"][0]["count"] == 1
    os.remove(root / "top.jpg")
    album.scan_folder()
    assert album.view()["folder"]["count"] == 1


def test_collect_flag_round_trip():
    album.apply({"op": "collect", "device": "dev-2"})
    assert album.for_device("dev-2")["collect"] is True
    album.collected("dev-2")
    assert album.for_device("dev-2")["collect"] is False


def test_device_list_is_capped_but_keeps_on_this_day(monkeypatch):
    today = date(2026, 11, 15)
    d = album.load()
    otd_ts = int(datetime(2022, 11, 15, 12).timestamp())
    for i in range(album.DEVICE_MAX + 20):
        pid = f"{i:012x}"
        d["photos"][pid] = {"id": pid, "albums": [], "taken": otd_ts if i < 5 else 1000, "w": 1, "h": 1}
    album.save(d)
    got = album.for_device("dev-1", today=today)["photos"]
    assert len(got) == album.DEVICE_MAX
    assert sum(p["otd"] for p in got) == 5 and all(p["otd"] for p in got[:5])
