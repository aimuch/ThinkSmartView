#!/usr/bin/env python3
"""
Standalone VCNL4200 Proximity Sensor Daemon
Monitors proximity sensor and triggers wake when display is off and hand approaches
Independent from vp5 - runs as separate systemd service

Each time the screen blanks the sensor is switched on and measured first (设计画布第 59 块): the
first reading after switching on is stale (0), then about a second of readings gives this unit's
"nobody here" level, and only a reading MARGIN above it (and above PROXIMITY_THRESHOLD) twice in a
row wakes the screen. Most units read 2-11 with nobody near; one read 85-180 (something in front of
the sensor, or the cover glass reflecting) and with the fixed threshold of 50 it lit the screen
again within a second of every blank. The level then follows slow drift while the screen stays dark.

The voice client can turn this off: "proximity": false in its screen hint (HINT_PATH, the file the
idle blanker reads). What was measured goes to STATUS_PATH for the client's settings page.
"""

import json
import os
import subprocess
import time
import sys
import signal
import logging

# Configuration
I2C_BUS = 0
I2C_ADDR = 0x51
PS_CONF1_REG = 0x03
PS_DATA_REG = 0x08
ALS_CONF_REG = 0x00

# PS_CONF1 (VCNL4200 layout: bits 7:6 PS_DUTY, 5:4 PS_PERS, 3:1 PS_IT, 0 PS_SD):
# 0xCA = PS_DUTY 1/1280, PS_PERS 1, PS_IT 9T, PS_SD=0 (active)
PS_CONF1_ENABLED = 0xCA
PS_CONF2_ENABLED = 0x08
PS_CONF1_DISABLED = 0xCB  # PS_SD bit set

# PS_MS register (0x04): high byte bits[2:0] = LED_I
# 000=50mA 001=75mA 010=100mA 011=120mA 100=140mA 101=160mA 110=180mA 111=200mA
# On this board LED_I is only the gate drive of an external FET whose resistor sets the
# IR LED current (datasheet Fig. 11); counts don't change with LED_I, only with PS_IT and pulses.
PS_MS_REG = 0x04
PS_LED_200MA = 0x0700  # low byte (PS_CONF3) = 0x00, high byte (PS_MS) = 0x07

# Thresholds
PROXIMITY_THRESHOLD = 50   # The lowest threshold: counts above this = object detected (ambient ~2-11, hand ~87-1044)
MARGIN = 70                # ...and this much above the unit's own "nobody here" level, measured at each blank
CALIBRATE_READS = 8        # readings (0.12 s apart, after a stale first one) for that level
DRIFT = 0.05               # how fast the level follows readings below the threshold while the screen stays dark
DEBOUNCE_COUNT = 2         # Require N consecutive readings above threshold before waking
WAKE_COOLDOWN = 10.0       # Seconds to wait before triggering wake again
POLLING_INTERVAL = 0.5    # Poll at 2 Hz
BACKLIGHT_PATH = "/sys/class/backlight/backlight/brightness"

# Blanking value must match idle-blanker.py BLANK_BRIGHTNESS.
# Proximity sensor is enabled when brightness <= this value (screen is blanked).
BLANK_BRIGHTNESS = 10

RUNTIME_DIR = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
HINT_PATH = os.path.join(RUNTIME_DIR, "thinksmart-screen.json")       # written by thinksmart-client
STATUS_PATH = os.path.join(RUNTIME_DIR, "thinksmart-proximity.json")  # read by thinksmart-client
STATE_PATH = os.path.join(RUNTIME_DIR, "thinksmart-screen-state.json") # written by idle-blanker (0.2.17)


def screen_blanked(brightness, path=STATE_PATH):
    """Is the screen off? idle-blanker says so in STATE_PATH (its screen-off level is the user's choice now,
    设计画布第 69 块); an older blanker didn't write it: then at or below BLANK_BRIGHTNESS."""
    try:
        with open(path) as f:
            return bool(json.load(f)["blanked"])
    except (OSError, ValueError, KeyError, TypeError):
        return brightness is not None and brightness <= BLANK_BRIGHTNESS


