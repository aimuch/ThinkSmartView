"""The device around the conversation: the screen and the hardware keys.

The system's screen blanker (thinksmart-ubuntu-image/screen/idle-blanker.py) owns the backlight; we only
tell it when a conversation keeps the screen on, the sleep hours, the user's brightness and screen-off (HINT_PATH). It already wakes on any input
device, the keys and the switch included; the wake word is the one thing it can't see. The volume keys and the mic-mute
switch are read straight from their input devices (the service binds /dev/input read-only).
"""
import array
import asyncio
import fcntl
import json
import logging
import os
import struct
import time
from pathlib import Path

from . import bg

log = logging.getLogger(__name__)

HINT_PATH = Path(os.environ.get('XDG_RUNTIME_DIR', '/run/user/1000')) / 'thinksmart-screen.json'
SCREEN_OFF_MINUTES = (1, 3, 5, 10, 30)   # the settings page's choices; 5 by default (config.py)
# the blanker's MANUAL_MIN (0%), MAX_BRIGHTNESS (100%) and DIM_BRIGHTNESS (sleep hours) on the 0-100 scale
DIM_LEVEL = round((1600 - 1400) * 100 / (4095 - 1400))

EV_KEY, EV_SW = 0x01, 0x05
KEY_VOLUMEDOWN, KEY_VOLUMEUP = 114, 115
SW_MIC = 4          # gpio-keys "Mic Mute" switch; the device tree reuses SW_MICROPHONE_INSERT: 1 = mic on
VOLUME_STEP = 5
EVENT = struct.Struct('qqHHi')   # struct input_event on 64-bit: timeval, type, code, value
EVIOCGSW = (2 << 30) | (8 << 16) | (ord('E') << 8) | 0x1b


def normalize_screen_off(setting, changes=None):
    """A complete, valid screen-off setting: {'enabled': bool, 'minutes': one of SCREEN_OFF_MINUTES}.
    Raises ValueError for other minutes."""
    s = {'enabled': True, 'minutes': 5}
    s.update({k: v for k, v in (setting or {}).items() if k in s})
    s.update({k: v for k, v in (changes or {}).items() if k in s and v is not None})
    s['enabled'] = bool(s['enabled'])
    if isinstance(s['minutes'], bool) or s['minutes'] not in SCREEN_OFF_MINUTES:
        raise ValueError(f'minutes must be one of {SCREEN_OFF_MINUTES}, got {s["minutes"]!r}')
    s['minutes'] = int(s['minutes'])
    return s


