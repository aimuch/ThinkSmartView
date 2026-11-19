"""[xiaozhi-hub] core/utils/atomic_file：并发写同一个文件不会撞临时文件，写完不留 .tmp，权限照给的。"""
import json
import os
import threading

from core.utils.atomic_file import write_json


def test_concurrent_writers_never_collide(tmp_path):
    path = tmp_path / "d" / "x.json"
    errors = []

    def writer(i):
        try:
            for j in range(30):
                write_json(path, {"i": i, "j": j, "pad": "x" * 2000})
        except Exception as e:  # noqa: BLE001
            errors.append(e)
    threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert json.loads(path.read_text())["j"] == 29                   # always a whole file
    assert [p.name for p in path.parent.iterdir()] == ["x.json"]    # no temp files left


def test_mode(tmp_path):
    write_json(tmp_path / "s.json", {}, mode=0o600)
    assert os.stat(tmp_path / "s.json").st_mode & 0o777 == 0o600
