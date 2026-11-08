#!/usr/bin/env python3
"""
Input-idle screen blanker + ALS auto-brightness for CD-18781Y.
- Blanks after IDLE_TIMEOUT seconds of no input (writes brightness=BLANK_BRIGHTNESS)
- While screen is on: adjusts brightness from ALS every ALS_INTERVAL seconds
- Voice conversations count too: the voice client (thinksmart-client) writes HINT_PATH,
  {"hold": true, "seq": n} while a conversation is going (keep the screen on) and
  {"hold": false, "after": 300, "seq": n} when it ends (blank after that many seconds unless
  someone touches the screen, which brings back the normal IDLE_TIMEOUT). A new seq is
  activity and wakes the screen (a wake word, a report); "dim": true (the client's sleep
  hours) holds the backlight at DIM_BRIGHTNESS instead of following the light sensor.
  "brightness": 0-100 (optional) is the user's own level, set on the client's settings page or by
  voice: 0 = MANUAL_MIN (dim but readable), 100 = MAX_BRIGHTNESS; the light sensor is then left
  alone. Without it (older clients too) brightness is automatic. With both, dim wins unless the
  user's level is darker (the client drops dim when the user sets a level in the sleep hours).
  The screen comes back on (touch, motion, proximity, wake word) at the dim, user or auto level.
  "idle": seconds (optional) is the user's auto screen-off, used instead of IDLE_TIMEOUT for
  touch and conversations alike (the client sends the same value in "after"); 0 = never blank.
  Without it (older clients) IDLE_TIMEOUT and "after" work as before.

Deploy to /usr/local/bin/idle-blanker.py
Service: ~/.config/systemd/user/idle-blanker.service
"""

import os
import sys
import time
import glob
import math
import select
import signal
import logging
import subprocess
import json
from pathlib import Path

