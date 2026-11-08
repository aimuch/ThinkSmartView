"""Screen rotation: the kiosk's Weston output transform (ROTATION in /etc/thinksmart-kiosk.conf).

The panel is natively 800x1280 portrait; the image ships ROTATION=90 (landscape). Changing it goes
through the system's `kiosk-mode rotation <v>` + `kiosk-mode restart` (root, via sudo -n like 关机):
the browser restarts and the page comes back in a few seconds. The client and the conversation
keep running meanwhile.

Auto: the bma253 accelerometer (iio) is read once a second; when gravity has pointed along one
axis for AUTO_STABLE seconds and that means another rotation, it is applied. Standing in the
stand (landscape) x is about +9.5 m/s² for ROTATION=90.
"""
import asyncio
import logging
import pathlib
import time

log = logging.getLogger(__name__)

CONF = pathlib.Path('/etc/thinksmart-kiosk.conf')
KIOSK_MODE = '/usr/local/bin/kiosk-mode'
ACCEL = pathlib.Path('/sys/bus/iio/devices/iio:device0')
CHOICES = {'90': '横屏', '270': '横屏（倒置）', 'normal': '竖屏', '180': '竖屏（倒置）'}
AUTO_STABLE = 3.0
GRAVITY_MIN = 6.0     # m/s² along the axis that decides
DOMINANCE = 1.6       # that axis must beat the other in-plane one by this much


def current():
    try:
        for line in CONF.read_text().splitlines():
            if line.startswith('ROTATION='):
                v = line.split('=', 1)[1].strip().strip('"')
                return v if v in CHOICES else 'normal'
    except OSError:
        pass
    return '90'


def orientation(x, y):
    """Rotation that keeps the picture upright for gravity (x, y) in m/s², or None when unclear
    (lying flat, moving, in between)."""
    ax, ay = abs(x), abs(y)
    if ax >= GRAVITY_MIN and ax >= ay * DOMINANCE:
        return '90' if x > 0 else '270'
    if ay >= GRAVITY_MIN and ay >= ax * DOMINANCE:
        return 'normal' if y < 0 else '180'
    return None


def read_accel():
    try:
        scale = float((ACCEL / 'in_accel_scale').read_text())
        x = int((ACCEL / 'in_accel_x_raw').read_text()) * scale
        y = int((ACCEL / 'in_accel_y_raw').read_text()) * scale
        return x, y
    except (OSError, ValueError):
        return None


async def _run(*args):
    p = await asyncio.create_subprocess_exec('sudo', '-n', KIOSK_MODE, *args, stdout=asyncio.subprocess.DEVNULL,
                                             stderr=asyncio.subprocess.PIPE)
    _, err = await p.communicate()
    if p.returncode != 0:
        raise RuntimeError(err.decode(errors='replace').strip() or f'exit {p.returncode}')


class Rotation:
    def __init__(self, auto, publish, save):
        self.auto = bool(auto)
        self.publish = publish
        self.save = save
        self.value = current()
        self.applying = False
        self._seen = None
        self._task = None

    def view(self):
        return {'value': self.value, 'auto': self.auto, 'choices': list(CHOICES.items()),   # pairs: keep the order
                'sensor': read_accel() is not None, 'applying': self.applying}

    def start(self):
        self._task = asyncio.create_task(self._auto_loop())

    async def set(self, value=None, auto=None):
        if auto is not None:
            self.auto = bool(auto)
            self.save(rotation_auto=self.auto)
            self._seen = None
        if value is not None:
            if value not in CHOICES:
                raise ValueError(f'rotation must be one of {", ".join(CHOICES)}')
            if auto is None and self.auto:
                self.auto = False      # picking one by hand turns auto off
                self.save(rotation_auto=False)
            await self.apply(value)
        self.publish('rotation', self.view())
        return self.view()

    async def apply(self, value):
        if value == self.value or self.applying:
            return
        self.applying = True
        self.publish('rotation', self.view())
        try:
            await _run('rotation', value)
            self.value = value
            log.info('screen rotation -> %s', value)
            await asyncio.sleep(0.5)       # let the page get its answer before the browser goes
            await _run('restart')
        except (OSError, RuntimeError) as e:
            log.error('cannot rotate the screen: %s', e)
            self.publish('toast', {'kind': 'error', 'text': '没能旋转屏幕'})
        finally:
            self.applying = False
            self.value = current()

    async def _auto_loop(self):
        while True:
            await asyncio.sleep(1)
            if not self.auto or self.applying:
                continue
            g = read_accel()
            want = orientation(*g) if g else None
            if want is None or want == self.value:
                self._seen = None
                continue
            if not self._seen or self._seen[0] != want:
                self._seen = (want, time.monotonic())
            elif time.monotonic() - self._seen[1] >= AUTO_STABLE:
                self._seen = None
                await self.apply(want)
