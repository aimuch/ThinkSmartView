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
    ib.STATE_PATH = tmp_path / 'state.json'
    ib.PROX_STATUS = tmp_path / 'prox.json'          # the proximity daemon's light readings (关灯熄屏)
    ib.DARK_SECONDS, ib.LIGHT_SECONDS = 0.6, 0.3
    ib.open_input_devices = lambda: [touch_r]
    ib.FREEZE_DELAY = float(os.environ.get('TEST_FREEZE_DELAY', '0.1'))
    ib.FREEZE_WEB = os.environ.get('TEST_FREEZE_WEB', '1') == '1'     # the stop/continue tests turn it on
    ib.signal.signal = lambda *a: None
    sent = []                                       # (pid, signal) to the page renderers
    ib.web_pids = lambda: [4242]
    ib.kill = lambda pid, sig: sent.append((pid, sig))
    threading.Thread(target=ib.run, daemon=True).start()   # left running: its files are this test's

    class Blanker:
        seq = 0
        signals = sent
        STOP, CONT = ib.signal.SIGSTOP, ib.signal.SIGCONT

        def hint(self, activity=True, **fields):
            Blanker.seq += activity
            hint.write_text(json.dumps({'seq': Blanker.seq, **fields}))
            time.sleep(0.4)

        def light(self, lux):
            (tmp_path / 'prox.json').write_text(json.dumps({'lux': lux, 'lux_at': time.time()}))

        def touch(self):
            os.write(touch_w, b'x')
            time.sleep(0.4)

        DIM = ib.DIM_BRIGHTNESS
        BLANK = ib.BLANK_BRIGHTNESS

        def state(self):
            return json.loads((tmp_path / 'state.json').read_text())

        def blanked_at(self, level, seconds):
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                if self.level() == level:
                    return True
                time.sleep(0.1)
            return False

        def level(self):
            # a plain file stands in for sysfs: the blanker's write_text truncates before it writes,
            # so a read in between sees '' (the real backlight file never does)
            while not (text := backlight.read_text().strip()):
                time.sleep(0.01)
            return int(text)

        def blanked_within(self, seconds):
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                if self.level() == ib.BLANK_BRIGHTNESS:
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


# The page renderer is stopped while the panel is dark and continued on every way back.

def test_page_stopped_while_dark_and_continued_on_wake(blanker):
    assert blanker.signals[:1] == [(4242, blanker.CONT)]        # startup continues a left-over stop
    blanker.hint(hold=False, idle=1, after=1)
    assert blanker.blanked_within(2)
    time.sleep(0.3)
    assert blanker.signals[-1] == (4242, blanker.STOP)
    blanker.touch()
    assert blanker.signals[-1] == (4242, blanker.CONT)
    assert blanker.level() > blanker.DIM


def test_page_continued_when_something_else_lights_the_panel(blanker, tmp_path):
    blanker.hint(hold=False, idle=1, after=1)
    assert blanker.blanked_within(2)
    time.sleep(0.3)
    (tmp_path / 'backlight').write_text('3000\n')                # the proximity daemon lit it
    time.sleep(0.5)
    assert blanker.signals[-1] == (4242, blanker.CONT)


def test_a_voice_wake_continues_the_page(blanker):
    blanker.hint(hold=False, idle=1, after=1)
    assert blanker.blanked_within(2)
    time.sleep(0.3)
    blanker.hint(hold=True)                                      # wake word: new seq
    assert blanker.signals[-1] == (4242, blanker.CONT)


def test_page_stopped_only_after_the_delay(tmp_path, monkeypatch, request):
    """THINKSMART_FREEZE_WEB=1: the page is stopped FREEZE_DELAY seconds after blanking, not at once."""
    monkeypatch.setenv('TEST_FREEZE_DELAY', '1.0')
    b = request.getfixturevalue('blanker')
    b.hint(hold=False, idle=1, after=1)
    assert b.blanked_within(2)
    assert (4242, b.STOP) not in b.signals
    time.sleep(1.2)
    assert b.signals[-1] == (4242, b.STOP)


