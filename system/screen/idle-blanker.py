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
- "blank": the backlight level while the screen is off (system 0.2.17, 设计画布第 69 块), BLANK_MIN to
  BLANK_MAX; without it BLANK_BRIGHTNESS. These panels still show the page at the darkest level, so the
  user picks how much. "preview": {"level", "until"} shows a level for a moment while the screen is on
  (the settings page's 「看一下」); afterwards the normal level comes back. Whether the screen is off and at
  which level goes to STATE_PATH, so the proximity and touch/motion wake daemons and the client don't
  have to guess it from the backlight (a brighter screen-off level would look "on" to them).
- THINKSMART_FREEZE_WEB=1 only (off by default since system 0.2.16): the backlight's darkest level still
  shows the page on these panels, and a stopped page left a clock that never moved, so the page keeps
  running and pauses its own animations while dark (thinksmart-client watches the backlight). When on:
  while the screen is dark the kiosk's page renderer (WebKitWebProcess) is stopped (SIGSTOP): it kept
  animating at ~30% CPU behind a dark panel. Every way back (touch, voice, proximity, motion, anyone
  else lighting the panel) continues it first (SIGCONT); so do startup (a blanker that died while it
  was stopped) and exit. The page notices the gap and reloads its state. THINKSMART_FREEZE_WEB=0 turns
  this off. (Idea from fubin0478/thinksmart-view, MIT.) It is stopped FREEZE_DELAY seconds after the
  backlight goes down, not at once.

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
BLANK_MIN      = 10       # the darkest screen-off level (the old fixed one)
BLANK_MAX      = 1300     # the brightest one: still below MANUAL_MIN, the user's 0%
STATE_PATH     = HINT_PATH.with_name("thinksmart-screen-state.json")   # {"blanked", "blank", "at"}
DIM_BRIGHTNESS = 1600      # sleep hours (WLED goes dark below ~1200)
MANUAL_MIN     = 1400      # the user's 0% (hint "brightness"); thinksmart-client mirrors it
ALS_PATH       = "/tmp/als_lux"
ALS_INTERVAL   = 10.0      # seconds between ALS adjustments
ALS_WAKE_COOL  = 30.0      # seconds after wake before ALS kicks in
ALS_DEADBAND   = 150       # only adjust if target differs by this much
FREEZE_WEB     = os.environ.get("THINKSMART_FREEZE_WEB", "0") == "1"   # 默认不暂停：息屏时时间和内容照样更新
WEB_COMM       = "WebKitWebProces"   # /proc/<pid>/comm keeps 15 characters
FREEZE_DELAY   = 3.0      # seconds between blanking and stopping the page (THINKSMART_FREEZE_WEB=1 only)
kill           = os.kill             # tests replace it


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


def blank_level(value):
    """The hint's "blank" as a backlight level; anything else is the old fixed BLANK_BRIGHTNESS."""
    if isinstance(value, bool) or not isinstance(value, int):
        return BLANK_BRIGHTNESS
    return max(BLANK_MIN, min(BLANK_MAX, value))


def write_state(blanked, level):
    """Tell the wake daemons and the client whether the screen is off (and at which level)."""
    try:
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps({"blanked": bool(blanked), "blank": level, "at": int(time.time())}))
        tmp.replace(STATE_PATH)
    except OSError as e:
        logger.warning(f"screen state not written: {e}")


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


def web_pids():
    """This user's page renderers (the kiosk's WebKitWebProcess)."""
    pids, uid = [], os.getuid()
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            if Path(f"/proc/{d}/comm").read_text().strip() == WEB_COMM and os.stat(f"/proc/{d}").st_uid == uid:
                pids.append(int(d))
        except OSError:
            pass
    return pids


def freeze_web(stop):
    """Stop (screen dark) or continue the page renderers; continuing one that runs is harmless."""
    if not FREEZE_WEB and stop:
        return
    done = 0
    for pid in web_pids():
        try:
            kill(pid, signal.SIGSTOP if stop else signal.SIGCONT)
            done += 1
        except OSError:
            pass
    if done:
        logger.info(f"Page renderer {'stopped' if stop else 'continued'} ({done} process{'es' if done > 1 else ''})")


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
    freeze_web(False)     # a blanker that died while the screen was dark left the page stopped
    write_state(False, BLANK_BRIGHTNESS)

    last_input    = time.monotonic()
    last_als      = 0.0
    last_wake     = 0.0
    blanked       = False
    blanked_at    = 0.0
    blank         = BLANK_BRIGHTNESS   # the user's screen-off level (hint "blank")
    preview       = None           # (level, until): the settings page shows a level for a moment
    previewing    = None           # the level shown now
    frozen        = False          # the page renderer is stopped
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
        freeze_web(False)
        if blanked:
            set_brightness(target_brightness)
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    def wake(why):
        nonlocal blanked, last_wake, frozen
        logger.info(f"{why} — restoring brightness")
        last_wake = time.monotonic()
        freeze_web(False)      # first: the page draws while the backlight comes up
        frozen = False
        set_brightness(awake_brightness())
        blanked = False
        write_state(False, blank)

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
            new_blank = blank_level(hint.get("blank"))
            if new_blank != blank:
                blank = new_blank
                logger.info(f"Voice client: screen-off level {blank}")
                if blanked:
                    set_brightness(blank)
                write_state(blanked, blank)
            p = hint.get("preview")
            preview = None
            if isinstance(p, dict) and isinstance(p.get("until"), (int, float)) and p["until"] > time.time():
                preview = (blank_level(p.get("level")), float(p["until"]))
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
        if blanked and brightness is not None and brightness > blank:
            logger.info("Screen restored externally — resetting idle timer")
            freeze_web(False)
            frozen = False
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
            write_state(False, blank)

        # the settings page's preview: that level for a moment, then the normal one again
        if not blanked:
            want = preview[0] if preview and time.time() < preview[1] else None
            if want != previewing:
                set_brightness(want if want is not None else awake_brightness())
                logger.info(f"Preview of the screen-off level {want}" if want is not None else "Preview over")
                previewing = want
                last_wake = time.monotonic()   # the light sensor waits a little after a preview too

        if not blanked and previewing is None:
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
                if set_brightness(blank):
                    logger.info(f"Screen blanked (level {blank})")
                    blanked = True
                    blanked_at = time.monotonic()
                    write_state(True, blank)

        # THINKSMART_FREEZE_WEB=1: stop the page a little after blanking
        if blanked and not frozen and time.monotonic() - blanked_at >= FREEZE_DELAY:
            freeze_web(True)
            frozen = True


if __name__ == "__main__":
    run()
