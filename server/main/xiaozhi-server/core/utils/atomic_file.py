"""[xiaozhi-hub] 写数据文件：要么是旧的、要么是新的，不会半截、不会是空的。

以前各模块各写一份「写 xxx.tmp 再 os.replace」：临时文件名是固定的，两个线程（_blocking 跑在线程池里）同时写
同一个文件会打开同一个 .tmp、内容交错，后替换的那个找不到 .tmp 报 FileNotFoundError；也都没有 fsync，
机器断电后刚写的文件可能是空的。现在都调这里：临时文件名每次不同（mkstemp，同一目录），写完 fsync，
替换后再 fsync 目录。
"""
import json
import os
import tempfile


def write_bytes(path, data: bytes, mode: int = 0o644) -> None:
    path = os.fspath(path)
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix="." + os.path.basename(path) + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        dfd = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(dfd)   # 替换这件事本身也落盘
        finally:
            os.close(dfd)
    except OSError:
        pass


def write_text(path, text: str, mode: int = 0o644) -> None:
    write_bytes(path, text.encode("utf-8"), mode)


def write_json(path, data, mode: int = 0o644, **dump_kwargs) -> None:
    """json.dumps(data, **dump_kwargs) 写进 path；ensure_ascii 不写时按 json 的默认。"""
    write_text(path, json.dumps(data, **dump_kwargs), mode)