def test_page_keeps_running_while_dark_by_default(monkeypatch, request):
    """默认不暂停网页：息屏时（背光最低还看得见）时间和内容照样更新。"""
    monkeypatch.setenv('TEST_FREEZE_WEB', '0')
    b = request.getfixturevalue('blanker')
    b.hint(hold=False, idle=1, after=1)
    assert b.blanked_within(2)
    time.sleep(0.4)
    assert (4242, b.STOP) not in b.signals


def test_the_users_screen_off_level(blanker, tmp_path):
    """熄屏后的亮度（设计画布第 69 块）：照 hint 的 blank；状态文件告诉别的程序屏幕是不是熄着。"""
    blanker.hint(hold=False, idle=1, after=1, blank=400)
    assert blanker.state()["blanked"] is False
    assert blanker.blanked_at(400, 2)
    time.sleep(0.3)
    assert blanker.state() == {"blanked": True, "blank": 400, "at": blanker.state()["at"]}
    blanker.hint(activity=False, hold=False, idle=1, after=1, blank=150)   # 熄着时改了：马上换
    assert blanker.level() == 150
    (tmp_path / 'backlight').write_text('3000\n')                        # 走近亮屏点亮了它
    time.sleep(0.5)
    assert blanker.state()["blanked"] is False


def test_out_of_range_levels_are_clamped(blanker):
    blanker.hint(hold=False, idle=1, after=1, blank=99999)
    assert blanker.blanked_at(1300, 2)
    blanker.touch()
    blanker.hint(hold=False, idle=1, after=1, blank="x")
    assert blanker.blanked_at(blanker.BLANK, 2)


def test_preview_shows_the_level_then_the_normal_one(blanker):
    blanker.hint(hold=False, idle=60, brightness=50)
    normal = blanker.level()
    blanker.hint(activity=False, hold=False, idle=60, brightness=50, blank=400, preview={"level": 400, "until": time.time() + 0.8})
    assert blanker.level() == 400 and blanker.state()["blanked"] is False
    time.sleep(1.0)
    assert blanker.level() == normal


def test_light_gate():
    """关灯熄屏（设计画布第 74 块）：暗够 DARK_SECONDS 才算关灯；明显亮了 LIGHT_SECONDS 才算开灯；闪一下不算。"""
    spec = importlib.util.spec_from_file_location('idle_blanker_gate', SCRIPT)
    ib = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ib)
    g = ib.LightGate(1.0)
    assert g.feed(0.5, 0) is None and g.feed(0.5, 30) is None and g.feed(0.5, 60) == "dark" and g.feed(0.4, 70) is None
    assert g.feed(2.5, 71) is None                     # 亮了一点，不够 3 lux：不算开灯
    assert g.feed(20, 80) is None and g.feed(0.5, 81) is None and g.feed(20, 82) is None   # 手机亮了一下又暗
    assert g.feed(20, 85) == "light" and g.dark is False
    assert g.feed(None, 90) is None and g.feed(0.5, 91) is None and g.feed(0.5, 120) is None   # 没读数：从头算


def test_room_goes_dark_blanks_and_light_wakes(blanker):
    blanker.light(30)
    blanker.hint(hold=False, idle=600, dark_lux=1.0)
    time.sleep(0.8)
    assert blanker.level() != blanker.BLANK                 # 屋里亮着：不熄
    for _ in range(12):
        blanker.light(0.4); time.sleep(0.2)
    assert blanker.blanked_within(1.5) and blanker.state()["blanked"] is True
    for _ in range(14):
        blanker.light(40); time.sleep(0.2)
    assert blanker.state()["blanked"] is False and blanker.level() > 1300


def test_dark_does_nothing_without_the_setting_or_during_a_conversation(blanker):
    blanker.hint(hold=True, idle=600, dark_lux=1.0)
    for _ in range(12):
        blanker.light(0.2); time.sleep(0.2)
    assert blanker.level() != blanker.BLANK
    blanker.hint(hold=False, idle=600)
    for _ in range(12):
        blanker.light(0.2); time.sleep(0.2)
    assert blanker.level() != blanker.BLANK
