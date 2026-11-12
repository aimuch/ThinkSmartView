"""Photo slideshow on the screen, fed by programs on this device (POST /api/photos, web.py).

For example an assistant running on the device finds photos (a folder, a NAS photo library), resizes
them to the screen and posts them here, so the client never decodes a camera original (the Adreno 506
and 2 GB of memory choke on 50-megapixel JPEGs in WebKit). Any local program can use it: the page's
server only listens on 127.0.0.1.

- One show at a time; a new one replaces it. The images are kept on disk (spool_root()), not in
  memory, one directory per show named by a random id; the page loads /photos/<id>/<n>.
- Only JPEG, PNG and WebP (by their first bytes), at most MAX_IMAGES of MAX_IMAGE_BYTES each.
- The screen stays on while a show is open (device.Screen.photos); it closes itself after
  `minutes` (default 30), on the page's close button, or by voice (mcp self.screen.close_photos).
"""
import logging
import os
import secrets
import shutil
import time
from pathlib import Path

log = logging.getLogger(__name__)

MAX_IMAGES = 60
MAX_IMAGE_BYTES = 3 * 1024 * 1024      # the sender resizes to the screen first: ~200-400 KB each
MAX_TOTAL_BYTES = 80 * 1024 * 1024
INTERVAL_RANGE, DEFAULT_INTERVAL = (2, 120), 6
MINUTES_RANGE, DEFAULT_MINUTES = (1, 240), 30
TITLE_MAX = 60
TYPES = {'jpg': 'image/jpeg', 'png': 'image/png', 'webp': 'image/webp'}


def spool_root():
    base = os.environ.get('XDG_CACHE_HOME') or str(Path.home() / '.cache')
    return Path(base) / 'thinksmart-client' / 'photos'


def sniff(data):
    """'jpg', 'png' or 'webp' from the first bytes; None for anything else."""
    if data[:3] == b'\xff\xd8\xff':
        return 'jpg'
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return 'png'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'webp'
    return None


def _whole(value, name, lo, hi, default):
    if value is None or value == '':
        return default
    try:
        n = int(str(value).strip())
    except ValueError:
        raise ValueError(f'{name} must be a whole number from {lo} to {hi}, got {value!r}') from None
    if not lo <= n <= hi:
        raise ValueError(f'{name} must be from {lo} to {hi}, got {n}')
    return n


def normalize(title=None, interval_seconds=None, minutes=None):
    """The show's settings from the request's text fields; raises ValueError."""
    title = ' '.join(str(title or '').split())[:TITLE_MAX]
    return {'title': title,
            'interval': _whole(interval_seconds, 'interval_seconds', *INTERVAL_RANGE, DEFAULT_INTERVAL),
            'minutes': _whole(minutes, 'minutes', *MINUTES_RANGE, DEFAULT_MINUTES)}


class Show:
    def __init__(self, show_id, folder, files, title, interval, minutes, now=None):
        self.id, self.folder, self.files = show_id, folder, files
        self.title, self.interval, self.minutes = title, interval, minutes
        self.until = (now if now is not None else time.time()) + minutes * 60

    def view(self):
        """What the page gets (the 'slides' event, the snapshot's 'slides')."""
        return {'id': self.id, 'title': self.title, 'interval': self.interval, 'count': len(self.files),
                'until': round(self.until)}


class Upload:
    """One show being received: images go to disk as they arrive; commit() makes it the show."""

    def __init__(self, slides):
        self.slides = slides
        self.id = secrets.token_hex(8)
        self.folder = slides.root / self.id
        self.folder.mkdir(parents=True, exist_ok=False)
        self.files = []
        self.total = 0

    def add(self, data):
        if len(self.files) >= MAX_IMAGES:
            raise ValueError(f'at most {MAX_IMAGES} images')
        if not data:
            raise ValueError(f'image {len(self.files) + 1} is empty')
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError(f'image {len(self.files) + 1} is over {MAX_IMAGE_BYTES} bytes: resize it to the screen first')
        if self.total + len(data) > MAX_TOTAL_BYTES:
            raise ValueError(f'the images are over {MAX_TOTAL_BYTES} bytes together')
        kind = sniff(data)
        if kind is None:
            raise ValueError(f'image {len(self.files) + 1} is not a JPEG, PNG or WebP')
        name = f'{len(self.files)}.{kind}'
        (self.folder / name).write_bytes(data)
        self.files.append(name)
        self.total += len(data)

    def commit(self, title=None, interval_seconds=None, minutes=None, now=None):
        """Raises ValueError (bad settings, no images); the upload is then thrown away."""
        try:
            settings = normalize(title, interval_seconds, minutes)
            if not self.files:
                raise ValueError('no images')
        except ValueError:
            self.abort()
            raise
        show = Show(self.id, self.folder, list(self.files), now=now, **settings)
        self.slides._replace(show)
        return show

    def abort(self):
        shutil.rmtree(self.folder, ignore_errors=True)


class Slides:
    """The current show, and its images on disk."""

    def __init__(self, root):
        self.root = Path(root)
        self.current = None

    def clear_all(self):
        """At start: shows from a previous run are gone (the page has no way back to them)."""
        self.current = None
        shutil.rmtree(self.root, ignore_errors=True)

    def begin(self):
        self.root.mkdir(parents=True, exist_ok=True)
        return Upload(self)

    def _replace(self, show):
        old, self.current = self.current, show
        if old is not None:
            shutil.rmtree(old.folder, ignore_errors=True)
        log.info('photos: showing %d (%s), %d s each, for %d min', len(show.files), show.title or 'no title',
                 show.interval, show.minutes)

    def close(self):
        """True if a show was open."""
        old, self.current = self.current, None
        if old is None:
            return False
        shutil.rmtree(old.folder, ignore_errors=True)
        log.info('photos: closed')
        return True

    def file(self, show_id, n):
        """(path, content type) of image n of the open show, or None."""
        show = self.current
        if show is None or show_id != show.id:
            return None
        try:
            n = int(n)
        except (TypeError, ValueError):
            return None
        if not 0 <= n < len(show.files):
            return None
        name = show.files[n]
        return show.folder / name, TYPES[name.rsplit('.', 1)[1]]