class Gate:
    """When a reading means someone came near: above max(PROXIMITY_THRESHOLD, nobody-here level + MARGIN),
    DEBOUNCE_COUNT times in a row. No hardware here, so it can be tested."""

    def __init__(self, floor=PROXIMITY_THRESHOLD, margin=MARGIN, debounce=DEBOUNCE_COUNT):
        self.floor, self.margin, self.debounce = floor, margin, debounce
        self.baseline = None
        self.above = 0

    @property
    def threshold(self):
        if self.baseline is None:            # nothing measured: the fixed threshold, as before
            return self.floor
        return max(self.floor, int(round(self.baseline + self.margin)))

    def calibrate(self, readings):
        """The nobody-here level from readings taken right after switching the sensor on: zeros (stale,
        failed reads) don't count. Returns it (None when nothing usable came back)."""
        good = [r for r in readings if r]
        self.baseline = max(good) if good else None
        self.above = 0
        return self.baseline

    def feed(self, prox):
        """True when this reading wakes the screen."""
        if prox > self.threshold:
            self.above += 1
            if self.above >= self.debounce:
                self.above = 0
                return True
            return False
        self.above = 0
        if prox and self.baseline is not None:
            self.baseline += (prox - self.baseline) * DRIFT
        elif prox and self.baseline is None:
            self.baseline = prox
        return False


def proximity_wanted(path=HINT_PATH):
    """The client's "走近亮屏" switch; on when the hint is missing or says nothing about it."""
    try:
        with open(path) as f:
            return json.load(f).get("proximity", True) is not False
    except (OSError, ValueError, AttributeError):
        return True


def write_status(status, path=STATUS_PATH):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(status, f)
        os.replace(tmp, path)
    except OSError as e:
        logger.warning(f"could not write {path}: {e}")


# Logging
# stdout goes to the journal (persistent, size-capped); a log file of its own grew without bound
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