IDLE_TIMEOUT    = 1800     # seconds before blanking
POLL_INTERVAL   = 0.2      # a wake word must light the screen before the chime ends
HINT_PATH       = Path(os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000")) / "thinksmart-screen.json"
BACKLIGHT_PATH  = "/sys/class/backlight/backlight/brightness"
MAX_BRIGHTNESS  = 4095
MIN_BRIGHTNESS  = 3800     # never go below ~93% in auto mode (WLED minimum ~1200)
# Blanking value: must be non-zero on Plasma 6 — writing 0 causes KWin 6 to disable
# the DRM connector at hardware level, making the panel unrecoverable without restarting
# the compositor. WLED hardware shuts off below ~1200, so 10 is physically dark but
# KWin never sees 0 and leaves the DRM connector alive.
BLANK_BRIGHTNESS = 10
DIM_BRIGHTNESS = 1600      # sleep hours (WLED goes dark below ~1200)
MANUAL_MIN     = 1400      # the user's 0% (hint "brightness"); thinksmart-client mirrors it
ALS_PATH       = "/tmp/als_lux"
ALS_INTERVAL   = 10.0      # seconds between ALS adjustments
ALS_WAKE_COOL  = 30.0      # seconds after wake before ALS kicks in
ALS_DEADBAND   = 150       # only adjust if target differs by this much


# stdout goes to the journal (persistent, size-capped); a log file of its own grew without bound
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


def lux_to_brightness(lux):
    """Map ambient lux to backlight level (log scale)."""
    if lux <= 0:
        return MIN_BRIGHTNESS
    # log10(1)=0 → min, log10(3000)≈3.47 → max
    ratio = math.log10(max(1.0, lux)) / math.log10(3000.0)
    raw = MIN_BRIGHTNESS + int((MAX_BRIGHTNESS - MIN_BRIGHTNESS) * min(1.0, ratio))
    return max(MIN_BRIGHTNESS, min(MAX_BRIGHTNESS, raw))


def manual_brightness(percent):
    """The user's 0-100 as a backlight level, or None (automatic) for anything else."""
    if isinstance(percent, bool) or not isinstance(percent, (int, float)):
        return None
    return MANUAL_MIN + round((MAX_BRIGHTNESS - MANUAL_MIN) * max(0, min(100, percent)) / 100)


def read_als():
    try:
        txt = Path(ALS_PATH).read_text().strip()
        return float(txt)
    except Exception:
        return None


def get_brightness():
    try:
        return int(Path(BACKLIGHT_PATH).read_text().strip())
    except Exception:
        return None


def set_brightness(val):
    try:
        Path(BACKLIGHT_PATH).write_text(str(val) + "\n")
        return True
    except PermissionError:
        r = subprocess.run(["sudo", "tee", BACKLIGHT_PATH],
                           input=str(val) + "\n", text=True,
                           capture_output=True, timeout=2)
        return r.returncode == 0
    except Exception as e:
        logger.warning(f"set_brightness({val}) failed: {e}")
        return False


def read_hint(last_mtime):
    """The voice client's hint, if it changed since last_mtime: (mtime, hint dict or None)."""
    try:
        mtime = HINT_PATH.stat().st_mtime_ns
    except FileNotFoundError:
        return last_mtime, None
    if mtime == last_mtime:
        return last_mtime, None
    try:
        return mtime, json.loads(HINT_PATH.read_text())
    except Exception:
        return mtime, None


def open_input_devices():
    fds = []
    for path in glob.glob("/dev/input/event*"):
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            fds.append(fd)
        except Exception:
            pass
    return fds


def drain(fds, timeout=0):
    if not fds:
        time.sleep(timeout)
        return False
    r, _, _ = select.select(fds, [], [], timeout)
    if r:
        for fd in r:
            try:
                os.read(fd, 4096)
            except Exception:
                pass
        return True
    return False


def run():
    logger.info(f"Idle blanker started — timeout={IDLE_TIMEOUT}s, ALS interval={ALS_INTERVAL}s")
    fds = open_input_devices()
    logger.info(f"Watching {len(fds)} input device(s)")

    last_input    = time.monotonic()
    last_als      = 0.0
    last_wake     = 0.0
    blanked       = False
    target_brightness = MAX_BRIGHTNESS
    hint_mtime    = None
    hint_seq      = None
    hold          = False          # a voice conversation is going: never blank
    dim           = False          # the voice client's sleep hours
    manual        = None           # the user's backlight level (hint "brightness"), None: auto
    idle_timeout  = IDLE_TIMEOUT   # the user's screen-off (hint "idle"), 0: never
    timeout       = IDLE_TIMEOUT   # shortened after a conversation, back to normal on touch

    def handle_signal(sig, frame):
        logger.info("Signal — restoring brightness and exiting")
        if blanked:
            set_brightness(target_brightness)
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    def wake(why):
        nonlocal blanked, last_wake
        logger.info(f"{why} — restoring brightness")
        last_wake = time.monotonic()
        set_brightness(awake_brightness())
        blanked = False

    def awake_brightness():
        if manual is not None:
            level = manual
        else:
            try:
                level = int(open("/tmp/user_brightness").read())
            except Exception:
                level = MAX_BRIGHTNESS
        return min(DIM_BRIGHTNESS, level) if dim else level

    while True:
        had_input = drain(fds, POLL_INTERVAL)

        hint_mtime, hint = read_hint(hint_mtime)
        if hint is not None:
            new_dim, new_manual = bool(hint.get("dim")), manual_brightness(hint.get("brightness"))
            if (new_dim, new_manual) != (dim, manual):
                dim, manual = new_dim, new_manual
                logger.info(f"Voice client: dim={dim} brightness={manual or 'auto'}")
                if manual is None:
                    last_als = 0.0     # back to auto: follow the light sensor right away
                if not blanked:
                    set_brightness(awake_brightness())
            new_idle = hint.get("idle", IDLE_TIMEOUT)
            if isinstance(new_idle, bool) or not isinstance(new_idle, int) or new_idle < 0:
                new_idle = IDLE_TIMEOUT
            if new_idle != idle_timeout:
                idle_timeout = timeout = new_idle
                logger.info(f"Voice client: screen-off after {idle_timeout}s" if idle_timeout else "Voice client: screen-off never")
            if hint.get("seq") != hint_seq:
                hint_seq = hint.get("seq")
                last_input = time.monotonic()
                hold = bool(hint.get("hold"))
                timeout = idle_timeout if hold else int(hint.get("after", idle_timeout))
                logger.info(f"Voice client: hold={hold} timeout={timeout}s")
                if blanked:
                    wake("Voice client")

        if had_input:
            last_input = time.monotonic()
            timeout = idle_timeout
            if blanked:
                wake("Input detected")

        idle_secs  = time.monotonic() - last_input
        brightness = get_brightness()

        # External restore (e.g. proximity daemon)
        if blanked and brightness is not None and brightness > BLANK_BRIGHTNESS:
            logger.info("Screen restored externally — resetting idle timer")
            last_input = time.monotonic()
            last_wake  = time.monotonic()
            if dim or manual is not None:
                # screen-wake / proximity restore full brightness; sleep hours or the user's level win
                set_brightness(awake_brightness())
            else:
                try:
                    open("/tmp/user_brightness", "w").write(str(brightness))
                except Exception:
                    pass
            idle_secs = 0
            blanked   = False

        if not blanked:
            # ALS auto-brightness while screen is on (not in the sleep hours or at the user's level)
            now = time.monotonic()
            if not dim and manual is None and now - last_als >= ALS_INTERVAL and now - last_wake >= ALS_WAKE_COOL:
                last_als = now
                lux = read_als()
                if lux is not None:
                    new_target = lux_to_brightness(lux)
                    if abs(new_target - target_brightness) >= ALS_DEADBAND:
                        logger.info(f"ALS: {lux:.1f} lux → brightness {new_target} (was {target_brightness})")
                        target_brightness = new_target
                        if brightness is not None and brightness > 0:
                            set_brightness(target_brightness)
                            try:
                                open("/tmp/user_brightness", "w").write(str(target_brightness))
                            except Exception:
                                pass

            # Blank on idle (never during a voice conversation, nor with the user's screen-off off)
            if not hold and idle_timeout and idle_secs >= timeout:
                logger.info(f"Idle for {idle_secs:.1f}s — blanking screen")
                if set_brightness(BLANK_BRIGHTNESS):
                    logger.info("Screen blanked")
                    blanked = True


if __name__ == "__main__":
    run()
