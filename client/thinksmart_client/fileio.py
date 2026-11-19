"""Writing a file so that a power cut leaves either the old one or the new one, never half of each or an
empty one: the device has no battery and is unplugged without warning. Everything the client keeps on
disk (settings, alarms, notes, caches) goes through write_atomic."""
import os


def write_atomic(path, data):
    """Replace path (a pathlib.Path) with data (bytes or str)."""
    if isinstance(data, str):
        data = data.encode('utf-8')
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    with open(tmp, 'wb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    try:
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)   # the rename itself
        finally:
            os.close(fd)
    except OSError:
        pass
