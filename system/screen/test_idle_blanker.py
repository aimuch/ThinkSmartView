"""idle-blanker.py's main loop against a fake backlight, hint file and touch input (a pipe).

  uv run --no-project --with pytest python -m pytest -q screen
"""
import importlib.util
import json
import os
import threading
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).with_name('idle-blanker.py')


@pytest.fixture
def blanker(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))      # its log file
    spec = importlib.util.spec_from_file_location(f'idle_blanker_{tmp_path.name}', SCRIPT)
    ib = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ib)
    backlight, hint = tmp_path / 'backlight', tmp_path / 'hint.json'
    backlight.write_text('4095\n')
    touch_r, touch_w = os.pipe()
    ib.BACKLIGHT_PATH, ib.HINT_PATH, ib.ALS_PATH = str(backlight), hint, str(tmp_path / 'als')
    ib.open_input_devices = lambda: [touch_r]
    ib.signal.signal = lambda *a: None
    threading.Thread(target=ib.run, daemon=True).start()   # left running: its files are this test's

    class Blanker:
        seq = 0

        def hint(self, activity=True, **fields):
            Blanker.seq += activity
            hint.write_text(json.dumps({'seq': Blanker.seq, **fields}))
            time.sleep(0.4)

        def touch(self):
            os.write(touch_w, b'x')
            time.sleep(0.4)

        DIM = ib.DIM_BRIGHTNESS

        def level(self):
            return int(backlight.read_text())

        def blanked_within(self, seconds):
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                if int(backlight.read_text()) == ib.BLANK_BRIGHTNESS:
                    return True
                time.sleep(0.1)
            return False

    return Blanker()


def test_the_users_screen_off_after_a_conversation_and_after_a_touch(blanker):
    blanker.hint(hold=False, idle=1, after=1)
    assert blanker.blanked_within(2)
    blanker.touch()                       # lights it again, and the user's time applies again
    assert not blanker.blanked_within(0.3)
    assert blanker.blanked_within(2)


def test_never_while_a_conversation_holds_the_screen(blanker):
    blanker.hint(hold=True, idle=1)
    assert not blanker.blanked_within(2)


def test_screen_off_turned_off_never_blanks(blanker):
    blanker.hint(hold=False, idle=0, after=1)   # "after" is for older blankers
    assert not blanker.blanked_within(2)
    blanker.touch()
    assert not blanker.blanked_within(1)


def test_changing_the_setting_alone_applies_it(blanker):
    blanker.hint(hold=False, idle=0)
    blanker.hint(activity=False, hold=False, idle=1, after=1)   # not activity: same seq
    assert blanker.blanked_within(2.5)


def test_an_older_client_keeps_after_and_the_long_touch_timeout(blanker):
    blanker.hint(hold=False, after=1)     # no "idle"
    assert blanker.blanked_within(2)
    blanker.touch()                       # back to IDLE_TIMEOUT (30 minutes)
    assert not blanker.blanked_within(2)


@pytest.mark.parametrize('bad', [True, -5, 'x', 1.5])
def test_a_bad_idle_is_the_normal_timeout(blanker, bad):
    blanker.hint(hold=False, idle=bad)
    blanker.touch()
    assert not blanker.blanked_within(1.5)


# The sleep hours ("dim") only lower the backlight while the screen is on; screen-off follows the
# user's setting there too (the user: 「我希望睡眠时段也遵循息屏设置」).

def test_sleep_hours_blank_after_the_users_time_and_wake_dimmed(blanker):
    blanker.hint(hold=False, dim=True, idle=1, after=1)
    assert blanker.level() == blanker.DIM
    assert blanker.blanked_within(2)
    blanker.touch()
    assert blanker.level() == blanker.DIM
    assert blanker.blanked_within(2)


def test_sleep_hours_with_screen_off_turned_off_stay_on_dimmed(blanker):
    blanker.hint(hold=False, dim=True, idle=0)
    blanker.touch()
    assert not blanker.blanked_within(2.5)
    assert blanker.level() == blanker.DIM


def test_sleep_hours_wait_for_a_longer_time(blanker):
    blanker.hint(hold=False, dim=True, idle=3, after=3)
    assert not blanker.blanked_within(2)
    assert blanker.blanked_within(2)