class Screen:
    """Hints for the blanker: hold() while a conversation goes on, release() when it ends, dim()
    for the sleep hours, brightness() for the user's level, blank_after() for the user's
    screen-off, photos() while a slideshow is open. A new seq is activity (wakes the screen); a
    setting change alone is not."""

    def __init__(self):
        self.held = None
        self.ringing = False         # an alarm rings: on until it stops, whatever the conversation does
        self.showing = False         # a photo slideshow is open (photos.py): on until it closes
        self.dimmed = False
        self.level = 100             # the user's 0-100
        self.sleep_level = None      # the sleep hours' brightness (0-100) while dimmed; None: the blanker's own dim level
        self.idle = 300              # the user's screen-off in seconds, 0: never
        self.dawn = None             # 温柔叫醒 (设计画布第 46 块): 0-100 while it brightens before an alarm, else None
        self._seq = time.time_ns()   # unique across client restarts

    def hold(self):
        if self.held is not True:
            self.held = True
            self._write(activity=True)

    def release(self):
        if self.held is not False:
            self.held = False
            self._write(activity=True)

    def alarm(self, on):
        """An alarm rings: light the screen (it may be blank) and keep it on; when it stops, blank after
        the user's screen-off."""
        if self.ringing != on:
            self.ringing = on
            self._write(activity=True)

    def photos(self, on):
        """A slideshow opens (light the screen, keep it on) or closes (blank after the user's screen-off)."""
        if self.showing != on:
            self.showing = on
            self._write(activity=True)

    def sunrise(self, level):
        """温柔叫醒: keep the screen on at this 0-100 level (the sleep hours' dimming gives way), None when it
        ends. Starting it lights the screen; the steps in between and the end are not activity."""
        if level != self.dawn:
            starting = self.dawn is None and level is not None
            self.dawn = level
            self._write(activity=starting)

    def poke(self):
        """Wake the screen now (a wake word, a report); it blanks after the user's screen-off."""
        self._write(activity=True)

    def dim(self, on):
        if self.dimmed != on:
            self.dimmed = on
            self._write(activity=False)

    def brightness(self, level):
        if self.level != level:
            self.level = level
            self._write(activity=False)

    def blank_after(self, seconds):
        if self.idle != seconds:
            self.idle = seconds
            self._write(activity=False)

    def _write(self, activity):
        if activity:
            self._seq += 1
        hint = {'hold': bool(self.held or self.ringing or self.showing), 'dim': self.dimmed, 'seq': self._seq,
                'brightness': self.level, 'idle': self.idle}
        if self.dimmed and self.sleep_level is not None:
            # the user's sleep brightness instead of the blanker's fixed dim level (设计画布第 29 块)
            hint['dim'], hint['brightness'] = False, self.sleep_level
        if self.dawn is not None:
            hint['hold'], hint['dim'], hint['brightness'] = True, False, self.dawn
        if not hint['hold'] and self.idle:
            hint['after'] = self.idle   # for a blanker older than "idle": the time after a conversation
        try:
            tmp = HINT_PATH.with_suffix('.tmp')
            tmp.write_text(json.dumps(hint))
            tmp.replace(HINT_PATH)
        except OSError as e:
            log.warning('screen hint not written: %s', e)


def input_device(name):
    """/dev/input/eventN of the input device called `name` (from /proc/bus/input/devices)."""
    try:
        blocks = Path('/proc/bus/input/devices').read_text().split('\n\n')
    except OSError:
        return None
    for block in blocks:
        if f'N: Name="{name}"' in block:
            for word in block.split():
                if word.startswith('event'):
                    return f'/dev/input/{word}'
    return None


class Keys:
    """Volume up (gpio-keys), volume down (pm8941_resin) and the mic-mute switch (gpio-keys).
    on_step(±VOLUME_STEP) is a coroutine: the client knows whether an alarm is ringing (the keys then
    change only that ring's volume)."""

    def __init__(self, on_step, on_mic):
        self.on_step = on_step
        self.on_mic = on_mic
        self.mic_on = True
        self._fds = []

    def start(self):
        loop = asyncio.get_running_loop()
        for name in ('gpio-keys', 'pm8941_resin'):
            path = input_device(name)
            if not path:
                log.warning('input device %s not found', name)
                continue
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError as e:
                log.warning('cannot open %s (%s): %s', path, name, e)
                continue
            self._fds.append(fd)
            loop.add_reader(fd, self._read, fd)
            if name == 'gpio-keys':
                self.mic_on = self._switch(fd, SW_MIC)
        log.info('keys: %d input devices, mic %s', len(self._fds), 'on' if self.mic_on else 'off')

    @staticmethod
    def _switch(fd, code):
        bits = array.array('B', [0] * 8)
        try:
            fcntl.ioctl(fd, EVIOCGSW, bits)
        except OSError:
            return True
        return bool(int.from_bytes(bytes(bits), 'little') >> code & 1)

    def _read(self, fd):
        try:
            data = os.read(fd, EVENT.size * 32)
        except BlockingIOError:
            return
        for i in range(0, len(data) - EVENT.size + 1, EVENT.size):
            _sec, _usec, etype, code, value = EVENT.unpack_from(data, i)
            if etype == EV_KEY and code in (KEY_VOLUMEUP, KEY_VOLUMEDOWN) and value in (1, 2):
                bg.spawn(self.on_step(VOLUME_STEP if code == KEY_VOLUMEUP else -VOLUME_STEP))
            elif etype == EV_SW and code == SW_MIC:
                self.mic_on = bool(value)
                self.on_mic(self.mic_on)