class ProximitySensor:
    def __init__(self):
        self.running = False
        self.sensor_enabled = False
        self.last_brightness = None
        self._last_wake_time = 0.0     # time of last wake trigger (for cooldown)
        self.gate = Gate()
        self.status = {"enabled": True, "baseline": None, "threshold": None, "measured": None, "last_wake": None}
        self.bus = None                # smbus2.SMBus, opened on first use
        self.bus_failed = False
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

    def _signal_handler(self, sig, frame):
        logger.info("Shutdown signal received")
        self.stop()
        sys.exit(0)

    def _bus(self):
        """The I2C bus opened directly (user ubuntu is in the i2c group): reading through
        `sudo i2cget` about once a second filled the journal and auth.log with sudo records.
        None (and sudo below) when /dev/i2c-N isn't ours, e.g. before the group change takes effect."""
        if self.bus is None and not self.bus_failed:
            try:
                from smbus2 import SMBus
                self.bus = SMBus(I2C_BUS)
            except Exception as e:
                self.bus_failed = True
                logger.warning(f"direct I2C unavailable ({e}), using sudo i2cget/i2cset")
        return self.bus

    def _sudo(self, args):
        r = subprocess.run(['sudo', args[0], '-y', str(I2C_BUS), f'0x{I2C_ADDR:02x}', *args[1:]],
                           capture_output=True, text=True, timeout=2)
        if r.returncode != 0:
            logger.warning(f"{args[0]} failed (rc={r.returncode}): {r.stderr.strip()}")
        return r

    def i2c_get(self, reg):
        """Read word from i2c register"""
        try:
            bus = self._bus()
            if bus is not None:
                return bus.read_word_data(I2C_ADDR, reg)
            r = self._sudo(['i2cget', f'0x{reg:02x}', 'w'])
            return int(r.stdout.strip(), 16) if r.returncode == 0 else None
        except Exception as e:
            logger.warning(f"i2cget failed: {e}")
            return None

    def i2c_set(self, reg, val_low):
        """Write a single byte to an i2c register."""
        try:
            bus = self._bus()
            if bus is not None:
                bus.write_byte_data(I2C_ADDR, reg, val_low)
                return True
            return self._sudo(['i2cset', f'0x{reg:02x}', f'0x{val_low:02x}', 'b']).returncode == 0
        except Exception as e:
            logger.warning(f"i2cset failed: {e}")
            return False

    def i2c_set_word(self, reg, word_val):
        """Write a 16-bit word to an i2c register (little-endian: low byte first)."""
        try:
            bus = self._bus()
            if bus is not None:
                bus.write_word_data(I2C_ADDR, reg, word_val)
                return True
            return self._sudo(['i2cset', f'0x{reg:02x}', f'0x{word_val:04x}', 'w']).returncode == 0
        except Exception as e:
            logger.warning(f"i2cset word failed: {e}")
            return False

    def get_brightness(self):
        """Read current backlight brightness"""
        try:
            with open(BACKLIGHT_PATH) as f:
                return int(f.read().strip())
        except Exception as e:
            logger.warning(f"Failed to read brightness: {e}")
            return None

    def enable_sensor(self):
        """Enable proximity sensor at 200mA LED current."""
        if self.sensor_enabled:
            return
        logger.info("Enabling proximity sensor")
        # Set LED current to 200mA (PS_MS reg 0x04, high byte LED_I=7)
        self.i2c_set_word(PS_MS_REG, PS_LED_200MA)
        if self.i2c_set(PS_CONF1_REG, PS_CONF1_ENABLED):
            logger.info("PS_CONF1 enabled (0xCA), LED=200mA")
            self.sensor_enabled = True
        else:
            logger.warning("Failed to enable sensor")

    def disable_sensor(self):
        """Disable proximity sensor to save power"""
        if not self.sensor_enabled:
            return
        logger.info("Disabling proximity sensor (PS_SD=1)")
        if self.i2c_set(PS_CONF1_REG, PS_CONF1_DISABLED):
            logger.info("Sensor shut down")
            self.sensor_enabled = False
        else:
            logger.warning("Failed to disable sensor")

    def calibrate(self):
        """Measure this unit's nobody-here level right after switching the sensor on."""
        time.sleep(0.15)
        self.read_proximity()                      # stale: 0 right after switching on
        readings = []
        for _ in range(CALIBRATE_READS):
            time.sleep(0.12)
            v = self.read_proximity()
            if v is not None:
                readings.append(v)
        base = self.gate.calibrate(readings)
        self.status.update(baseline=None if base is None else int(base), threshold=self.gate.threshold,
                           measured=int(time.time()))
        write_status(self.status)
        logger.info(f"nobody-here level {base} (readings {readings}), wake above {self.gate.threshold}")

    def read_proximity(self):
        """Read proximity data"""
        val = self.i2c_get(PS_DATA_REG)
        if val is not None:
            return val & 0xFFFF
        return None

    def trigger_wake(self):
        """Wake the display by restoring backlight brightness directly."""
        now = time.time()
        if now - self._last_wake_time < WAKE_COOLDOWN:
            logger.debug(f"Wake cooldown active ({WAKE_COOLDOWN - (now - self._last_wake_time):.1f}s remaining)")
            return
        logger.warning("=== PROXIMITY DETECTED — WAKING SCREEN ===")
        self._last_wake_time = now
        self.status["last_wake"] = int(now)
        write_status(self.status)

        # Restore backlight via sudo tee.
        try:
            r = subprocess.run(
                ['sudo', 'tee', BACKLIGHT_PATH],
                input='4095\n', text=True,
                capture_output=True, timeout=3
            )
            if r.returncode == 0:
                logger.info("Screen woken via sudo tee backlight write")
            else:
                logger.warning(f"sudo tee backlight failed (rc={r.returncode}): {r.stderr.strip()}")
        except Exception as e:
            logger.warning(f"Backlight write failed: {e}")

    def run(self):
        """Main daemon loop"""
        logger.info("Proximity daemon started")
        self.running = True

        # Initial state
        brightness = self.get_brightness()
        logger.info(f"Initial brightness: {brightness}")
        self.last_brightness = brightness

        while self.running:
            try:
                # Check brightness
                brightness = self.get_brightness()

                if brightness is not None:
                    if self.last_brightness is None or brightness != self.last_brightness:
                        logger.info(f"Brightness changed: {self.last_brightness} -> {brightness}")
                        self.last_brightness = brightness

                    wanted = proximity_wanted()
                    if wanted != self.status["enabled"]:
                        logger.info(f"走近亮屏 {'on' if wanted else 'off'} (the client's setting)")
                        self.status["enabled"] = wanted
                        write_status(self.status)

                    # Screen is blanked (brightness at or below BLANK_BRIGHTNESS=10)
                    if screen_blanked(brightness) and wanted:
                        if not self.sensor_enabled:
                            self.enable_sensor()
                            self.calibrate()

                        # Read proximity
                        prox = self.read_proximity()
                        if prox is not None:
                            logger.debug(f"PS_DATA = 0x{prox:04x} ({prox})")
                            if self.gate.feed(prox):
                                logger.warning(f"PROXIMITY: {prox} counts (nobody-here level {self.gate.baseline:.0f}, "
                                               f"threshold {self.gate.threshold}) — triggering wake")
                                self.trigger_wake()

                    # Screen is ON (brightness > BLANK_BRIGHTNESS), or 走近亮屏 is off
                    else:
                        if self.sensor_enabled:
                            self.disable_sensor()

                time.sleep(POLLING_INTERVAL)

            except Exception as e:
                logger.error(f"Error in loop: {e}")
                time.sleep(1)

    def stop(self):
        """Shutdown gracefully"""
        self.running = False
        self.disable_sensor()
        logger.info("Daemon stopped")

if __name__ == '__main__':
    daemon = ProximitySensor()
    daemon.run()
