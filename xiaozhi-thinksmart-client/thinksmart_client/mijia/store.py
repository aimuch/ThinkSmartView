"""Small JSON files under ~/.local/share/thinksmart-client/mijia/, written atomically (power cuts)."""
import json
import os
import pathlib

DIR = pathlib.Path.home() / '.local' / 'share' / 'thinksmart-client' / 'mijia'


def read_json(path: pathlib.Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def write_json(path: pathlib.Path, data, private=False):
    """tmp + fsync + rename + fsync dir, like alarms.write_atomic; private files are 0600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if private else 0o644)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    dfd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def remove(path: pathlib.Path):
    try:
        path.unlink()
    except FileNotFoundError:
        pass
